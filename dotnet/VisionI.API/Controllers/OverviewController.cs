using System.Data;
using System.Text.Json;
using Microsoft.AspNetCore.Authorization;
using Microsoft.AspNetCore.Mvc;
using Microsoft.AspNetCore.RateLimiting;
using Microsoft.EntityFrameworkCore;
using VisionI.API.Infrastructure;
using VisionI.API.Services;

namespace VisionI.API.Controllers;

[ApiController]
[Route("api/overview")]
[Authorize]
[Produces("application/json")]
public class OverviewController : ControllerBase
{
    private readonly IIntelligenceService _intelligence;
    private readonly AppDbContext _db;
    private readonly SourceCatalogService _sources;

    public OverviewController(IIntelligenceService intelligence, AppDbContext db, SourceCatalogService sources)
    {
        _intelligence = intelligence;
        _db = db;
        _sources = sources;
    }

    [HttpGet]
    [EnableRateLimiting("query")]
    public async Task<IActionResult> GetOverview(
        [FromQuery] int window_hours = 24,
        CancellationToken ct = default)
    {
        var fast = await TryBuildDbOverviewAsync(window_hours, ct);
        if (fast is not null)
            return Content(fast, "application/json");

        var cacheKey = $"cache:overview:{window_hours}";
        var json = await _intelligence.GetCachedJsonAsync(
            cacheKey,
            innerCt => _intelligence.GetPythonJsonAsync($"/overview?window_hours={window_hours}", innerCt),
            TimeSpan.FromMinutes(2),
            ct);

        if (json == null) return StatusCode(502, "Intelligence layer unavailable.");
        return Content(json, "application/json");
    }

    private async Task<string?> TryBuildDbOverviewAsync(int windowHours, CancellationToken ct)
    {
        try
        {
            await using var conn = _db.Database.GetDbConnection();
            if (conn.State != ConnectionState.Open)
                await conn.OpenAsync(ct);

            var windowStart = DateTimeOffset.UtcNow.AddHours(-Math.Max(1, windowHours)).UtcDateTime;

            var totalEvents = await ExecuteCountAsync(conn, "SELECT COUNT(*) FROM events", null, ct);
            var alertCount = await ExecuteCountAsync(conn, "SELECT COUNT(*) FROM alerts WHERE acknowledged = FALSE AND resolved_at IS NULL", null, ct);
            var narrativeCount = await ExecuteCountAsync(conn, "SELECT COUNT(*) FROM narratives WHERE status = 'active'", null, ct);
            var topEvents = await ReadTopEventsAsync(conn, windowStart, ct);
            var activeAlerts = await ReadActiveAlertsAsync(conn, ct);
            var sourceHealthJson = await _sources.BuildOverviewSourceHealthJsonAsync(ct);
            using var sourceHealthDoc = JsonDocument.Parse(sourceHealthJson);

            return JsonSerializer.Serialize(new
            {
                total_events = totalEvents,
                alert_count = alertCount,
                narrative_count = narrativeCount,
                generated_at = DateTime.UtcNow.ToString("O"),
                top_events = topEvents,
                active_alerts = activeAlerts,
                source_health = sourceHealthDoc.RootElement.Clone(),
                _served_from = "db",
            });
        }
        catch
        {
            return null;
        }
    }

    private static async Task<int> ExecuteCountAsync(System.Data.Common.DbConnection conn, string sql, Action<System.Data.Common.DbCommand>? configure, CancellationToken ct)
    {
        await using var cmd = conn.CreateCommand();
        cmd.CommandText = sql;
        configure?.Invoke(cmd);
        var value = await cmd.ExecuteScalarAsync(ct);
        return value is null or DBNull ? 0 : Convert.ToInt32(value);
    }

    private static async Task<List<object>> ReadTopEventsAsync(System.Data.Common.DbConnection conn, DateTime windowStart, CancellationToken ct)
    {
        await using var cmd = conn.CreateCommand();
        cmd.CommandText = """
            SELECT event_id, title, source, event_type, timestamp, risk_score
            FROM events
            WHERE timestamp >= @windowStart
            ORDER BY timestamp DESC NULLS LAST, ingest_time DESC NULLS LAST
            LIMIT 12
            """;

        var param = cmd.CreateParameter();
        param.ParameterName = "@windowStart";
        param.Value = windowStart;
        cmd.Parameters.Add(param);

        var rows = new List<object>();
        await using var reader = await cmd.ExecuteReaderAsync(ct);
        while (await reader.ReadAsync(ct))
        {
            rows.Add(new
            {
                event_id = reader.IsDBNull(0) ? null : reader.GetString(0),
                title = reader.IsDBNull(1) ? "--" : reader.GetString(1),
                source = reader.IsDBNull(2) ? null : reader.GetString(2),
                event_type = reader.IsDBNull(3) ? null : reader.GetString(3),
                timestamp = reader.IsDBNull(4) ? null : reader.GetDateTime(4).ToString("O"),
                risk_score = reader.IsDBNull(5) ? (double?)null : reader.GetDouble(5),
            });
        }

        return rows;
    }

    private static async Task<List<object>> ReadActiveAlertsAsync(System.Data.Common.DbConnection conn, CancellationToken ct)
    {
        await using var cmd = conn.CreateCommand();
        cmd.CommandText = """
            SELECT alert_id, title, severity, alert_type, detected_at
            FROM alerts
            WHERE acknowledged = FALSE AND resolved_at IS NULL
            ORDER BY detected_at DESC
            LIMIT 8
            """;

        var rows = new List<object>();
        await using var reader = await cmd.ExecuteReaderAsync(ct);
        while (await reader.ReadAsync(ct))
        {
            rows.Add(new
            {
                alert_id = reader.IsDBNull(0) ? null : reader.GetString(0),
                title = reader.IsDBNull(1) ? "--" : reader.GetString(1),
                severity = reader.IsDBNull(2) ? "medium" : reader.GetString(2),
                alert_type = reader.IsDBNull(3) ? null : reader.GetString(3),
                detected_at = reader.IsDBNull(4) ? null : reader.GetDateTime(4).ToString("O"),
            });
        }

        return rows;
    }

    [HttpGet("source-health")]
    [EnableRateLimiting("query")]
    public async Task<IActionResult> GetSourceHealth(CancellationToken ct = default)
    {
        var json = await _sources.BuildOverviewSourceHealthJsonAsync(ct);
        return Content(json, "application/json");
    }
}

[ApiController]
[Route("api/delta")]
[Authorize]
[Produces("application/json")]
public class DeltaController : ControllerBase
{
    private readonly IIntelligenceService _intelligence;

    public DeltaController(IIntelligenceService intelligence)
    {
        _intelligence = intelligence;
    }

    [HttpGet]
    [EnableRateLimiting("query")]
    public async Task<IActionResult> GetDelta(
        [FromQuery] int hours = 6,
        CancellationToken ct = default)
    {
        var cacheKey = $"cache:delta:{hours}";
        var json = await _intelligence.GetCachedJsonAsync(
            cacheKey,
            innerCt => _intelligence.GetPythonJsonAsync($"/delta?hours={hours}", innerCt),
            TimeSpan.FromMinutes(2),
            ct);

        if (json == null) return StatusCode(502, "Intelligence layer unavailable.");
        return Content(json, "application/json");
    }
}
