using System.Text.Json;
using Microsoft.AspNetCore.Authorization;
using Microsoft.AspNetCore.Mvc;
using Microsoft.EntityFrameworkCore;
using System.Data;
using VisionI.API.Infrastructure;
using VisionI.API.Models.Responses;
using VisionI.API.Services;

namespace VisionI.API.Controllers;

[ApiController]
[Route("api/dashboard")]
[Authorize]
[Produces("application/json")]
public class DashboardController : ControllerBase
{
    private static readonly JsonSerializerOptions JsonOptions = new(JsonSerializerDefaults.Web);

    private readonly IIntelligenceService _intelligence;
    private readonly AppDbContext _db;

    public DashboardController(IIntelligenceService intelligence, AppDbContext db)
    {
        _intelligence = intelligence;
        _db = db;
    }

    [HttpGet("overview")]
    public async Task<ActionResult<DashboardOverviewResponse>> GetOverview(CancellationToken ct = default)
    {
        var isAdmin = User.IsInRole("Admin");

        // Both roles get cached: viewers at 30 s, admins at 15 s.
        // Without this, every admin request triggers a 10+ route fan-out to Python,
        // exhausting the two Uvicorn workers and causing 50s+ latency. The DB fast
        // path below is also cached — otherwise every live tick re-runs ~10 SQL
        // queries (incl. a CROSS JOIN LATERAL over all events) per circuit.
        var cacheKey = isAdmin ? "dashboard:overview:admin" : "dashboard:overview:viewer";
        var cacheTtl = isAdmin ? TimeSpan.FromSeconds(15) : TimeSpan.FromSeconds(30);

        var cached = await _intelligence.GetPrecomputedJsonAsync(cacheKey, ct);
        if (!string.IsNullOrEmpty(cached))
        {
            try
            {
                var cachedResponse = JsonSerializer.Deserialize<DashboardOverviewResponse>(cached, JsonOptions);
                if (cachedResponse is not null)
                    return Ok(cachedResponse);
            }
            catch { /* stale/corrupt cache — fall through to fresh fetch */ }
        }

        var dbResponse = await TryBuildDbDashboardOverviewAsync(isAdmin, ct);
        if (dbResponse is not null)
        {
            try
            {
                var json = JsonSerializer.Serialize(dbResponse, JsonOptions);
                await _intelligence.SetCachedJsonAsync(cacheKey, json, cacheTtl, ct);
            }
            catch { /* cache write failure is non-critical */ }
            return Ok(dbResponse);
        }

        // Hard 12-second ceiling on the fan-out. Any individual route that exceeds its share
        // returns null and gets an empty default. Without this a cold admin request holds open
        // all 10+ Python slots until the outer request times out (~50s+).
        using var fanoutCts = CancellationTokenSource.CreateLinkedTokenSource(ct);
        fanoutCts.CancelAfter(TimeSpan.FromSeconds(12));
        var safeCt = fanoutCts.Token;
        async Task<JsonDocument?> SafeDoc(string path)
        {
            try { return await _intelligence.GetPythonDocumentAsync(path, safeCt); }
            catch { return null; }
        }

        var eventsTask       = SafeDoc("/events?limit=50");
        var overviewTask     = SafeDoc("/overview");
        var situationsTask   = SafeDoc("/ontology/overview?limit=16");
        var liveTask         = SafeDoc("/streams/live?limit=20");
        var entitiesTask     = SafeDoc("/entities?limit=40");
        var alertSummaryTask = SafeDoc("/alerts/summary");
        var alertsTask       = SafeDoc("/alerts?limit=10&acknowledged=false");
        var narrativesTask   = SafeDoc("/narratives/summary");
        var timelineTask     = SafeDoc("/sentiment/timeline?bucket=hour");
        var swarmTask        = SafeDoc("/agents");
        var pythonHealthTask = SafeDoc("/health");
        var liveCacheTask    = _intelligence.GetPrecomputedJsonAsync("precomputed:live_streams", safeCt)
            .ContinueWith(t => t.IsCompletedSuccessfully ? t.Result : null, TaskContinuationOptions.None);
        var dashboardSummaryTask = GetJsonFromCacheAsync(
            "precomputed:dashboard_summary",
            () => Task.FromResult<string?>(null),
            safeCt);
        var confidenceTask   = GetJsonFromCacheAsync(
            "precomputed:confidence_distribution",
            () => Task.FromResult<string?>("{\"high\":0,\"medium\":0,\"low\":0,\"unscored\":0,\"total\":0}"),
            safeCt);
        var correlationTask  = GetJsonFromCacheAsync(
            "precomputed:correlation_summary",
            () => _intelligence.GetPythonJsonAsync("/admin/signals/stats", safeCt),
            safeCt);
        var statsTask = isAdmin
            ? SafeDoc("/admin/stats")
            : Task.FromResult<JsonDocument?>(null);
        var jobsTask = isAdmin
            ? SafeDoc("/admin/jobs?limit=5")
            : Task.FromResult<JsonDocument?>(null);
        // jarvisTask is a bare Redis call — not wrapped by SafeDoc, so guard it explicitly.
        var jarvisTask = _intelligence.GetPrecomputedJsonAsync("precomputed:jarvis_insight", safeCt)
            .ContinueWith(t => t.IsCompletedSuccessfully ? t.Result : null, TaskContinuationOptions.None);

        // WhenAll itself can throw if confidenceTask/correlationTask (bare Redis calls) fault.
        try
        {
            await Task.WhenAll(
                eventsTask, situationsTask, liveTask, entitiesTask, alertSummaryTask, alertsTask,
                narrativesTask, timelineTask, swarmTask, pythonHealthTask, overviewTask,
                dashboardSummaryTask, confidenceTask, correlationTask, statsTask, jobsTask, jarvisTask);
        }
        catch { /* individual task exceptions are handled by SafeDoc and the try-catches inside GetJsonFromCacheAsync; swallow any stragglers */ }

        var dbAvailable = false;
        try { dbAvailable = await _intelligence.CanConnectDbAsync(ct); } catch { }
        var overviewElement = CloneOrDefault(overviewTask.Result, new
        {
            total_events = 0,
            alert_count = 0,
            narrative_count = 0,
            asset_count = 0,
            top_events = Array.Empty<object>(),
        });
        var rawEventsElement = CloneOrDefault(eventsTask.Result, new { total = 0, limit = 200, offset = 0, events = Array.Empty<object>() });
        var rawLiveEventsElement = CloneOrDefault(liveTask.Result, new { total = 0, events = Array.Empty<object>() });
        var cachedLiveEventsElement = ParseArray(liveCacheTask.IsCompletedSuccessfully ? liveCacheTask.Result : null);
        var liveEventsElement = HasItems(rawLiveEventsElement, "events")
            ? ExtractArray(rawLiveEventsElement, "events", Array.Empty<object>())
            : cachedLiveEventsElement;
        var overviewEventsElement = ExtractArray(overviewElement, "top_events", Array.Empty<object>());
        var eventsElement = HasItems(rawEventsElement, "events")
            ? ExtractArray(rawEventsElement, "events", Array.Empty<object>())
            : (HasItems(rawLiveEventsElement, "events") ? liveEventsElement : overviewEventsElement);

        var response = new DashboardOverviewResponse
        {
            IsAdminView = isAdmin,
            GeneratedAt = DateTime.UtcNow.ToString("O"),
            Situations = CloneOrDefault(situationsTask.Result, new { total = 0, situations = Array.Empty<object>() }),
            Events = eventsElement,
            LiveEvents = liveEventsElement,
            Entities = CloneOrDefault(entitiesTask.Result, new { total = 0, entities = Array.Empty<object>() }),
            Stats = isAdmin && statsTask.Result is not null
                ? statsTask.Result.RootElement.Clone()
                : BuildStatsFallback(eventsElement, overviewElement, dashboardSummaryTask.Result),
            Health = BuildHealth(dbAvailable, pythonHealthTask.Result),
            AlertSummary = CloneOrDefault(alertSummaryTask.Result, new { unacknowledged = 0, by_severity = new Dictionary<string, int>() }),
            RecentAlerts = ExtractArray(
                CloneOrDefault(alertsTask.Result, new { total = 0, alerts = Array.Empty<object>() }),
                "alerts",
                Array.Empty<object>()),
            NarrativeSummary = CloneOrDefault(narrativesTask.Result, new { total = 0, by_type = new Dictionary<string, int>(), by_severity = new Dictionary<string, int>() }),
            SentimentTimeline = CloneOrDefault(timelineTask.Result, new { data = Array.Empty<object>(), bucket_size = "day" }),
            Jobs = CloneOrDefault(jobsTask.Result, new { total = 0, jobs = Array.Empty<object>() }),
            Swarm = CloneOrDefault(swarmTask.Result, new { total = 0, agents = Array.Empty<object>() }),
            ConfidenceDistribution = confidenceTask.IsCompletedSuccessfully ? confidenceTask.Result : JsonDocument.Parse("{}").RootElement.Clone(),
            CorrelationSummary = correlationTask.IsCompletedSuccessfully ? correlationTask.Result : JsonDocument.Parse("{}").RootElement.Clone(),
            JarvisInsight = jarvisTask.IsCompletedSuccessfully ? jarvisTask.Result : null,
        };

        try
        {
            var json = JsonSerializer.Serialize(response, JsonOptions);
            await _intelligence.SetCachedJsonAsync(cacheKey, json, cacheTtl, ct);
        }
        catch { /* cache write failure is non-critical */ }

        return Ok(response);
    }

    private async Task<JsonElement> GetJsonFromCacheAsync(
        string key,
        Func<Task<string?>> fallbackFactory,
        CancellationToken ct)
    {
        try
        {
            var json = await _intelligence.GetPrecomputedJsonAsync(key, ct) ?? await fallbackFactory();
            if (!string.IsNullOrWhiteSpace(json))
                return JsonDocument.Parse(json).RootElement.Clone();
        }
        catch { /* Redis or Python unavailable — return empty element */ }
        return JsonDocument.Parse("{}").RootElement.Clone();
    }

    private static JsonElement CloneOrDefault(JsonDocument? doc, object fallback)
        => doc?.RootElement.Clone() ?? JsonSerializer.SerializeToElement(fallback, JsonOptions);

    private static bool HasItems(JsonElement payload, string propertyName)
        => payload.ValueKind == JsonValueKind.Object
           && payload.TryGetProperty(propertyName, out var items)
           && items.ValueKind == JsonValueKind.Array
           && items.GetArrayLength() > 0;

    private static JsonElement ExtractArray(JsonElement payload, string propertyName, object fallback)
    {
        if (payload.ValueKind == JsonValueKind.Object &&
            payload.TryGetProperty(propertyName, out var items) &&
            items.ValueKind == JsonValueKind.Array)
        {
            return items.Clone();
        }

        return JsonSerializer.SerializeToElement(fallback, JsonOptions);
    }

    private static JsonElement ParseArray(string? json)
    {
        if (string.IsNullOrWhiteSpace(json))
            return JsonSerializer.SerializeToElement(Array.Empty<object>(), JsonOptions);

        try
        {
            using var doc = JsonDocument.Parse(json);
            if (doc.RootElement.ValueKind == JsonValueKind.Array)
                return doc.RootElement.Clone();
        }
        catch { }

        return JsonSerializer.SerializeToElement(Array.Empty<object>(), JsonOptions);
    }

    private static JsonElement BuildHealth(bool dbAvailable, JsonDocument? pythonHealth)
    {
        var pythonStatus = pythonHealth?.RootElement.TryGetProperty("status", out var statusProp) == true
            ? statusProp.GetString()
            : null;

        return JsonSerializer.SerializeToElement(new
        {
            status = dbAvailable && (
                string.Equals(pythonStatus, "healthy", StringComparison.OrdinalIgnoreCase) ||
                string.Equals(pythonStatus, "ok", StringComparison.OrdinalIgnoreCase))
                ? "ok"
                : "degraded",
            db_available = dbAvailable,
            python = new
            {
                status = pythonStatus ?? "unknown"
            },
            timestamp = DateTime.UtcNow.ToString("O"),
        }, JsonOptions);
    }

    private static JsonElement BuildStatsFallback(
        JsonElement eventsArray,
        JsonElement overviewElement,
        JsonElement dashboardSummary)
    {
        if (dashboardSummary.ValueKind == JsonValueKind.Object &&
            dashboardSummary.TryGetProperty("total_events", out _))
        {
            return dashboardSummary.Clone();
        }

        var bySource = new Dictionary<string, int>(StringComparer.OrdinalIgnoreCase);
        var byType = new Dictionary<string, int>(StringComparer.OrdinalIgnoreCase);
        var total = 0;

        if (eventsArray.ValueKind == JsonValueKind.Array)
        {
            foreach (var item in eventsArray.EnumerateArray())
            {
                total++;

                if (item.TryGetProperty("source", out var sourceProp) && sourceProp.GetString() is { Length: > 0 } source)
                    bySource[source] = bySource.GetValueOrDefault(source) + 1;

                if (item.TryGetProperty("event_type", out var typeProp) && typeProp.GetString() is { Length: > 0 } eventType)
                    byType[eventType] = byType.GetValueOrDefault(eventType) + 1;
                }
            }

        if (total == 0 &&
            overviewElement.ValueKind == JsonValueKind.Object &&
            overviewElement.TryGetProperty("total_events", out var totalEventsProp) &&
            totalEventsProp.TryGetInt32(out var overviewTotal))
        {
            total = overviewTotal;
        }

        return JsonSerializer.SerializeToElement(new
        {
            total_events = total,
            by_source = bySource,
            by_type = byType,
            generated_at = DateTime.UtcNow.ToString("O"),
        }, JsonOptions);
    }

    private async Task<DashboardOverviewResponse?> TryBuildDbDashboardOverviewAsync(bool isAdmin, CancellationToken ct)
    {
        try
        {
            var conn = _db.Database.GetDbConnection();
            if (conn.State != ConnectionState.Open)
                await conn.OpenAsync(ct);

            var events = await ReadDashboardEventsAsync(conn, ct);
            var recentAlerts = await ReadRecentAlertsAsync(conn, ct);
            var totalEvents = await CountAsync(conn, "SELECT COUNT(*) FROM events", ct);
            var activeAlerts = await CountAsync(conn, "SELECT COUNT(*) FROM alerts WHERE acknowledged = FALSE AND resolved_at IS NULL", ct);
            var totalNarratives = await CountAsync(conn, "SELECT COUNT(*) FROM narratives WHERE status = 'active'", ct);
            var bySource = await GroupCountAsync(conn, "source", "events", "source IS NOT NULL AND source <> ''", ct);
            var byType = await GroupCountAsync(conn, "event_type", "events", "event_type IS NOT NULL AND event_type <> ''", ct);
            var alertsBySeverity = await GroupCountAsync(conn, "severity", "alerts", "acknowledged = FALSE AND resolved_at IS NULL", ct);
            var narrativesBySeverity = await GroupCountAsync(conn, "severity", "narratives", "status = 'active'", ct);
            var narrativesByType = await GroupCountAsync(conn, "signal_type", "narratives", "status = 'active'", ct);
            var entities = await ReadTopEntitiesAsync(conn, ct);

            var generatedAt = DateTime.UtcNow.ToString("O");
            return new DashboardOverviewResponse
            {
                IsAdminView = isAdmin,
                GeneratedAt = generatedAt,
                Situations = JsonSerializer.SerializeToElement(new { generated_at = generatedAt, total = 0, situations = Array.Empty<object>() }, JsonOptions),
                Events = JsonSerializer.SerializeToElement(events, JsonOptions),
                LiveEvents = JsonSerializer.SerializeToElement(events.Take(20), JsonOptions),
                Entities = JsonSerializer.SerializeToElement(new { total = entities.Count, entities }, JsonOptions),
                Stats = JsonSerializer.SerializeToElement(new { total_events = totalEvents, by_source = bySource, by_type = byType, generated_at = generatedAt }, JsonOptions),
                Health = JsonSerializer.SerializeToElement(new { status = "ok", db_available = true, python = new { status = "bypassed" }, timestamp = generatedAt }, JsonOptions),
                AlertSummary = JsonSerializer.SerializeToElement(new { unacknowledged = activeAlerts, by_severity = alertsBySeverity }, JsonOptions),
                RecentAlerts = JsonSerializer.SerializeToElement(recentAlerts, JsonOptions),
                NarrativeSummary = JsonSerializer.SerializeToElement(new { total = totalNarratives, by_type = narrativesByType, by_severity = narrativesBySeverity }, JsonOptions),
                SentimentTimeline = JsonSerializer.SerializeToElement(new { data = Array.Empty<object>(), bucket_size = "hour" }, JsonOptions),
                Jobs = JsonSerializer.SerializeToElement(new { total = 0, jobs = Array.Empty<object>() }, JsonOptions),
                Swarm = JsonSerializer.SerializeToElement(new { total = 0, agents = Array.Empty<object>() }, JsonOptions),
                ConfidenceDistribution = JsonSerializer.SerializeToElement(new { high = 0, medium = 0, low = 0, unscored = 0, total = totalEvents }, JsonOptions),
                CorrelationSummary = JsonSerializer.SerializeToElement(new { }, JsonOptions),
                JarvisInsight = events.Count > 0
                    ? $"Live DB fast path active. {events.Count} recent events are available without Python fan-out."
                    : "Live DB fast path active. Waiting for new events.",
            };
        }
        catch
        {
            return null;
        }
    }

    private static async Task<List<object>> ReadDashboardEventsAsync(System.Data.Common.DbConnection conn, CancellationToken ct)
    {
        await using var cmd = conn.CreateCommand();
        cmd.CommandText = """
            SELECT event_id, source, source_id, event_type, title, description, body, url,
                   language, author, timestamp, ingest_time, sentiment_label, sentiment_score,
                   location_lat, location_lon, location_name, actors, tags,
                   confidence_score, influence_score, risk_score, signal_count, reasoning
            FROM events
            ORDER BY timestamp DESC NULLS LAST, ingest_time DESC NULLS LAST
            LIMIT 50
            """;

        var rows = new List<object>();
        await using var reader = await cmd.ExecuteReaderAsync(ct);
        while (await reader.ReadAsync(ct))
        {
            rows.Add(new
            {
                event_id = reader.IsDBNull(0) ? "" : reader.GetString(0),
                source = reader.IsDBNull(1) ? null : reader.GetString(1),
                source_id = reader.IsDBNull(2) ? null : reader.GetString(2),
                event_type = reader.IsDBNull(3) ? null : reader.GetString(3),
                title = reader.IsDBNull(4) ? "--" : reader.GetString(4),
                description = reader.IsDBNull(5) ? null : reader.GetString(5),
                body = reader.IsDBNull(6) ? null : reader.GetString(6),
                url = reader.IsDBNull(7) ? null : reader.GetString(7),
                language = reader.IsDBNull(8) ? "en" : reader.GetString(8),
                author = reader.IsDBNull(9) ? null : reader.GetString(9),
                timestamp = reader.IsDBNull(10) ? null : reader.GetDateTime(10).ToString("O"),
                ingest_time = reader.IsDBNull(11) ? null : reader.GetDateTime(11).ToString("O"),
                sentiment = BuildNullableSentiment(reader),
                location = BuildNullableLocation(reader),
                actors = ParseJsonArray(reader, 17),
                tags = ParseJsonArray(reader, 18),
                confidence_score = ReadNullableDouble(reader, 19),
                influence_score = ReadNullableDouble(reader, 20),
                risk_score = ReadNullableDouble(reader, 21),
                signal_count = reader.IsDBNull(22) ? 0 : reader.GetInt32(22),
                reasoning = reader.IsDBNull(23) ? null : reader.GetString(23),
            });
        }

        return rows;
    }

    private static async Task<List<object>> ReadRecentAlertsAsync(System.Data.Common.DbConnection conn, CancellationToken ct)
    {
        await using var cmd = conn.CreateCommand();
        cmd.CommandText = """
            SELECT alert_id, alert_type, severity, title, description, entity, entity_type,
                   event_count, baseline, z_score, sources, location, detected_at,
                   resolved_at, acknowledged
            FROM alerts
            WHERE acknowledged = FALSE AND resolved_at IS NULL
            ORDER BY detected_at DESC NULLS LAST
            LIMIT 10
            """;

        var rows = new List<object>();
        await using var reader = await cmd.ExecuteReaderAsync(ct);
        while (await reader.ReadAsync(ct))
        {
            rows.Add(new
            {
                alert_id = reader.IsDBNull(0) ? null : reader.GetString(0),
                alert_type = reader.IsDBNull(1) ? "" : reader.GetString(1),
                severity = reader.IsDBNull(2) ? "medium" : reader.GetString(2),
                title = reader.IsDBNull(3) ? "--" : reader.GetString(3),
                description = reader.IsDBNull(4) ? null : reader.GetString(4),
                entity = reader.IsDBNull(5) ? null : reader.GetString(5),
                entity_type = reader.IsDBNull(6) ? null : reader.GetString(6),
                event_count = reader.IsDBNull(7) ? 0 : reader.GetInt32(7),
                baseline = reader.IsDBNull(8) ? 0 : reader.GetDouble(8),
                z_score = reader.IsDBNull(9) ? 0 : reader.GetDouble(9),
                sources = ParseJsonArray(reader, 10),
                location = reader.IsDBNull(11) ? null : reader.GetString(11),
                detected_at = reader.IsDBNull(12) ? null : reader.GetDateTime(12).ToString("O"),
                resolved_at = reader.IsDBNull(13) ? null : reader.GetDateTime(13).ToString("O"),
                acknowledged = !reader.IsDBNull(14) && reader.GetBoolean(14),
            });
        }

        return rows;
    }

    private static async Task<List<object>> ReadTopEntitiesAsync(System.Data.Common.DbConnection conn, CancellationToken ct)
    {
        await using var cmd = conn.CreateCommand();
        cmd.CommandText = """
            SELECT key, COUNT(*) AS mentions
            FROM events e
            CROSS JOIN LATERAL jsonb_array_elements_text(COALESCE(e.tags::jsonb, '[]'::jsonb)) AS key
            WHERE key <> ''
            GROUP BY key
            ORDER BY mentions DESC
            LIMIT 20
            """;

        var rows = new List<object>();
        try
        {
            await using var reader = await cmd.ExecuteReaderAsync(ct);
            while (await reader.ReadAsync(ct))
            {
                rows.Add(new
                {
                    id = reader.GetString(0),
                    name = reader.GetString(0),
                    type = "tag",
                    mention_count = Convert.ToInt32(reader.GetValue(1)),
                    last_seen = DateTime.UtcNow.ToString("O"),
                });
            }
        }
        catch
        {
            return new List<object>();
        }

        return rows;
    }

    private static async Task<int> CountAsync(System.Data.Common.DbConnection conn, string sql, CancellationToken ct)
    {
        await using var cmd = conn.CreateCommand();
        cmd.CommandText = sql;
        var value = await cmd.ExecuteScalarAsync(ct);
        return value is null or DBNull ? 0 : Convert.ToInt32(value);
    }

    private static async Task<Dictionary<string, int>> GroupCountAsync(
        System.Data.Common.DbConnection conn,
        string column,
        string table,
        string where,
        CancellationToken ct)
    {
        await using var cmd = conn.CreateCommand();
        cmd.CommandText = $"""
            SELECT {column}, COUNT(*)
            FROM {table}
            WHERE {where}
            GROUP BY {column}
            ORDER BY COUNT(*) DESC
            LIMIT 20
            """;

        var result = new Dictionary<string, int>(StringComparer.OrdinalIgnoreCase);
        await using var reader = await cmd.ExecuteReaderAsync(ct);
        while (await reader.ReadAsync(ct))
        {
            if (!reader.IsDBNull(0))
                result[reader.GetString(0)] = Convert.ToInt32(reader.GetInt64(1));
        }
        return result;
    }

    private static double? ReadNullableDouble(IDataRecord reader, int ordinal)
        => reader.IsDBNull(ordinal) ? null : reader.GetDouble(ordinal);

    private static JsonElement ParseJsonArray(IDataRecord reader, int ordinal)
    {
        if (reader.IsDBNull(ordinal))
            return JsonSerializer.SerializeToElement(Array.Empty<object>(), JsonOptions);

        var raw = reader.GetString(ordinal);
        try { return JsonDocument.Parse(string.IsNullOrWhiteSpace(raw) ? "[]" : raw).RootElement.Clone(); }
        catch { return JsonSerializer.SerializeToElement(Array.Empty<object>(), JsonOptions); }
    }

    private static object? BuildNullableSentiment(IDataRecord reader)
    {
        var hasLabel = !reader.IsDBNull(12);
        var hasScore = !reader.IsDBNull(13);
        if (!hasLabel && !hasScore)
            return null;

        return new
        {
            label = hasLabel ? reader.GetString(12) : null,
            score = hasScore ? reader.GetDouble(13) : (double?)null
        };
    }

    private static object? BuildNullableLocation(IDataRecord reader)
    {
        var hasLat = !reader.IsDBNull(14);
        var hasLon = !reader.IsDBNull(15);
        var hasName = !reader.IsDBNull(16);
        if (!hasLat && !hasLon && !hasName)
            return null;

        return new
        {
            lat = hasLat ? reader.GetDouble(14) : (double?)null,
            lon = hasLon ? reader.GetDouble(15) : (double?)null,
            name = hasName ? reader.GetString(16) : null
        };
    }
}
