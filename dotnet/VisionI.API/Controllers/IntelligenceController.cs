using Microsoft.AspNetCore.Authorization;
using Microsoft.AspNetCore.Mvc;
using Microsoft.AspNetCore.RateLimiting;
using System.ComponentModel.DataAnnotations;
using System.Data;
using System.Data.Common;
using System.Text;
using System.Text.Json;
using Microsoft.EntityFrameworkCore;
using VisionI.API.Models.Responses;
using VisionI.API.Services;
using VisionI.API.Infrastructure;

namespace VisionI.API.Controllers;

[ApiController]
[Route("api/narratives")]
[Authorize]
[Produces("application/json")]
public class NarrativesController : ControllerBase
{
    private readonly IIntelligenceService _intelligence;
    private readonly AppDbContext _db;

    public NarrativesController(IIntelligenceService intelligence, AppDbContext db)
    {
        _intelligence = intelligence;
        _db = db;
    }

    [HttpPost("detect")]
    [Authorize(Roles = "Analyst,Admin")]
    public async Task<IActionResult> TriggerDetection(
        [FromQuery] int window_hours = 6,
        [FromQuery] int baseline_days = 7,
        [FromQuery] bool persist = true,
        CancellationToken ct = default)
    {
        await _intelligence.RemoveCacheAsync("cache:narratives:summary", ct);
        var json = await _intelligence.PostPythonJsonAsync("/narratives/detect", new
        {
            window_hours,
            baseline_days,
            persist,
        }, ct);

        if (json == null) return StatusCode(502, "Intelligence layer unavailable.");
        return new ContentResult
        {
            StatusCode = StatusCodes.Status202Accepted,
            Content = json,
            ContentType = "application/json"
        };
    }

    [HttpGet]
    [EnableRateLimiting("query")]
    public async Task<IActionResult> ListNarratives(
        [FromQuery] string? signal_type = null,
        [FromQuery] string? severity = null,
        [FromQuery] string? status = "active",
        [FromQuery] string? from_time = null,
        [FromQuery] int limit = 50,
        [FromQuery] int offset = 0,
        CancellationToken ct = default)
    {
        if (string.IsNullOrWhiteSpace(signal_type) &&
            string.IsNullOrWhiteSpace(severity) &&
            string.IsNullOrWhiteSpace(from_time) &&
            string.IsNullOrWhiteSpace(status) is false &&
            string.Equals(status, "active", StringComparison.OrdinalIgnoreCase))
        {
            var fast = await TryListNarrativesFromDbAsync(status, limit, offset, ct);
            if (fast is not null)
                return Content(_intelligence.SerializeJson(fast), "application/json");
        }

        var key = $"cache:narratives:list:{signal_type}:{severity}:{status}:{from_time}:{limit}:{offset}";
        var json = await _intelligence.GetCachedJsonAsync(
            key,
            innerCt => _intelligence.GetPythonJsonAsync(BuildListPath("/narratives", signal_type, severity, status, from_time, limit, offset), innerCt),
            TimeSpan.FromMinutes(5),
            ct);

        if (json == null) return StatusCode(502, "Intelligence layer unavailable.");
        var payload = _intelligence.DeserializeJson<NarrativeListResponse>(json);
        return payload is null ? StatusCode(502, "Invalid narrative payload.") : Content(_intelligence.SerializeJson(payload), "application/json");
    }

    private async Task<NarrativeListResponse?> TryListNarrativesFromDbAsync(string status, int limit, int offset, CancellationToken ct)
    {
        try
        {
            await using var conn = _db.Database.GetDbConnection();
            if (conn.State != ConnectionState.Open)
                await conn.OpenAsync(ct);

            await using var totalCmd = conn.CreateCommand();
            totalCmd.CommandText = "SELECT COUNT(*) FROM narratives WHERE status = @status";
            var totalStatus = totalCmd.CreateParameter();
            totalStatus.ParameterName = "@status";
            totalStatus.Value = status;
            totalCmd.Parameters.Add(totalStatus);

            var totalObj = await totalCmd.ExecuteScalarAsync(ct);
            var total = totalObj is null or DBNull ? 0 : Convert.ToInt32(totalObj);

            await using var cmd = conn.CreateCommand();
            cmd.CommandText = """
                SELECT narrative_id, signal_type, topic, strength, confidence, severity,
                       event_count, source_count, sources, actors, sample_titles,
                       window_start, window_end, detected_at, meta_data, status
                FROM narratives
                WHERE status = @status
                ORDER BY detected_at DESC
                LIMIT @limit OFFSET @offset
                """;

            var statusParam = cmd.CreateParameter();
            statusParam.ParameterName = "@status";
            statusParam.Value = status;
            cmd.Parameters.Add(statusParam);

            var limitParam = cmd.CreateParameter();
            limitParam.ParameterName = "@limit";
            limitParam.Value = limit;
            cmd.Parameters.Add(limitParam);

            var offsetParam = cmd.CreateParameter();
            offsetParam.ParameterName = "@offset";
            offsetParam.Value = offset;
            cmd.Parameters.Add(offsetParam);

            var narratives = new List<NarrativeRecordResponse>();
            await using var reader = await cmd.ExecuteReaderAsync(ct);
            while (await reader.ReadAsync(ct))
            {
                var metadata = ParseJsonObject(reader, 14);
                var geographicSpread = JsonSerializer.SerializeToElement(new Dictionary<string, double>());
                if (metadata.ValueKind == JsonValueKind.Object &&
                    metadata.TryGetProperty("geographic_spread", out var spread))
                {
                    geographicSpread = spread.Clone();
                }

                narratives.Add(new NarrativeRecordResponse(
                    NarrativeId: reader.IsDBNull(0) ? "" : reader.GetString(0),
                    SignalType: reader.IsDBNull(1) ? "" : reader.GetString(1),
                    Topic: reader.IsDBNull(2) ? "" : reader.GetString(2),
                    Strength: reader.IsDBNull(3) ? 0 : reader.GetDouble(3),
                    Confidence: reader.IsDBNull(4) ? 0 : reader.GetDouble(4),
                    Severity: reader.IsDBNull(5) ? "low" : reader.GetString(5),
                    EventCount: reader.IsDBNull(6) ? 0 : reader.GetInt32(6),
                    SourceCount: reader.IsDBNull(7) ? 0 : reader.GetInt32(7),
                    Sources: ParseJsonStringList(reader, 8),
                    Actors: ParseJsonStringList(reader, 9),
                    SampleTitles: ParseJsonStringList(reader, 10),
                    WindowStart: reader.IsDBNull(11) ? null : reader.GetDateTime(11).ToString("O"),
                    WindowEnd: reader.IsDBNull(12) ? null : reader.GetDateTime(12).ToString("O"),
                    DetectedAt: reader.IsDBNull(13) ? null : reader.GetDateTime(13).ToString("O"),
                    Metadata: metadata,
                    GeographicSpread: geographicSpread,
                    Status: reader.IsDBNull(15) ? status : reader.GetString(15)
                ));
            }

            return new NarrativeListResponse(total, limit, offset, narratives);
        }
        catch
        {
            return null;
        }
    }

    private static List<string> ParseJsonStringList(DbDataReader reader, int ordinal)
    {
        if (reader.IsDBNull(ordinal))
            return new List<string>();

        var raw = reader.GetString(ordinal);
        return JsonSerializer.Deserialize<List<string>>(raw) ?? new List<string>();
    }

    private static JsonElement ParseJsonObject(DbDataReader reader, int ordinal)
    {
        if (reader.IsDBNull(ordinal))
            return JsonSerializer.SerializeToElement(new { });

        var raw = reader.GetString(ordinal);
        return JsonDocument.Parse(string.IsNullOrWhiteSpace(raw) ? "{}" : raw).RootElement.Clone();
    }

    [HttpGet("summary")]
    [EnableRateLimiting("query")]
    public async Task<IActionResult> GetSummary(CancellationToken ct = default)
    {
        var precomputed = await _intelligence.GetPrecomputedJsonAsync("precomputed:narratives_summary", ct);
        if (precomputed != null)
        {
            var cachedPayload = _intelligence.DeserializeJson<NarrativeSummaryResponse>(precomputed);
            if (cachedPayload != null)
                return Content(_intelligence.SerializeJson(cachedPayload with { ServedFrom = "precomputed" }), "application/json");
        }

        var json = await _intelligence.GetCachedJsonAsync(
            "cache:narratives:summary",
            innerCt => _intelligence.GetPythonJsonAsync("/narratives/summary", innerCt),
            TimeSpan.FromMinutes(5),
            ct);

        if (json == null) return StatusCode(502, "Intelligence layer unavailable.");
        var payload = _intelligence.DeserializeJson<NarrativeSummaryResponse>(json);
        return payload is null ? StatusCode(502, "Invalid narrative summary payload.") : Content(_intelligence.SerializeJson(payload), "application/json");
    }

    [HttpGet("influence")]
    [EnableRateLimiting("query")]
    public async Task<IActionResult> GetInfluenceNetwork(
        [FromQuery] int limit = 200,
        [FromQuery] float min_strength = 0.1f,
        CancellationToken ct = default)
    {
        var precomputed = await _intelligence.GetPrecomputedJsonAsync("precomputed:influence_network", ct);
        if (precomputed != null)
        {
            var cachedPayload = _intelligence.DeserializeJson<InfluenceNetworkResponse>(precomputed);
            if (cachedPayload != null)
                return Content(_intelligence.SerializeJson(cachedPayload with { ServedFrom = "precomputed" }), "application/json");
        }

        var key = $"cache:narratives:influence:{limit}:{min_strength}";
        var json = await _intelligence.GetCachedJsonAsync(
            key,
            innerCt => _intelligence.GetPythonJsonAsync($"/narratives/influence?limit={limit}&min_strength={min_strength}", innerCt),
            TimeSpan.FromMinutes(15),
            ct);

        if (json == null) return StatusCode(502, "Intelligence layer unavailable.");
        var payload = _intelligence.DeserializeJson<InfluenceNetworkResponse>(json);
        return payload is null ? StatusCode(502, "Invalid influence payload.") : Content(_intelligence.SerializeJson(payload), "application/json");
    }

    [HttpGet("timeline")]
    [EnableRateLimiting("query")]
    public async Task<IActionResult> GetTimeline(
        [FromQuery] string? topic = null,
        [FromQuery] string bucket = "day",
        [FromQuery] int days_back = 7,
        CancellationToken ct = default)
    {
        var key = $"cache:narratives:timeline:{topic}:{bucket}:{days_back}";
        var path = $"/narratives/timeline?bucket={Uri.EscapeDataString(bucket)}&days_back={days_back}";
        if (!string.IsNullOrWhiteSpace(topic)) path += $"&topic={Uri.EscapeDataString(topic)}";
        var json = await _intelligence.GetCachedJsonAsync(
            key,
            innerCt => _intelligence.GetPythonJsonAsync(path, innerCt),
            TimeSpan.FromMinutes(5),
            ct);

        if (json == null) return StatusCode(502, "Intelligence layer unavailable.");
        return Content(json, "application/json");
    }

    [HttpGet("{narrativeId}/forecast")]
    [EnableRateLimiting("query")]
    public async Task<IActionResult> GetForecast(
        string narrativeId,
        [FromQuery] int horizon = 12,
        CancellationToken ct = default)
    {
        var key = $"cache:narratives:forecast:{narrativeId}:{horizon}";
        var json = await _intelligence.GetCachedJsonAsync(
            key,
            innerCt => _intelligence.GetPythonJsonAsync($"/narratives/{Uri.EscapeDataString(narrativeId)}/forecast?horizon={horizon}", innerCt),
            TimeSpan.FromMinutes(10),
            ct);

        if (json == null) return NotFound();
        var payload = _intelligence.DeserializeJson<NarrativeForecastResponse>(json);
        return payload is null ? StatusCode(502, "Invalid forecast payload.") : Content(_intelligence.SerializeJson(payload), "application/json");
    }

    [HttpGet("window")]
    [EnableRateLimiting("query")]
    public async Task<IActionResult> GetNarrativesWindow(
        [FromQuery] string? signal_type = null,
        [FromQuery] string? severity = null,
        [FromQuery] string? status = "active",
        [FromQuery] string? from_time = null,
        [FromQuery][Range(1, 250)] int limit = 50,
        [FromQuery] string? cursor = null,
        CancellationToken ct = default)
    {
        var offset = DecodeCursor(cursor);
        var json = await _intelligence.GetPythonJsonAsync(
            BuildListPath("/narratives", signal_type, severity, status, from_time, limit, offset),
            ct);
        if (json == null) return StatusCode(502, "Intelligence layer unavailable.");

        using var doc = JsonDocument.Parse(json);
        var root = doc.RootElement;
        var total = root.TryGetProperty("total", out var totalProp) && totalProp.TryGetInt32(out var t) ? t : offset + limit;
        var returned = root.TryGetProperty("narratives", out var itemsProp) && itemsProp.ValueKind == JsonValueKind.Array
            ? itemsProp.GetArrayLength()
            : 0;
        var nextOffset = offset + returned;
        var hasMore = nextOffset < total;

        return Ok(new
        {
            offset,
            limit,
            has_more = hasMore,
            next_cursor = hasMore ? EncodeCursor(nextOffset) : null,
            data = root.Clone(),
        });
    }

    [HttpPost("influence/update")]
    [Authorize(Roles = "Admin")]
    public async Task<IActionResult> UpdateInfluenceScores(CancellationToken ct = default)
    {
        var json = await _intelligence.PostPythonJsonAsync("/narratives/influence/update", null, ct);
        if (json == null) return StatusCode(502, "Intelligence layer unavailable.");
        return new ContentResult
        {
            StatusCode = StatusCodes.Status202Accepted,
            Content = json,
            ContentType = "application/json"
        };
    }

    private static string BuildListPath(string basePath, string? signalType, string? severity, string? status, string? fromTime, int limit, int offset)
    {
        var parts = new List<string> { $"limit={limit}", $"offset={offset}" };
        if (signalType != null) parts.Add($"signal_type={Uri.EscapeDataString(signalType)}");
        if (severity != null) parts.Add($"severity={Uri.EscapeDataString(severity)}");
        if (status != null) parts.Add($"status={Uri.EscapeDataString(status)}");
        if (fromTime != null) parts.Add($"from_time={Uri.EscapeDataString(fromTime)}");
        return $"{basePath}?{string.Join("&", parts)}";
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
}

[ApiController]
[Route("api/alerts")]
[Authorize]
[Produces("application/json")]
public class AlertsController : ControllerBase
{
    private readonly IIntelligenceService _intelligence;
    private readonly AppDbContext _db;

    public AlertsController(IIntelligenceService intelligence, AppDbContext db)
    {
        _intelligence = intelligence;
        _db = db;
    }

    [HttpPost("scan")]
    [Authorize(Roles = "Analyst,Admin")]
    public async Task<IActionResult> TriggerScan(
        [FromQuery] int window_hours = 1,
        [FromQuery] int baseline_days = 7,
        [FromQuery] bool persist = true,
        CancellationToken ct = default)
    {
        await _intelligence.RemoveCacheAsync("cache:alerts:summary", ct);
        var json = await _intelligence.PostPythonJsonAsync("/alerts/scan", new
        {
            window_hours,
            baseline_days,
            persist,
        }, ct);

        if (json == null) return StatusCode(502, "Intelligence layer unavailable.");
        return new ContentResult
        {
            StatusCode = StatusCodes.Status202Accepted,
            Content = json,
            ContentType = "application/json"
        };
    }

    [HttpGet]
    [EnableRateLimiting("query")]
    public async Task<IActionResult> ListAlerts(
        [FromQuery] string? alert_type = null,
        [FromQuery] string? severity = null,
        [FromQuery] bool? acknowledged = null,
        [FromQuery] string? from_time = null,
        [FromQuery] int limit = 50,
        [FromQuery] int offset = 0,
        CancellationToken ct = default)
    {
        if (string.IsNullOrWhiteSpace(alert_type) &&
            string.IsNullOrWhiteSpace(severity) &&
            !acknowledged.HasValue &&
            string.IsNullOrWhiteSpace(from_time))
        {
            var fast = await TryListAlertsFromDbAsync(limit, offset, ct);
            if (fast is not null)
                return Content(_intelligence.SerializeJson(fast), "application/json");
        }

        var key = $"cache:alerts:list:{alert_type}:{severity}:{acknowledged}:{from_time}:{limit}:{offset}";
        var json = await _intelligence.GetCachedJsonAsync(
            key,
            innerCt => _intelligence.GetPythonJsonAsync(BuildAlertsPath(limit, offset, alert_type, severity, acknowledged, from_time), innerCt),
            TimeSpan.FromMinutes(2),
            ct);

        if (json == null) return StatusCode(502, "Intelligence layer unavailable.");
        var payload = _intelligence.DeserializeJson<AlertsResponse>(json);
        if (payload is null) return StatusCode(502, "Invalid alert payload.");

        var projected = payload with
        {
            Alerts = payload.Alerts
                .Select(alert => alert with { Indicator = BuildAlertIndicator(alert) })
                .ToList()
        };

        return Content(_intelligence.SerializeJson(projected), "application/json");
    }

    private async Task<AlertsResponse?> TryListAlertsFromDbAsync(int limit, int offset, CancellationToken ct)
    {
        try
        {
            await using var conn = _db.Database.GetDbConnection();
            if (conn.State != ConnectionState.Open)
                await conn.OpenAsync(ct);

            await using var totalCmd = conn.CreateCommand();
            totalCmd.CommandText = "SELECT COUNT(*) FROM alerts";
            var totalObj = await totalCmd.ExecuteScalarAsync(ct);
            var total = totalObj is null or DBNull ? 0 : Convert.ToInt32(totalObj);

            await using var cmd = conn.CreateCommand();
            cmd.CommandText = """
                SELECT alert_id, alert_type, severity, title, description, entity, entity_type,
                       event_count, baseline, z_score, sources, location, detected_at,
                       resolved_at, acknowledged, meta_data
                FROM alerts
                ORDER BY detected_at DESC
                LIMIT @limit OFFSET @offset
                """;

            var limitParam = cmd.CreateParameter();
            limitParam.ParameterName = "@limit";
            limitParam.Value = limit;
            cmd.Parameters.Add(limitParam);

            var offsetParam = cmd.CreateParameter();
            offsetParam.ParameterName = "@offset";
            offsetParam.Value = offset;
            cmd.Parameters.Add(offsetParam);

            var alerts = new List<AlertRecordResponse>();
            await using var reader = await cmd.ExecuteReaderAsync(ct);
            while (await reader.ReadAsync(ct))
            {
                var sources = new List<string>();
                if (!reader.IsDBNull(10))
                {
                    var rawSources = reader.GetString(10);
                    sources = JsonSerializer.Deserialize<List<string>>(rawSources) ?? new List<string>();
                }

                JsonElement metadata = JsonSerializer.SerializeToElement(new { });
                if (!reader.IsDBNull(15))
                {
                    var rawMeta = reader.GetString(15);
                    metadata = JsonDocument.Parse(string.IsNullOrWhiteSpace(rawMeta) ? "{}" : rawMeta).RootElement.Clone();
                }

                var alert = new AlertRecordResponse(
                    AlertId: reader.IsDBNull(0) ? null : reader.GetString(0),
                    AlertType: reader.IsDBNull(1) ? "" : reader.GetString(1),
                    Severity: reader.IsDBNull(2) ? "medium" : reader.GetString(2),
                    Title: reader.IsDBNull(3) ? "--" : reader.GetString(3),
                    Description: reader.IsDBNull(4) ? null : reader.GetString(4),
                    Entity: reader.IsDBNull(5) ? null : reader.GetString(5),
                    EntityType: reader.IsDBNull(6) ? null : reader.GetString(6),
                    EventCount: reader.IsDBNull(7) ? 0 : reader.GetInt32(7),
                    Baseline: reader.IsDBNull(8) ? 0 : reader.GetDouble(8),
                    ZScore: reader.IsDBNull(9) ? 0 : reader.GetDouble(9),
                    Sources: sources,
                    Location: reader.IsDBNull(11) ? null : reader.GetString(11),
                    DetectedAt: reader.IsDBNull(12) ? null : reader.GetDateTime(12).ToString("O"),
                    ResolvedAt: reader.IsDBNull(13) ? null : reader.GetDateTime(13).ToString("O"),
                    Acknowledged: !reader.IsDBNull(14) && reader.GetBoolean(14),
                    Metadata: metadata
                );

                alerts.Add(alert with { Indicator = BuildAlertIndicator(alert) });
            }

            return new AlertsResponse(total, alerts);
        }
        catch
        {
            return null;
        }
    }

    [HttpGet("summary")]
    [EnableRateLimiting("query")]
    public async Task<IActionResult> GetSummary(CancellationToken ct = default)
    {
        var precomputed = await _intelligence.GetPrecomputedJsonAsync("precomputed:alerts_summary", ct);
        if (precomputed != null) return Content(precomputed, "application/json");

        var json = await _intelligence.GetCachedJsonAsync(
            "cache:alerts:summary",
            innerCt => _intelligence.GetPythonJsonAsync("/alerts/summary", innerCt),
            TimeSpan.FromMinutes(1),
            ct);

        if (json == null) return StatusCode(502, "Intelligence layer unavailable.");
        return Content(json, "application/json");
    }

    [HttpPost("{alertId}/ack")]
    [Authorize(Roles = "Analyst,Admin")]
    public async Task<IActionResult> Acknowledge(string alertId, CancellationToken ct = default)
    {
        await _intelligence.RemoveCacheAsync("cache:alerts:summary", ct);
        var json = await _intelligence.PostPythonJsonAsync($"/alerts/{Uri.EscapeDataString(alertId)}/ack", null, ct);
        if (json == null) return StatusCode(502, "Intelligence layer unavailable.");
        return Content(json, "application/json");
    }

    [HttpPost("{alertId}/resolve")]
    [Authorize(Roles = "Analyst,Admin")]
    public async Task<IActionResult> Resolve(string alertId, CancellationToken ct = default)
    {
        await _intelligence.RemoveCacheAsync("cache:alerts:summary", ct);
        var json = await _intelligence.PostPythonJsonAsync($"/alerts/{Uri.EscapeDataString(alertId)}/resolve", null, ct);
        if (json == null) return StatusCode(502, "Intelligence layer unavailable.");
        return Content(json, "application/json");
    }

    [HttpGet("window")]
    [EnableRateLimiting("query")]
    public async Task<IActionResult> GetAlertsWindow(
        [FromQuery] string? alert_type = null,
        [FromQuery] string? severity = null,
        [FromQuery] bool? acknowledged = null,
        [FromQuery] string? from_time = null,
        [FromQuery][Range(1, 250)] int limit = 50,
        [FromQuery] string? cursor = null,
        CancellationToken ct = default)
    {
        var offset = DecodeCursor(cursor);
        var json = await _intelligence.GetPythonJsonAsync(
            BuildAlertsPath(limit, offset, alert_type, severity, acknowledged, from_time),
            ct);
        if (json == null) return StatusCode(502, "Intelligence layer unavailable.");

        using var doc = JsonDocument.Parse(json);
        var root = doc.RootElement;
        var total = root.TryGetProperty("total", out var totalProp) && totalProp.TryGetInt32(out var t) ? t : offset + limit;
        var returned = root.TryGetProperty("alerts", out var itemsProp) && itemsProp.ValueKind == JsonValueKind.Array
            ? itemsProp.GetArrayLength()
            : 0;
        var nextOffset = offset + returned;
        var hasMore = nextOffset < total;

        return Ok(new
        {
            offset,
            limit,
            has_more = hasMore,
            next_cursor = hasMore ? EncodeCursor(nextOffset) : null,
            data = root.Clone(),
        });
    }

    private static string BuildAlertsPath(int limit, int offset, string? alertType, string? severity, bool? acknowledged, string? fromTime)
    {
        var parts = new List<string> { $"limit={limit}", $"offset={offset}" };
        if (alertType != null) parts.Add($"alert_type={Uri.EscapeDataString(alertType)}");
        if (severity != null) parts.Add($"severity={Uri.EscapeDataString(severity)}");
        if (acknowledged.HasValue) parts.Add($"acknowledged={acknowledged.Value.ToString().ToLowerInvariant()}");
        if (fromTime != null) parts.Add($"from={Uri.EscapeDataString(fromTime)}");
        return $"/alerts?{string.Join("&", parts)}";
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

    private static AnalystIndicatorResponse BuildAlertIndicator(AlertRecordResponse alert)
    {
        var alertType = NormalizeAlertType(alert.AlertType);
        var driver = alertType switch
        {
            "sentiment_deterioration" => "sentiment deterioration",
            "geographic_cluster" => "geographic clustering",
            "entity_spike" => "actor convergence",
            "source_silence" => "source silence",
            "coordinated_amplification" => "coordinated amplification",
            "escalation_risk" => "escalation pressure",
            _ => string.IsNullOrWhiteSpace(alert.AlertType) ? "corroborated anomaly" : alert.AlertType.Replace("_", " "),
        };

        var severity = string.IsNullOrWhiteSpace(alert.Severity) ? "medium" : alert.Severity.ToLowerInvariant();
        var trajectory = severity is "critical" or "high" ? "rising" : "stable";
        var sourceCount = alert.Sources?.Count ?? 0;
        var corroboration = Math.Min(1.0,
            (sourceCount * 0.18) +
            (alert.EventCount * 0.08) +
            (Math.Max(alert.ZScore, 0) * 0.08));
        var action = severity switch
        {
            "critical" => "triage now",
            "high" => "investigate",
            "medium" => "review",
            _ => "monitor"
        };

        return new AnalystIndicatorResponse(
            Id: alert.AlertId ?? alert.Title,
            Label: alert.Title,
            Category: "alert",
            IndicatorKind: "alert",
            EvidenceKind: sourceCount > 1 || alert.EventCount > 1 ? "correlated" : "observed",
            AssessmentKind: alertType,
            Severity: severity,
            Driver: driver,
            DriverCode: alertType,
            Trajectory: trajectory,
            TrajectoryCode: trajectory,
            RecommendedAction: action,
            RecommendedActionCode: NormalizeCode(action, "monitor"),
            Region: alert.Location,
            Score: alert.ZScore,
            Confidence: corroboration,
            Corroboration: corroboration,
            Linked: new IndicatorLinkCountsResponse(
                Actors: string.IsNullOrWhiteSpace(alert.Entity) ? 0 : 1,
                Narratives: 0,
                Signals: 0,
                Alerts: 1,
                Regions: string.IsNullOrWhiteSpace(alert.Location) ? 0 : 1,
                Sources: sourceCount,
                Events: alert.EventCount
            ),
            Summary: alert.Description ?? alert.Title,
            ObservationSummary: $"{sourceCount} source(s) observed {alert.Title.ToLowerInvariant()} with {alert.EventCount} linked event(s).",
            AssessmentSummary: $"{alert.Title} is classified as {severity} severity with z-score {alert.ZScore:0.0}.",
            CorrelationSummary: $"{sourceCount} source(s), {(string.IsNullOrWhiteSpace(alert.Entity) ? "no linked entity" : $"entity {alert.Entity}")}, and {(string.IsNullOrWhiteSpace(alert.Location) ? "no linked region" : $"location {alert.Location}")} support this alert."
        );
    }

    private static string NormalizeAlertType(string? alertType)
        => (alertType ?? "").Trim().ToLowerInvariant() switch
        {
            "sentiment_shift" => "sentiment_deterioration",
            "geo_cluster" => "geographic_cluster",
            "" => "corroborated_alert",
            var raw => raw
        };

    private static string NormalizeCode(string? value, string fallback)
    {
        if (string.IsNullOrWhiteSpace(value))
            return fallback;

        var normalized = value.Trim().ToLowerInvariant()
            .Replace("/", " ")
            .Replace("-", " ")
            .Replace(".", " ");
        var parts = normalized
            .Split(' ', StringSplitOptions.RemoveEmptyEntries | StringSplitOptions.TrimEntries);
        return parts.Length == 0 ? fallback : string.Join("_", parts);
    }
}
