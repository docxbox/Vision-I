using System.ComponentModel.DataAnnotations;
using System.Data;
using System.Text;
using System.Text.Json;
using Microsoft.AspNetCore.Authorization;
using Microsoft.AspNetCore.Mvc;
using Microsoft.AspNetCore.RateLimiting;
using Microsoft.AspNetCore.SignalR;
using Microsoft.EntityFrameworkCore;
using VisionI.API.Hubs;
using VisionI.API.Infrastructure;
using VisionI.API.Services;

namespace VisionI.API.Controllers;

[ApiController]
[Route("api/events")]
[Authorize]
[Produces("application/json")]
public class EventsController : ControllerBase
{
    private readonly IIntelligenceService _intelligence;
    private readonly AppDbContext _db;
    private readonly IHubContext<EventHub> _hub;
    private readonly ILogger<EventsController> _log;

    public EventsController(
        IIntelligenceService intelligence,
        AppDbContext db,
        IHubContext<EventHub> hub,
        ILogger<EventsController> log)
    {
        _intelligence = intelligence;
        _db = db;
        _hub = hub;
        _log = log;
    }

    [HttpGet]
    [EnableRateLimiting("query")]
    public async Task<IActionResult> GetEvents(
        [FromQuery] string? source = null,
        [FromQuery] string? event_type = null,
        [FromQuery] string? query = null,
        [FromQuery] string? sentiment = null,
        [FromQuery] string? from = null,
        [FromQuery] string? to = null,
        [FromQuery] string? sort_by = null,
        [FromQuery][Range(1, 1000)] int limit = 50,
        [FromQuery][Range(0, int.MaxValue)] int offset = 0,
        [FromQuery] string? job_id = null,
        CancellationToken ct = default)
    {
        if (string.IsNullOrWhiteSpace(job_id))
        {
            var dbPayload = await TryListEventsFromDbAsync(
                limit, offset, sort_by, source, event_type, query, sentiment, from, to, ct);
            if (dbPayload is not null)
                return Content(dbPayload, "application/json");

            var precomputed = await TryBuildPrecomputedEventsAsync(
                limit, offset, sort_by, source, event_type, query, sentiment, from, to, ct);
            if (precomputed is not null)
                return Content(precomputed, "application/json");
        }

        var cacheKey = $"cache:events:{source}:{event_type}:{query}:{sentiment}:{from}:{to}:{sort_by}:{limit}:{offset}:{job_id}";

        var json = await _intelligence.GetCachedJsonAsync(
            cacheKey,
            innerCt => _intelligence.GetPythonJsonAsync(
                $"/events{BuildQuery(
                    ("source", source),
                    ("event_type", event_type),
                    ("query", query),
                    ("sentiment", sentiment),
                    ("from", from),
                    ("to", to),
                    ("sort_by", sort_by),
                    ("limit", limit.ToString()),
                    ("offset", offset.ToString()),
                    ("job_id", job_id))}",
                innerCt),
            TimeSpan.FromMinutes(2),
            ct);

        if (json == null) return StatusCode(502, "Intelligence layer unavailable.");
        return Content(json, "application/json");
    }

    private async Task<string?> TryListEventsFromDbAsync(
        int limit,
        int offset,
        string? sortBy,
        string? source,
        string? eventType,
        string? query,
        string? sentiment,
        string? from,
        string? to,
        CancellationToken ct)
    {
        try
        {
            var conn = _db.Database.GetDbConnection();
            if (conn.State != ConnectionState.Open)
                await conn.OpenAsync(ct);

            var where = new List<string>();
            DateTime? fromTime = TryParseDate(from);
            DateTime? toTime = TryParseDate(to);
            if (fromTime.HasValue)
                where.Add("timestamp >= @from");
            if (toTime.HasValue)
                where.Add("timestamp <= @to");
            if (!string.IsNullOrWhiteSpace(source))
                where.Add("(LOWER(source) = LOWER(@source) OR LOWER(source) LIKE LOWER(@sourcePrefix))");
            if (!string.IsNullOrWhiteSpace(eventType))
                where.Add("LOWER(event_type) = LOWER(@eventType)");
            if (!string.IsNullOrWhiteSpace(sentiment))
                where.Add("LOWER(sentiment_label) = LOWER(@sentiment)");
            if (!string.IsNullOrWhiteSpace(query))
                where.Add("(title ILIKE @query OR description ILIKE @query OR body ILIKE @query OR source ILIKE @query OR event_type ILIKE @query OR author ILIKE @query OR location_name ILIKE @query OR actors::text ILIKE @query OR tags::text ILIKE @query OR extras::text ILIKE @query)");
            var whereSql = where.Count == 0 ? "" : "WHERE " + string.Join(" AND ", where);

            var facetWhere = new List<string>();
            if (fromTime.HasValue)
                facetWhere.Add("timestamp >= @from");
            if (toTime.HasValue)
                facetWhere.Add("timestamp <= @to");
            if (!string.IsNullOrWhiteSpace(eventType))
                facetWhere.Add("LOWER(event_type) = LOWER(@eventType)");
            if (!string.IsNullOrWhiteSpace(sentiment))
                facetWhere.Add("LOWER(sentiment_label) = LOWER(@sentiment)");
            if (!string.IsNullOrWhiteSpace(query))
                facetWhere.Add("(title ILIKE @query OR description ILIKE @query OR body ILIKE @query OR source ILIKE @query OR event_type ILIKE @query OR author ILIKE @query OR location_name ILIKE @query OR actors::text ILIKE @query OR tags::text ILIKE @query OR extras::text ILIKE @query)");
            var facetWhereSql = facetWhere.Count == 0 ? "" : "WHERE " + string.Join(" AND ", facetWhere);

            await using var totalCmd = conn.CreateCommand();
            totalCmd.CommandText = $"SELECT COUNT(*) FROM events {whereSql}";
            AddDateRangeParams(totalCmd, fromTime, toTime);
            AddFilterParams(totalCmd, source, eventType, query, sentiment);
            var totalObj = await totalCmd.ExecuteScalarAsync(ct);
            var total = totalObj is null or DBNull ? 0 : Convert.ToInt32(totalObj);

            var orderBy = sortBy?.Equals("risk_score", StringComparison.OrdinalIgnoreCase) == true
                ? "ORDER BY risk_score DESC NULLS LAST, timestamp DESC NULLS LAST"
                : "ORDER BY timestamp DESC NULLS LAST, ingest_time DESC NULLS LAST";

            await using var cmd = conn.CreateCommand();
            cmd.CommandText = $"""
                SELECT event_id, source, source_id, event_type, title, description, body, url,
                       language, author, timestamp, ingest_time, sentiment_label, sentiment_score,
                       location_lat, location_lon, location_name, actors::text, tags::text, extras::text,
                       confidence_score, influence_score, risk_score, supporting_signals::text,
                       signal_count, reasoning
                FROM events
                {whereSql}
                {orderBy}
                LIMIT @limit OFFSET @offset
                """;

            AddDateRangeParams(cmd, fromTime, toTime);
            AddFilterParams(cmd, source, eventType, query, sentiment);

            var limitParam = cmd.CreateParameter();
            limitParam.ParameterName = "@limit";
            limitParam.Value = limit;
            cmd.Parameters.Add(limitParam);

            var offsetParam = cmd.CreateParameter();
            offsetParam.ParameterName = "@offset";
            offsetParam.Value = offset;
            cmd.Parameters.Add(offsetParam);

            var events = new List<object>();
            await using (var reader = await cmd.ExecuteReaderAsync(ct))
            {
                while (await reader.ReadAsync(ct))
                {
                    var sourceName = reader.IsDBNull(1) ? "" : reader.GetString(1);
                    var title = reader.IsDBNull(4) ? "" : reader.GetString(4);

                    events.Add(new
                    {
                        event_id = reader.IsDBNull(0) ? "" : reader.GetString(0),
                        source = sourceName,
                        source_id = reader.IsDBNull(2) ? null : reader.GetString(2),
                        event_type = reader.IsDBNull(3) ? "" : reader.GetString(3),
                        title,
                        description = reader.IsDBNull(5) ? null : reader.GetString(5),
                        body = reader.IsDBNull(6) ? null : reader.GetString(6),
                        url = reader.IsDBNull(7) ? null : reader.GetString(7),
                        language = reader.IsDBNull(8) ? "en" : reader.GetString(8),
                        author = reader.IsDBNull(9) ? null : reader.GetString(9),
                        timestamp = ReadTimestamp(reader, 10),
                        ingest_time = ReadTimestamp(reader, 11),
                        sentiment = BuildSentiment(reader),
                        location = BuildLocation(reader),
                        actors = ParseJsonArray(reader, 17),
                        tags = ParseJsonArray(reader, 18),
                        extras = ParseJsonObject(reader, 19),
                        confidence_score = ReadNullableDouble(reader, 20),
                        influence_score = ReadNullableDouble(reader, 21),
                        risk_score = ReadNullableDouble(reader, 22),
                        supporting_signals = ParseJsonArray(reader, 23),
                        signal_count = reader.IsDBNull(24) ? 0 : reader.GetInt32(24),
                        reasoning = reader.IsDBNull(25) ? null : reader.GetString(25),
                        is_anomaly = sourceName.Contains("anomaly", StringComparison.OrdinalIgnoreCase) ||
                                     title.Contains("[ANOMALY]", StringComparison.OrdinalIgnoreCase),
                    });
                }
            }

            var sources = new List<object>();
            await using (var facetCmd = conn.CreateCommand())
            {
                facetCmd.CommandText = $"""
                    SELECT source, COUNT(*) AS count, MAX(timestamp) AS latest
                    FROM events
                    {facetWhereSql}
                    WHERE source IS NOT NULL AND source <> ''
                    GROUP BY source
                    ORDER BY count DESC
                    LIMIT 100
                    """;
                if (!string.IsNullOrWhiteSpace(facetWhereSql))
                {
                    facetCmd.CommandText = $"""
                        SELECT source, COUNT(*) AS count, MAX(timestamp) AS latest
                        FROM events
                        {facetWhereSql} AND source IS NOT NULL AND source <> ''
                        GROUP BY source
                        ORDER BY count DESC
                        LIMIT 100
                        """;
                }
                AddDateRangeParams(facetCmd, fromTime, toTime);
                AddFilterParams(facetCmd, null, eventType, query, sentiment);
                await using var facetReader = await facetCmd.ExecuteReaderAsync(ct);
                while (await facetReader.ReadAsync(ct))
                {
                    sources.Add(new
                    {
                        source = facetReader.IsDBNull(0) ? "" : facetReader.GetString(0),
                        count = facetReader.IsDBNull(1) ? 0 : Convert.ToInt32(facetReader.GetInt64(1)),
                        latest = facetReader.IsDBNull(2) ? null : facetReader.GetDateTime(2).ToString("O"),
                    });
                }
            }

            return JsonSerializer.Serialize(new
            {
                total,
                limit,
                offset,
                events,
                sources,
                _served_from = "db",
            });
        }
        catch (Exception ex)
        {
            _log.LogWarning(ex, "DB events fast path failed for source={Source}, eventType={EventType}, query={Query}", source, eventType, query);
            return null;
        }
    }

    private async Task<string?> TryBuildPrecomputedEventsAsync(
        int limit,
        int offset,
        string? sortBy,
        string? source,
        string? eventType,
        string? query,
        string? sentiment,
        string? from,
        string? to,
        CancellationToken ct)
    {
        try
        {
            var cached = await _intelligence.GetPrecomputedJsonAsync("precomputed:live_streams", ct);
            if (string.IsNullOrWhiteSpace(cached))
                return null;

            using var liveDoc = JsonDocument.Parse(cached);
            JsonElement eventsArray;
            if (liveDoc.RootElement.ValueKind == JsonValueKind.Array)
            {
                eventsArray = liveDoc.RootElement;
            }
            else if (liveDoc.RootElement.ValueKind == JsonValueKind.Object &&
                     liveDoc.RootElement.TryGetProperty("events", out var nestedEvents) &&
                     nestedEvents.ValueKind == JsonValueKind.Array)
            {
                eventsArray = nestedEvents;
            }
            else
            {
                return null;
            }

            DateTime? fromTime = TryParseDate(from);
            DateTime? toTime = TryParseDate(to);
            var items = eventsArray
                .EnumerateArray()
                .Where(e => PrecomputedMatches(e, source, eventType, query, sentiment, fromTime, toTime))
                .Select(static e => e.Clone())
                .ToList();
            if (sortBy?.Equals("risk_score", StringComparison.OrdinalIgnoreCase) == true)
            {
                items = items
                    .OrderByDescending(static e => e.TryGetProperty("risk_score", out var risk) && risk.TryGetDouble(out var value) ? value : double.MinValue)
                    .ToList();
            }
            else
            {
                items = items
                    .OrderByDescending(static e => e.TryGetProperty("timestamp", out var ts) ? ts.GetString() : null)
                    .ToList();
            }

            var total = items.Count;
            var page = items.Skip(offset).Take(limit).ToList();

            return JsonSerializer.Serialize(new
            {
                total,
                limit,
                offset,
                events = page,
                sources = BuildSourceFacets(items),
                _served_from = "precomputed",
            });
        }
        catch (Exception ex)
        {
            _log.LogDebug(ex, "Precomputed events fallback failed");
            return null;
        }
    }

    private static bool PrecomputedMatches(
        JsonElement e,
        string? source,
        string? eventType,
        string? query,
        string? sentiment,
        DateTime? fromTime,
        DateTime? toTime)
    {
        if (!string.IsNullOrWhiteSpace(source))
        {
            var actual = ReadJsonString(e, "source") ?? "";
            if (!actual.Equals(source, StringComparison.OrdinalIgnoreCase) &&
                !actual.StartsWith($"{source}_", StringComparison.OrdinalIgnoreCase))
            {
                return false;
            }
        }

        if (!string.IsNullOrWhiteSpace(eventType) &&
            !string.Equals(ReadJsonString(e, "event_type"), eventType, StringComparison.OrdinalIgnoreCase))
        {
            return false;
        }

        if (!string.IsNullOrWhiteSpace(sentiment))
        {
            var label = ReadJsonString(e, "sentiment_label") ?? ReadNestedJsonString(e, "sentiment", "label");
            if (!string.Equals(label, sentiment, StringComparison.OrdinalIgnoreCase))
                return false;
        }

        if ((fromTime.HasValue || toTime.HasValue) &&
            DateTime.TryParse(ReadJsonString(e, "timestamp"), out var ts))
        {
            if (fromTime.HasValue && ts < fromTime.Value)
                return false;
            if (toTime.HasValue && ts > toTime.Value)
                return false;
        }

        if (!string.IsNullOrWhiteSpace(query))
        {
            var q = query.Trim();
            var haystack = string.Join(" ",
                ReadJsonString(e, "title"),
                ReadJsonString(e, "description"),
                ReadJsonString(e, "body"),
                ReadJsonString(e, "source"),
                ReadJsonString(e, "event_type"),
                ReadJsonString(e, "author"),
                ReadNestedJsonString(e, "location", "name"),
                e.TryGetProperty("actors", out var actors) ? actors.ToString() : null,
                e.TryGetProperty("tags", out var tags) ? tags.ToString() : null,
                e.TryGetProperty("extras", out var extras) ? extras.ToString() : null);

            if (haystack.IndexOf(q, StringComparison.OrdinalIgnoreCase) < 0)
                return false;
        }

        return true;
    }

    private static List<object> BuildSourceFacets(IEnumerable<JsonElement> items)
        => items
            .Select(e => ReadJsonString(e, "source") ?? "")
            .Where(s => !string.IsNullOrWhiteSpace(s))
            .GroupBy(s => s, StringComparer.OrdinalIgnoreCase)
            .OrderByDescending(g => g.Count())
            .Take(100)
            .Select(g => (object)new { source = g.Key, count = g.Count(), latest = (string?)null })
            .ToList();

    private static string? ReadJsonString(JsonElement e, string name)
        => e.TryGetProperty(name, out var p) && p.ValueKind == JsonValueKind.String ? p.GetString() : null;

    private static string? ReadNestedJsonString(JsonElement e, string objectName, string name)
        => e.TryGetProperty(objectName, out var obj) &&
           obj.ValueKind == JsonValueKind.Object &&
           obj.TryGetProperty(name, out var p) &&
           p.ValueKind == JsonValueKind.String
            ? p.GetString()
            : null;

    [HttpGet("sources")]
    [EnableRateLimiting("query")]
    public async Task<IActionResult> GetEventSources(
        [FromQuery] string? query = null,
        [FromQuery] string? event_type = null,
        [FromQuery] string? sentiment = null,
        [FromQuery] string? from = null,
        [FromQuery] string? to = null,
        [FromQuery][Range(1, 200)] int limit = 100,
        CancellationToken ct = default)
    {
        try
        {
            var conn = _db.Database.GetDbConnection();
            if (conn.State != ConnectionState.Open)
                await conn.OpenAsync(ct);

            DateTime? fromTime = TryParseDate(from);
            DateTime? toTime = TryParseDate(to);
            var where = new List<string> { "source IS NOT NULL", "source <> ''" };
            if (fromTime.HasValue)
                where.Add("timestamp >= @from");
            if (toTime.HasValue)
                where.Add("timestamp <= @to");
            if (!string.IsNullOrWhiteSpace(event_type))
                where.Add("LOWER(event_type) = LOWER(@eventType)");
            if (!string.IsNullOrWhiteSpace(sentiment))
                where.Add("LOWER(sentiment_label) = LOWER(@sentiment)");
            if (!string.IsNullOrWhiteSpace(query))
                where.Add("(title ILIKE @query OR description ILIKE @query OR body ILIKE @query OR source ILIKE @query OR event_type ILIKE @query OR author ILIKE @query OR location_name ILIKE @query OR actors::text ILIKE @query OR tags::text ILIKE @query OR extras::text ILIKE @query)");

            await using var cmd = conn.CreateCommand();
            cmd.CommandText = $"""
                SELECT source, COUNT(*) AS count, MAX(timestamp) AS latest
                FROM events
                WHERE {string.Join(" AND ", where)}
                GROUP BY source
                ORDER BY count DESC
                LIMIT @limit
                """;
            AddDateRangeParams(cmd, fromTime, toTime);
            AddFilterParams(cmd, null, event_type, query, sentiment);
            var limitParam = cmd.CreateParameter();
            limitParam.ParameterName = "@limit";
            limitParam.Value = limit;
            cmd.Parameters.Add(limitParam);

            var sources = new List<object>();
            await using var reader = await cmd.ExecuteReaderAsync(ct);
            while (await reader.ReadAsync(ct))
            {
                sources.Add(new
                {
                    source = reader.IsDBNull(0) ? "" : reader.GetString(0),
                    count = reader.IsDBNull(1) ? 0 : Convert.ToInt32(reader.GetInt64(1)),
                    latest = reader.IsDBNull(2) ? null : reader.GetDateTime(2).ToString("O"),
                });
            }

            return Ok(new { total = sources.Count, sources, _served_from = "db" });
        }
        catch (Exception ex)
        {
            _log.LogWarning(ex, "Event source facet query failed");
            return StatusCode(500, new { error = "event source facets unavailable" });
        }
    }

    [HttpGet("feed")]
    [EnableRateLimiting("query")]
    public async Task<IActionResult> GetEventFeed(
        [FromQuery] string? source = null,
        [FromQuery] string? query = null,
        [FromQuery] string? sentiment = null,
        [FromQuery] string? from = null,
        [FromQuery] string? to = null,
        [FromQuery] string? sort = "latest",
        [FromQuery][Range(1, 200)] int limit = 50,
        [FromQuery][Range(0, int.MaxValue)] int offset = 0,
        CancellationToken ct = default)
    {
        var cacheKey = $"cache:events:feed:{source}:{query}:{sentiment}:{from}:{to}:{sort}:{limit}:{offset}";

        var json = await _intelligence.GetCachedJsonAsync(
            cacheKey,
            innerCt => _intelligence.GetPythonJsonAsync(
                $"/events/feed{BuildQuery(
                    ("source", source),
                    ("query", query),
                    ("sentiment", sentiment),
                    ("from", from),
                    ("to", to),
                    ("sort", sort),
                    ("limit", limit.ToString()),
                    ("offset", offset.ToString()))}",
                innerCt),
            TimeSpan.FromMinutes(1),
            ct);

        if (json == null) return StatusCode(502, "Intelligence layer unavailable.");
        return Content(json, "application/json");
    }

    /// <summary>Incremental delta — only events ingested after the given cursor. Powers client-side patching.</summary>
    [HttpGet("delta")]
    [EnableRateLimiting("query")]
    public async Task<IActionResult> GetEventsDelta(
        [FromQuery] string? since = null,
        [FromQuery][Range(1, 1000)] int limit = 200,
        CancellationToken ct = default)
    {
        // Short cache so a burst of hub ticks within a few seconds shares one DB hit.
        var cacheKey = $"cache:events:delta:{since}:{limit}";
        var json = await _intelligence.GetCachedJsonAsync(
            cacheKey,
            innerCt => _intelligence.GetPythonJsonAsync(
                $"/events/delta{BuildQuery(("since", since), ("limit", limit.ToString()))}",
                innerCt),
            TimeSpan.FromSeconds(5),
            ct);

        if (json == null) return StatusCode(502, "Intelligence layer unavailable.");
        return Content(json, "application/json");
    }

    [HttpGet("map")]
    [EnableRateLimiting("query")]
    public async Task<IActionResult> GetMap(
        [FromQuery] string? source = null,
        [FromQuery] string? event_type = null,
        [FromQuery] string? from = null,
        [FromQuery] string? to = null,
        [FromQuery] int limit = 500,
        CancellationToken ct = default)
    {
        var cacheKey = $"cache:events:map:{source}:{event_type}:{from}:{to}:{limit}";
        var json = await _intelligence.GetCachedJsonAsync(
            cacheKey,
            innerCt => _intelligence.GetPythonJsonAsync(
                $"/events/map{BuildQuery(
                    ("source", source),
                    ("event_type", event_type),
                    ("from", from),
                    ("to", to),
                    ("limit", limit.ToString()))}",
                innerCt),
            TimeSpan.FromMinutes(5),
            ct);

        if (json == null) return StatusCode(502, "Intelligence layer unavailable.");
        return Content(json, "application/json");
    }

    [HttpGet("{eventId}")]
    [EnableRateLimiting("query")]
    public async Task<IActionResult> GetEvent(string eventId, CancellationToken ct = default)
    {
        var cacheKey = $"cache:event:{eventId}";

        var json = await _intelligence.GetCachedJsonAsync(
            cacheKey,
            innerCt => _intelligence.GetPythonJsonAsync($"/events/{Uri.EscapeDataString(eventId)}", innerCt),
            TimeSpan.FromMinutes(10),
            ct);

        if (json == null) return NotFound();
        return Content(json, "application/json");
    }

    [HttpGet("{eventId}/intelligence")]
    public async Task<IActionResult> GetEventIntelligence(string eventId, CancellationToken ct = default)
    {
        var cacheKey = $"cache:event:intelligence:{eventId}";

        var json = await _intelligence.GetCachedJsonAsync(
            cacheKey,
            innerCt => _intelligence.GetPythonJsonAsync($"/event/{Uri.EscapeDataString(eventId)}", innerCt),
            TimeSpan.FromMinutes(5),
            ct);

        if (json == null) return NotFound();
        return Content(json, "application/json");
    }

    [HttpGet("{eventId}/social")]
    [EnableRateLimiting("query")]
    public async Task<IActionResult> GetEventSocial(
        string eventId,
        [FromQuery] int limit = 20,
        CancellationToken ct = default)
    {
        var cacheKey = $"cache:event:social:{eventId}:{limit}";

        var json = await _intelligence.GetCachedJsonAsync(
            cacheKey,
            innerCt => _intelligence.GetPythonJsonAsync($"/events/{Uri.EscapeDataString(eventId)}/social?limit={limit}", innerCt),
            TimeSpan.FromMinutes(5),
            ct);

        if (json == null) return StatusCode(502, "Intelligence layer unavailable.");
        return Content(json, "application/json");
    }

    [HttpGet("{eventId}/context")]
    [EnableRateLimiting("query")]
    public async Task<IActionResult> GetEventContext(string eventId, CancellationToken ct = default)
    {
        var cacheKey = $"cache:event:context:{eventId}";

        var json = await _intelligence.GetCachedJsonAsync(
            cacheKey,
            innerCt => _intelligence.GetPythonJsonAsync($"/event/{Uri.EscapeDataString(eventId)}/context", innerCt),
            TimeSpan.FromMinutes(5),
            ct);

        if (json == null) return NotFound();
        return Content(json, "application/json");
    }

    [HttpGet("{eventId}/full")]
    [EnableRateLimiting("query")]
    public async Task<IActionResult> GetEventFull(
        string eventId,
        [FromQuery] int social_limit = 20,
        [FromQuery] int similar_limit = 5,
        CancellationToken ct = default)
    {
        var cacheKey = $"cache:event:full:{eventId}:{social_limit}:{similar_limit}";

        var json = await _intelligence.GetCachedJsonAsync(
            cacheKey,
            async _ =>
            {
                async Task<JsonElement?> SafeAsync(Func<CancellationToken, Task<JsonDocument?>> call, string label)
                {
                    using var cts = CancellationTokenSource.CreateLinkedTokenSource(ct);
                    cts.CancelAfter(TimeSpan.FromSeconds(20));
                    try
                    {
                        var doc = await call(cts.Token);
                        return doc?.RootElement.Clone();
                    }
                    catch (OperationCanceledException) when (!ct.IsCancellationRequested)
                    {
                        _log.LogWarning("event/full fan-out [{Label}] timed out after 20 s", label);
                        return null;
                    }
                    catch (Exception ex)
                    {
                        _log.LogWarning("event/full fan-out [{Label}] failed: {Error}", label, ex.Message);
                        return null;
                    }
                }

                var ctxTask = SafeAsync(t => _intelligence.GetPythonDocumentAsync($"/event/{Uri.EscapeDataString(eventId)}/context", t), "context");
                var intelTask = SafeAsync(t => _intelligence.GetPythonDocumentAsync($"/event/{Uri.EscapeDataString(eventId)}", t), "intelligence");
                var socialTask = SafeAsync(t => _intelligence.GetPythonDocumentAsync($"/events/{Uri.EscapeDataString(eventId)}/social?limit={social_limit}", t), "social");
                var similarTask = SafeAsync(t => _intelligence.GetPythonDocumentAsync($"/copilot/similar/{Uri.EscapeDataString(eventId)}?limit={similar_limit}", t), "similar");
                var explainTask = SafeAsync(t => _intelligence.PostPythonDocumentAsync($"/copilot/explain/{Uri.EscapeDataString(eventId)}", new { }, t), "explain");
                var recommendTask = SafeAsync(t => _intelligence.GetPythonDocumentAsync($"/copilot/recommend/{Uri.EscapeDataString(eventId)}", t), "recommend");

                await Task.WhenAll(ctxTask, intelTask, socialTask, similarTask, explainTask, recommendTask);

                var ctx = await ctxTask;
                if (ctx == null) return null;

                var envelope = new
                {
                    event_id = eventId,
                    context = ctx,
                    intelligence = await intelTask,
                    social = await socialTask,
                    similar = await similarTask,
                    explain = await explainTask,
                    recommend = await recommendTask,
                    fetched_at = DateTimeOffset.UtcNow.ToString("O"),
                };

                return JsonSerializer.Serialize(envelope, new JsonSerializerOptions
                {
                    PropertyNamingPolicy = JsonNamingPolicy.CamelCase,
                    DefaultIgnoreCondition = System.Text.Json.Serialization.JsonIgnoreCondition.WhenWritingNull,
                });
            },
            TimeSpan.FromMinutes(3),
            ct);

        if (json == null) return NotFound();
        return Content(json, "application/json");
    }

    [HttpPost("{eventId}/enrich")]
    [Authorize(Roles = "Analyst,Admin")]
    public async Task<IActionResult> EnrichEvent(string eventId, [FromBody] JsonElement payload, CancellationToken ct = default)
    {
        var result = await _intelligence.PostPythonDocumentAsync($"/events/{Uri.EscapeDataString(eventId)}/enrich", payload, ct);
        if (result == null)
        {
            return Ok(new
            {
                event_id = eventId,
                enriched = false,
                reason = "embeddings_unavailable",
                message = "Vector embedding service is currently unavailable. Field intelligence was accepted but vector indexing will be retried by the background worker.",
            });
        }
        return Content(result.RootElement.GetRawText(), "application/json");
    }

    [HttpGet("window")]
    [EnableRateLimiting("query")]
    public async Task<IActionResult> GetWindow(
        [FromQuery] string? source = null,
        [FromQuery] string? event_type = null,
        [FromQuery] string? query = null,
        [FromQuery] string? sentiment = null,
        [FromQuery] string? from = null,
        [FromQuery] string? to = null,
        [FromQuery][Range(1, 500)] int limit = 100,
        [FromQuery] string? cursor = null,
        CancellationToken ct = default)
    {
        var offset = DecodeCursor(cursor);
        var result = await _intelligence.GetPythonDocumentAsync(
            $"/events{BuildQuery(
                ("source", source),
                ("event_type", event_type),
                ("query", query),
                ("sentiment", sentiment),
                ("from", from),
                ("to", to),
                ("limit", limit.ToString()),
                ("offset", offset.ToString()))}",
            ct);
        if (result == null) return StatusCode(502, "Intelligence layer unavailable.");

        var root = result.RootElement;
        var total = root.TryGetProperty("total", out var totalProp) && totalProp.TryGetInt32(out var t) ? t : offset + limit;
        var returned = root.TryGetProperty("events", out var eventsProp) && eventsProp.ValueKind == JsonValueKind.Array
            ? eventsProp.GetArrayLength()
            : 0;
        var nextOffset = offset + returned;
        var hasMore = nextOffset < total;

        var payload = new
        {
            window = new { from, to },
            offset,
            limit,
            has_more = hasMore,
            next_cursor = hasMore ? EncodeCursor(nextOffset) : null,
            data = root,
        };
        return Ok(payload);
    }

    [HttpGet("snapshot")]
    [EnableRateLimiting("query")]
    public async Task<IActionResult> GetSnapshotAtTime(
        [FromQuery] DateTimeOffset? at = null,
        [FromQuery][Range(1, 168)] int lookback_hours = 24,
        [FromQuery][Range(1, 500)] int limit = 200,
        CancellationToken ct = default)
    {
        var ts = at ?? DateTimeOffset.UtcNow;
        var fromIso = ts.AddHours(-lookback_hours).UtcDateTime.ToString("O");
        var toIso = ts.UtcDateTime.ToString("O");
        var cacheKey = $"cache:events:snapshot:{ts:yyyyMMddHHmm}:{lookback_hours}:{limit}";

        var json = await _intelligence.GetCachedJsonAsync(
            cacheKey,
            async _ =>
            {
                var doc = await _intelligence.GetPythonDocumentAsync(
                    $"/events?from={Uri.EscapeDataString(fromIso)}&to={Uri.EscapeDataString(toIso)}&limit={limit}&offset=0",
                    ct);
                return doc?.RootElement.GetRawText();
            },
            TimeSpan.FromMinutes(2),
            ct);

        if (json == null) return StatusCode(502, "Intelligence layer unavailable.");
        return Content(json, "application/json");
    }

    private static string EncodeCursor(int offset)
        => Convert.ToBase64String(Encoding.UTF8.GetBytes(offset.ToString()));

    private static int DecodeCursor(string? cursor)
    {
        if (string.IsNullOrWhiteSpace(cursor)) return 0;
        try
        {
            var raw = Encoding.UTF8.GetString(Convert.FromBase64String(cursor));
            return int.TryParse(raw, out var offset) && offset >= 0 ? offset : 0;
        }
        catch
        {
            return 0;
        }
    }

    private static string BuildQuery(params (string Key, string? Value)[] parts)
    {
        var included = parts
            .Where(p => !string.IsNullOrWhiteSpace(p.Value))
            .Select(p => $"{p.Key}={Uri.EscapeDataString(p.Value!)}")
            .ToArray();

        return included.Length == 0 ? "" : "?" + string.Join("&", included);
    }

    private static string? ReadTimestamp(IDataRecord reader, int ordinal)
        => reader.IsDBNull(ordinal) ? null : reader.GetDateTime(ordinal).ToString("O");

    private static DateTime? TryParseDate(string? raw)
        => DateTimeOffset.TryParse(raw, out var dto) ? dto.UtcDateTime : null;

    private static void AddDateRangeParams(System.Data.Common.DbCommand cmd, DateTime? from, DateTime? to)
    {
        if (from.HasValue)
        {
            var p = cmd.CreateParameter();
            p.ParameterName = "@from";
            p.Value = from.Value;
            cmd.Parameters.Add(p);
        }

        if (to.HasValue)
        {
            var p = cmd.CreateParameter();
            p.ParameterName = "@to";
            p.Value = to.Value;
            cmd.Parameters.Add(p);
        }
    }

    private static void AddFilterParams(
        System.Data.Common.DbCommand cmd,
        string? source,
        string? eventType,
        string? query,
        string? sentiment)
    {
        if (!string.IsNullOrWhiteSpace(source))
        {
            var p = cmd.CreateParameter();
            p.ParameterName = "@source";
            p.Value = source;
            cmd.Parameters.Add(p);

            var prefix = cmd.CreateParameter();
            prefix.ParameterName = "@sourcePrefix";
            prefix.Value = $"{source}_%";
            cmd.Parameters.Add(prefix);
        }

        if (!string.IsNullOrWhiteSpace(eventType))
        {
            var p = cmd.CreateParameter();
            p.ParameterName = "@eventType";
            p.Value = eventType;
            cmd.Parameters.Add(p);
        }

        if (!string.IsNullOrWhiteSpace(sentiment))
        {
            var p = cmd.CreateParameter();
            p.ParameterName = "@sentiment";
            p.Value = sentiment;
            cmd.Parameters.Add(p);
        }

        if (!string.IsNullOrWhiteSpace(query))
        {
            var p = cmd.CreateParameter();
            p.ParameterName = "@query";
            p.Value = $"%{query.Trim()}%";
            cmd.Parameters.Add(p);
        }
    }

    private static double? ReadNullableDouble(IDataRecord reader, int ordinal)
        => reader.IsDBNull(ordinal) ? null : reader.GetDouble(ordinal);

    private static JsonElement ParseJsonArray(IDataRecord reader, int ordinal)
    {
        if (reader.IsDBNull(ordinal))
            return JsonSerializer.SerializeToElement(Array.Empty<object>());

        var raw = reader.GetString(ordinal);
        return JsonDocument.Parse(string.IsNullOrWhiteSpace(raw) ? "[]" : raw).RootElement.Clone();
    }

    private static JsonElement ParseJsonObject(IDataRecord reader, int ordinal)
    {
        if (reader.IsDBNull(ordinal))
            return JsonSerializer.SerializeToElement(new { });

        var raw = reader.GetString(ordinal);
        return JsonDocument.Parse(string.IsNullOrWhiteSpace(raw) ? "{}" : raw).RootElement.Clone();
    }

    private static object? BuildSentiment(IDataRecord reader)
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

    private static object? BuildLocation(IDataRecord reader)
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
