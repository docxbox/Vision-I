using Microsoft.AspNetCore.Authorization;
using Microsoft.AspNetCore.Mvc;
using Microsoft.EntityFrameworkCore;
using System.Data;
using System.Text.Json;
using VisionI.API.Infrastructure;
using VisionI.API.Services;

namespace VisionI.API.Controllers;

[ApiController]
[Route("api/threatboard")]
[Authorize]
[Produces("application/json")]
public class ThreatBoardController : ControllerBase
{
    private readonly IIntelligenceService _intelligence;
    private readonly AppDbContext _db;

    public ThreatBoardController(IIntelligenceService intelligence, AppDbContext db)
    {
        _intelligence = intelligence;
        _db = db;
    }

    [HttpGet]
    public async Task<IActionResult> GetThreatBoard(
        [FromQuery] int hours = 24,
        [FromQuery] int limit = 30,
        CancellationToken ct = default)
    {
        var dbPayload = await TryBuildThreatBoardFromDbAsync(hours, limit, ct);
        if (dbPayload is not null)
            return Content(dbPayload, "application/json");

        var key = $"cache:threatboard:{hours}:{limit}";
        var json = await _intelligence.GetCachedJsonAsync(
            key,
            innerCt => _intelligence.GetPythonJsonAsync(
                $"/threatboard?hours={hours}&limit={limit}", innerCt),
            TimeSpan.FromMinutes(3),
            ct);

        if (json == null) return StatusCode(502, new { error = "Intelligence layer unavailable." });
        return Content(json, "application/json");
    }

    private async Task<string?> TryBuildThreatBoardFromDbAsync(int hours, int limit, CancellationToken ct)
    {
        try
        {
            var conn = _db.Database.GetDbConnection();
            if (conn.State != ConnectionState.Open)
                await conn.OpenAsync(ct);

            var since = DateTime.UtcNow.AddHours(-Math.Max(1, hours));
            await using var cmd = conn.CreateCommand();
            cmd.CommandText = """
                WITH scoped AS (
                    SELECT
                        COALESCE(NULLIF(location_name, ''), NULLIF((tags->>0), ''), 'Global') AS zone,
                        event_type,
                        title,
                        actors,
                        timestamp,
                        COALESCE(risk_score, 0.1) AS risk_score
                    FROM events
                    WHERE timestamp >= @since
                      AND location_lat IS NOT NULL
                      AND location_lon IS NOT NULL
                      AND COALESCE(source, '') NOT IN ('ais', 'opensky')
                      AND COALESCE(event_type, '') NOT ILIKE '%asset%'
                      AND COALESCE(event_type, '') NOT ILIKE '%maritime%'
                      AND COALESCE(event_type, '') NOT ILIKE '%aviation%'
                ),
                rolled AS (
                    SELECT zone,
                           COUNT(*) AS event_count,
                           AVG(risk_score) AS avg_score,
                           COUNT(*) FILTER (WHERE risk_score >= 0.7) AS alert_count,
                           COUNT(*) FILTER (WHERE timestamp >= @recentSince) AS recent_count,
                           ARRAY_REMOVE(ARRAY_AGG(DISTINCT event_type), NULL) AS top_signals,
                           ARRAY_REMOVE(ARRAY_AGG(DISTINCT title), NULL) AS sample_titles
                    FROM scoped
                    GROUP BY zone
                )
                SELECT zone,
                       event_count,
                       LEAST(1.0, avg_score + (LN(event_count + 1) * 0.045) + (alert_count * 0.08)) AS score,
                       alert_count,
                       top_signals,
                       recent_count,
                       sample_titles
                FROM rolled
                WHERE event_count >= 2 OR avg_score >= 0.35 OR alert_count > 0
                ORDER BY score DESC, recent_count DESC, event_count DESC
                LIMIT @limit
                """;

            var sinceParam = cmd.CreateParameter();
            sinceParam.ParameterName = "@since";
            sinceParam.Value = since;
            cmd.Parameters.Add(sinceParam);

            var recentParam = cmd.CreateParameter();
            recentParam.ParameterName = "@recentSince";
            recentParam.Value = DateTime.UtcNow.AddHours(-Math.Max(1, hours / 3));
            cmd.Parameters.Add(recentParam);

            var limitParam = cmd.CreateParameter();
            limitParam.ParameterName = "@limit";
            limitParam.Value = Math.Clamp(limit, 1, 50);
            cmd.Parameters.Add(limitParam);

            var zones = new List<object>();
            var summary = new Dictionary<string, int>(StringComparer.OrdinalIgnoreCase)
            {
                ["critical"] = 0,
                ["high"] = 0,
                ["medium"] = 0,
                ["low"] = 0,
            };

            await using var reader = await cmd.ExecuteReaderAsync(ct);
            while (await reader.ReadAsync(ct))
            {
                var name = reader.IsDBNull(0) ? "Global" : reader.GetString(0);
                var eventCount = Convert.ToInt32(reader.GetValue(1));
                var score = reader.IsDBNull(2) ? 0 : Math.Clamp(reader.GetDouble(2), 0, 1);
                var alertCount = Convert.ToInt32(reader.GetValue(3));
                var recentCount = Convert.ToInt32(reader.GetValue(5));
                var level = score switch
                {
                    >= 0.75 => "critical",
                    >= 0.55 => "high",
                    >= 0.35 => "medium",
                    _ => "low"
                };
                summary[level] = summary.GetValueOrDefault(level) + 1;

                zones.Add(new
                {
                    name,
                    threat_level = level,
                    dominant_severity = level,
                    score,
                    trend = recentCount >= Math.Max(1, eventCount / 2) ? "rising" : recentCount == 0 ? "cooling" : "stable",
                    alert_count = alertCount,
                    narrative_count = Math.Max(0, ReadStringArray(reader, 4).Length - 1),
                    event_count = eventCount,
                    top_signals = ReadStringArray(reader, 4),
                    top_actors = ReadStringArray(reader, 6).Take(3).ToArray(),
                    location = name
                });
            }

            var overall = summary["critical"] > 0 ? "critical"
                : summary["high"] > 0 ? "high"
                : summary["medium"] > 0 ? "medium"
                : zones.Count > 0 ? "low"
                : "monitoring";

            return JsonSerializer.Serialize(new
            {
                generated_at = DateTime.UtcNow.ToString("O"),
                hours,
                overall_level = overall,
                zones,
                summary,
                db_available = true,
                _served_from = "db"
            });
        }
        catch
        {
            return null;
        }
    }

    private static string[] ReadStringArray(IDataRecord reader, int ordinal)
    {
        if (reader.IsDBNull(ordinal))
            return Array.Empty<string>();

        return reader.GetValue(ordinal) switch
        {
            string[] values => values.Where(v => !string.IsNullOrWhiteSpace(v)).Take(5).ToArray(),
            IEnumerable<string> values => values.Where(v => !string.IsNullOrWhiteSpace(v)).Take(5).ToArray(),
            _ => Array.Empty<string>()
        };
    }

    [HttpGet("summary")]
    public async Task<IActionResult> GetThreatBoardSummary(CancellationToken ct = default)
    {
        var key = "cache:threatboard:summary";
        var json = await _intelligence.GetCachedJsonAsync(
            key,
            innerCt => _intelligence.GetPythonJsonAsync("/threatboard/summary", innerCt),
            TimeSpan.FromMinutes(5),
            ct);

        if (json == null) return StatusCode(502, new { error = "Intelligence layer unavailable." });
        return Content(json, "application/json");
    }
}
