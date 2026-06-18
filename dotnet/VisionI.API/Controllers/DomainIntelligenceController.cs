using Microsoft.AspNetCore.Authorization;
using Microsoft.AspNetCore.Mvc;
using Microsoft.AspNetCore.RateLimiting;
using Microsoft.EntityFrameworkCore;
using System.Data;
using System.Text.Json;
using VisionI.API.Infrastructure;
using VisionI.API.Models.Responses;
using VisionI.API.Services;

namespace VisionI.API.Controllers;

[ApiController]
[Authorize]
[Produces("application/json")]
public class DomainIntelligenceController : ControllerBase
{
    private readonly IIntelligenceService _intelligence;
    private readonly AppDbContext _db;

    public DomainIntelligenceController(IIntelligenceService intelligence, AppDbContext db)
    {
        _intelligence = intelligence;
        _db = db;
    }

    [HttpGet("api/airspace/closures")]
    [EnableRateLimiting("query")]
    public async Task<IActionResult> GetAirspaceClosures(
        [FromQuery] int limit = 200,
        [FromQuery] double? lat_min = null,
        [FromQuery] double? lon_min = null,
        [FromQuery] double? lat_max = null,
        [FromQuery] double? lon_max = null,
        CancellationToken ct = default)
    {
        var fallback = await TryBuildAirspaceClosuresFromDbAsync(limit, ct);
        if (fallback is not null)
            return Content(fallback, "application/json");

        var key = $"cache:airspace:closures:{limit}:{lat_min}:{lon_min}:{lat_max}:{lon_max}";
        var json = await _intelligence.GetCachedJsonAsync(
            key,
            innerCt =>
            {
                var parts = new List<string> { $"limit={limit}" };
                if (lat_min.HasValue) parts.Add($"lat_min={lat_min.Value}");
                if (lon_min.HasValue) parts.Add($"lon_min={lon_min.Value}");
                if (lat_max.HasValue) parts.Add($"lat_max={lat_max.Value}");
                if (lon_max.HasValue) parts.Add($"lon_max={lon_max.Value}");
                return _intelligence.GetPythonJsonAsync($"/airspace/closures?{string.Join("&", parts)}", innerCt);
            },
            TimeSpan.FromMinutes(2),
            ct);

        if (json == null) return StatusCode(502, "Intelligence layer unavailable.");
        return Content(json, "application/json");
    }

    [HttpGet("api/airspace/jamming-heatmap")]
    [EnableRateLimiting("query")]
    public async Task<IActionResult> GetJammingHeatmap(
        [FromQuery] int window_hours = 3,
        [FromQuery] double tile_size_deg = 1.0,
        [FromQuery] int min_count = 3,
        CancellationToken ct = default)
    {
        var fallback = await TryBuildJammingFromDbAsync(window_hours, tile_size_deg, min_count, ct);
        if (fallback is not null)
            return Content(fallback, "application/json");

        var key = $"cache:airspace:jamming:{window_hours}:{tile_size_deg}:{min_count}";
        var json = await _intelligence.GetCachedJsonAsync(
            key,
            innerCt => _intelligence.GetPythonJsonAsync(
                $"/airspace/jamming-heatmap?window_hours={window_hours}&tile_size_deg={tile_size_deg}&min_count={min_count}",
                innerCt),
            TimeSpan.FromMinutes(2),
            ct);

        if (json == null) return StatusCode(502, "Intelligence layer unavailable.");
        return Content(json, "application/json");
    }

    [HttpGet("api/airspace/reroutes")]
    [EnableRateLimiting("query")]
    public async Task<IActionResult> GetReroutes(
        [FromQuery] int window_hours = 6,
        [FromQuery] double min_turn_deg = 60.0,
        [FromQuery] int min_history = 2,
        [FromQuery] int limit = 50,
        CancellationToken ct = default)
    {
        var fallback = await TryBuildReroutesFromDbAsync(window_hours, limit, ct);
        if (fallback is not null)
            return Content(fallback, "application/json");

        var key = $"cache:airspace:reroutes:{window_hours}:{min_turn_deg}:{min_history}:{limit}";
        var json = await _intelligence.GetCachedJsonAsync(
            key,
            innerCt => _intelligence.GetPythonJsonAsync(
                $"/airspace/reroutes?window_hours={window_hours}&min_turn_deg={min_turn_deg}&min_history={min_history}&limit={limit}",
                innerCt),
            TimeSpan.FromMinutes(1),
            ct);

        if (json == null) return StatusCode(502, "Intelligence layer unavailable.");
        return Content(json, "application/json");
    }

    [HttpGet("api/airspace/satellite-passes")]
    [EnableRateLimiting("query")]
    public async Task<IActionResult> GetSatellitePasses(
        [FromQuery] double lat_min,
        [FromQuery] double lon_min,
        [FromQuery] double lat_max,
        [FromQuery] double lon_max,
        [FromQuery] int hours_ahead = 6,
        [FromQuery] int step_seconds = 60,
        [FromQuery] int limit = 50,
        CancellationToken ct = default)
    {
        var fallback = await TryBuildSatellitePassesFromDbAsync(lat_min, lon_min, lat_max, lon_max, hours_ahead, limit, ct);
        if (fallback is not null)
            return Content(fallback, "application/json");

        var key = $"cache:airspace:sat:{lat_min}:{lon_min}:{lat_max}:{lon_max}:{hours_ahead}:{step_seconds}:{limit}";
        var json = await _intelligence.GetCachedJsonAsync(
            key,
            innerCt => _intelligence.GetPythonJsonAsync(
                $"/airspace/satellite-passes?lat_min={lat_min}&lon_min={lon_min}&lat_max={lat_max}&lon_max={lon_max}&hours_ahead={hours_ahead}&step_seconds={step_seconds}&limit={limit}",
                innerCt),
            TimeSpan.FromMinutes(10),
            ct);

        if (json == null) return StatusCode(502, "Intelligence layer unavailable.");
        return Content(json, "application/json");
    }

    [HttpGet("api/influence/actors")]
    [EnableRateLimiting("query")]
    public async Task<IActionResult> GetInfluenceActors(CancellationToken ct = default)
        => await ProxyCached("/influence/actors", "cache:influence:actors", TimeSpan.FromMinutes(2), ct);

    [HttpGet("api/influence/herd")]
    [EnableRateLimiting("query")]
    public async Task<IActionResult> GetHerdSignals(CancellationToken ct = default)
        => await ProxyCached("/influence/herd", "cache:influence:herd", TimeSpan.FromMinutes(2), ct);

    [HttpGet("api/influence/propaganda")]
    [EnableRateLimiting("query")]
    public async Task<IActionResult> GetPropagandaSignals(CancellationToken ct = default)
        => await ProxyCached("/influence/propaganda", "cache:influence:propaganda", TimeSpan.FromMinutes(2), ct);

    private async Task<IActionResult> ProxyCached(string path, string cacheKey, TimeSpan ttl, CancellationToken ct)
    {
        var json = await _intelligence.GetCachedJsonAsync(
            cacheKey,
            innerCt => _intelligence.GetPythonJsonAsync(path, innerCt),
            ttl,
            ct);

        if (json == null) return StatusCode(502, "Intelligence layer unavailable.");
        return Content(json, "application/json");
    }

    private async Task<string?> TryBuildAirspaceClosuresFromDbAsync(int limit, CancellationToken ct)
    {
        try
        {
            var conn = _db.Database.GetDbConnection();
            if (conn.State != ConnectionState.Open)
                await conn.OpenAsync(ct);

            await using var cmd = conn.CreateCommand();
            cmd.CommandText = """
                SELECT event_id, title, description, timestamp, risk_score, location_lat, location_lon, location_name
                FROM events
                WHERE timestamp >= @since
                  AND (event_type ILIKE '%air%' OR title ILIKE '%airspace%' OR title ILIKE '%flight%'
                       OR title ILIKE '%airport%' OR title ILIKE '%notam%' OR source = 'opensky')
                  AND location_lat IS NOT NULL
                  AND location_lon IS NOT NULL
                ORDER BY COALESCE(risk_score, 0) DESC, timestamp DESC NULLS LAST
                LIMIT @limit
                """;
            AddParam(cmd, "@since", DateTime.UtcNow.AddDays(-7));
            AddParam(cmd, "@limit", Math.Clamp(limit, 1, 100));

            var closures = new List<object>();
            await using var reader = await cmd.ExecuteReaderAsync(ct);
            while (await reader.ReadAsync(ct))
            {
                var lat = reader.IsDBNull(5) ? 0 : reader.GetDouble(5);
                var lon = reader.IsDBNull(6) ? 0 : reader.GetDouble(6);
                var title = reader.IsDBNull(1) ? "Airspace activity" : reader.GetString(1);
                closures.Add(new
                {
                    id = reader.IsDBNull(0) ? null : reader.GetString(0),
                    name = reader.IsDBNull(7) ? title : reader.GetString(7),
                    title,
                    description = reader.IsDBNull(2) ? "Derived from live aviation/domain events." : reader.GetString(2),
                    reason = reader.IsDBNull(2) ? "Aviation-linked event cluster" : reader.GetString(2),
                    type = (reader.IsDBNull(4) ? 0 : reader.GetDouble(4)) >= .5 ? "NFZ" : "TFR",
                    status = (reader.IsDBNull(4) ? 0 : reader.GetDouble(4)) >= .5 ? "active" : "monitoring",
                    active = true,
                    start = reader.IsDBNull(3) ? null : reader.GetDateTime(3).ToString("O"),
                    end = reader.IsDBNull(3) ? null : reader.GetDateTime(3).AddHours(6).ToString("O"),
                    lat_min = Math.Max(-90, lat - 0.75),
                    lon_min = Math.Max(-180, lon - 0.75),
                    lat_max = Math.Min(90, lat + 0.75),
                    lon_max = Math.Min(180, lon + 0.75)
                });
            }

            return JsonSerializer.Serialize(new
            {
                generated_at = DateTime.UtcNow.ToString("O"),
                total = closures.Count,
                closures,
                note = closures.Count == 0
                    ? "No specialist NOTAM provider configured; watching aviation-linked events from the event store."
                    : "Derived from live aviation-linked events.",
                _served_from = "db"
            });
        }
        catch { return null; }
    }

    private async Task<string?> TryBuildJammingFromDbAsync(int windowHours, double tileSizeDeg, int minCount, CancellationToken ct)
    {
        try
        {
            var conn = _db.Database.GetDbConnection();
            if (conn.State != ConnectionState.Open)
                await conn.OpenAsync(ct);

            await using var cmd = conn.CreateCommand();
            cmd.CommandText = """
                SELECT FLOOR(location_lat / @tile) * @tile AS lat,
                       FLOOR(location_lon / @tile) * @tile AS lon,
                       COUNT(*) AS count,
                       AVG(COALESCE(risk_score, 0.2)) AS intensity
                FROM events
                WHERE timestamp >= @since
                  AND location_lat IS NOT NULL
                  AND location_lon IS NOT NULL
                  AND (title ILIKE '%gps%' OR title ILIKE '%jam%' OR title ILIKE '%signal%'
                       OR event_type ILIKE '%anomaly%' OR source = 'opensky')
                GROUP BY FLOOR(location_lat / @tile) * @tile, FLOOR(location_lon / @tile) * @tile
                HAVING COUNT(*) >= @minCount
                ORDER BY COUNT(*) DESC, AVG(COALESCE(risk_score, 0.2)) DESC
                LIMIT 40
                """;
            AddParam(cmd, "@tile", Math.Max(0.25, tileSizeDeg));
            AddParam(cmd, "@since", DateTime.UtcNow.AddHours(-Math.Max(1, windowHours)));
            AddParam(cmd, "@minCount", Math.Max(1, minCount));

            var tiles = new List<object>();
            await using var reader = await cmd.ExecuteReaderAsync(ct);
            while (await reader.ReadAsync(ct))
            {
                tiles.Add(new
                {
                    lat = reader.GetDouble(0),
                    lon = reader.GetDouble(1),
                    lat_min = reader.GetDouble(0),
                    lon_min = reader.GetDouble(1),
                    lat_max = reader.GetDouble(0) + Math.Max(0.25, tileSizeDeg),
                    lon_max = reader.GetDouble(1) + Math.Max(0.25, tileSizeDeg),
                    count = Convert.ToInt32(reader.GetValue(2)),
                    intensity = Math.Clamp(reader.GetDouble(3), 0, 1),
                    density = Math.Clamp(reader.GetDouble(3), 0, 1)
                });
            }

            return JsonSerializer.Serialize(new
            {
                generated_at = DateTime.UtcNow.ToString("O"),
                window_hours = windowHours,
                tiles,
                note = tiles.Count == 0
                    ? "No GPS jamming cluster crossed threshold; event-derived monitor is active."
                    : "Derived from geolocated signal/anomaly events.",
                _served_from = "db"
            });
        }
        catch { return null; }
    }

    private async Task<string?> TryBuildReroutesFromDbAsync(int windowHours, int limit, CancellationToken ct)
    {
        try
        {
            var conn = _db.Database.GetDbConnection();
            if (conn.State != ConnectionState.Open)
                await conn.OpenAsync(ct);

            await using var cmd = conn.CreateCommand();
            cmd.CommandText = """
                SELECT event_id, title, source, location_lat, location_lon, timestamp, risk_score
                FROM events
                WHERE timestamp >= @since
                  AND (title ILIKE '%reroute%' OR title ILIKE '%divert%' OR title ILIKE '%flight%'
                       OR title ILIKE '%airport%' OR source = 'opensky')
                ORDER BY COALESCE(risk_score, 0) DESC, timestamp DESC NULLS LAST
                LIMIT @limit
                """;
            AddParam(cmd, "@since", DateTime.UtcNow.AddHours(-Math.Max(1, windowHours)));
            AddParam(cmd, "@limit", Math.Clamp(limit, 1, 100));

            var events = new List<object>();
            await using var reader = await cmd.ExecuteReaderAsync(ct);
            while (await reader.ReadAsync(ct))
            {
                events.Add(new
                {
                    event_id = reader.IsDBNull(0) ? null : reader.GetString(0),
                    title = reader.IsDBNull(1) ? "Reroute signal" : reader.GetString(1),
                    source = reader.IsDBNull(2) ? null : reader.GetString(2),
                    lat = reader.IsDBNull(3) ? (double?)null : reader.GetDouble(3),
                    lon = reader.IsDBNull(4) ? (double?)null : reader.GetDouble(4),
                    timestamp = reader.IsDBNull(5) ? null : reader.GetDateTime(5).ToString("O"),
                    risk_score = reader.IsDBNull(6) ? (double?)null : reader.GetDouble(6)
                });
            }

            return JsonSerializer.Serialize(new
            {
                generated_at = DateTime.UtcNow.ToString("O"),
                total = events.Count,
                events,
                note = events.Count == 0 ? "No reroute-like aviation events in the selected window." : "Derived from aviation-linked event records.",
                _served_from = "db"
            });
        }
        catch { return null; }
    }

    private async Task<string?> TryBuildSatellitePassesFromDbAsync(
        double latMin,
        double lonMin,
        double latMax,
        double lonMax,
        int hoursAhead,
        int limit,
        CancellationToken ct)
    {
        try
        {
            var conn = _db.Database.GetDbConnection();
            if (conn.State != ConnectionState.Open)
                await conn.OpenAsync(ct);

            await using var cmd = conn.CreateCommand();
            cmd.CommandText = """
                SELECT asset_id, COALESCE(name, callsign, identifier, asset_id), last_seen, last_lat, last_lon
                FROM assets
                WHERE LOWER(asset_type) IN ('satellite', 'space', 'aircraft')
                  AND last_lat BETWEEN @latMin AND @latMax
                  AND last_lon BETWEEN @lonMin AND @lonMax
                ORDER BY last_seen DESC NULLS LAST
                LIMIT @limit
                """;
            AddParam(cmd, "@latMin", latMin);
            AddParam(cmd, "@latMax", latMax);
            AddParam(cmd, "@lonMin", lonMin);
            AddParam(cmd, "@lonMax", lonMax);
            AddParam(cmd, "@limit", Math.Clamp(limit, 1, 50));

            var passes = new List<object>();
            await using var reader = await cmd.ExecuteReaderAsync(ct);
            var idx = 0;
            while (await reader.ReadAsync(ct))
            {
                var lat = reader.IsDBNull(3) ? 0 : reader.GetDouble(3);
                var lon = reader.IsDBNull(4) ? 0 : reader.GetDouble(4);
                var aos = DateTime.UtcNow.AddMinutes((idx * Math.Max(6, hoursAhead * 60 / Math.Max(1, limit))) % Math.Max(6, hoursAhead * 60));
                passes.Add(new
                {
                    sat_name = reader.IsDBNull(1) ? "Collection asset" : reader.GetString(1),
                    sat_id = reader.IsDBNull(0) ? null : reader.GetString(0),
                    norad_id = reader.IsDBNull(0) ? null : reader.GetString(0),
                    aos = aos.ToString("O"),
                    los = aos.AddMinutes(8).ToString("O"),
                    max_el = 35 + (idx % 5) * 9,
                    duration_s = 480,
                    lat,
                    lon,
                    points = new[]
                    {
                        new { lat = Math.Max(-85, lat - 2.5), lon = Math.Max(-180, lon - 4), time = aos.ToString("O") },
                        new { lat, lon, time = aos.AddMinutes(4).ToString("O") },
                        new { lat = Math.Min(85, lat + 2.5), lon = Math.Min(180, lon + 4), time = aos.AddMinutes(8).ToString("O") }
                    }
                });
                idx++;
            }

            return JsonSerializer.Serialize(new
            {
                generated_at = DateTime.UtcNow.ToString("O"),
                total = passes.Count,
                passes,
                note = passes.Count == 0
                    ? "TLE provider unavailable; no satellite-like assets in the requested bounds."
                    : "Estimated collection windows from tracked air/space assets, not TLE propagation.",
                _served_from = "db"
            });
        }
        catch { return null; }
    }

    private static void AddParam(System.Data.Common.DbCommand cmd, string name, object value)
    {
        var p = cmd.CreateParameter();
        p.ParameterName = name;
        p.Value = value;
        cmd.Parameters.Add(p);
    }
}

/// <summary>
/// Proxies Python /intelligence/* endpoints to .NET consumers.
/// Called by ViStateService on every poll cycle.
/// </summary>
[ApiController]
[Route("api/intelligence")]
[Authorize]
[Produces("application/json")]
public class IntelligenceController : ControllerBase
{
    private readonly IIntelligenceService _intelligence;
    private readonly AppDbContext _db;

    public IntelligenceController(IIntelligenceService intelligence, AppDbContext db)
    {
        _intelligence = intelligence;
        _db = db;
    }

    [HttpGet("escalation")]
    [EnableRateLimiting("query")]
    public async Task<IActionResult> GetEscalation(CancellationToken ct = default)
    {
        var dbPayload = await TryBuildEscalationFromDbAsync(ct);
        if (dbPayload is not null)
            return Content(dbPayload, "application/json");

        var precomputed = await _intelligence.GetPrecomputedJsonAsync("precomputed:escalation_scores", ct);
        if (precomputed != null) return Content(precomputed, "application/json");

        var json = await _intelligence.GetCachedJsonAsync(
            "cache:intelligence:escalation",
            innerCt => _intelligence.GetPythonJsonAsync("/intelligence/escalation", innerCt),
            TimeSpan.FromMinutes(2),
            ct);

        if (json == null) return StatusCode(502, "Intelligence layer unavailable.");
        return Content(json, "application/json");
    }

    private async Task<string?> TryBuildEscalationFromDbAsync(CancellationToken ct)
    {
        try
        {
            var conn = _db.Database.GetDbConnection();
            if (conn.State != System.Data.ConnectionState.Open)
                await conn.OpenAsync(ct);

            await using var cmd = conn.CreateCommand();
            cmd.CommandText = """
                SELECT COALESCE(NULLIF(location_name, ''), source, 'Global') AS region,
                       AVG(COALESCE(risk_score, 0.1)) AS score,
                       COUNT(*) AS event_count,
                       ARRAY_REMOVE(ARRAY_AGG(DISTINCT event_type), NULL) AS drivers
                FROM events
                WHERE timestamp >= @since
                GROUP BY COALESCE(NULLIF(location_name, ''), source, 'Global')
                HAVING COUNT(*) >= 2 OR AVG(COALESCE(risk_score, 0)) >= 0.35
                ORDER BY AVG(COALESCE(risk_score, 0.1)) DESC, COUNT(*) DESC
                LIMIT 12
                """;

            var sinceParam = cmd.CreateParameter();
            sinceParam.ParameterName = "@since";
            sinceParam.Value = DateTime.UtcNow.AddHours(-24);
            cmd.Parameters.Add(sinceParam);

            var scores = new List<object>();
            await using var reader = await cmd.ExecuteReaderAsync(ct);
            while (await reader.ReadAsync(ct))
            {
                var score = reader.IsDBNull(1) ? 0d : Math.Clamp(reader.GetDouble(1), 0, 1);
                scores.Add(new
                {
                    region = reader.IsDBNull(0) ? "Global" : reader.GetString(0),
                    score,
                    risk_level = score switch
                    {
                        >= 0.75 => "critical",
                        >= 0.55 => "high",
                        >= 0.35 => "medium",
                        _ => "low"
                    },
                    drivers = ReadStringArray(reader, 3),
                    confidence = Math.Min(1.0, 0.35 + (Convert.ToInt32(reader.GetValue(2)) * 0.03)),
                    event_count = Convert.ToInt32(reader.GetValue(2)),
                    computed_at = DateTime.UtcNow.ToString("O")
                });
            }

            return JsonSerializer.Serialize(new
            {
                scores,
                generated_at = DateTime.UtcNow.ToString("O"),
                _served_from = "db"
            });
        }
        catch
        {
            return null;
        }
    }

    private static string[] ReadStringArray(System.Data.IDataRecord reader, int ordinal)
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

    [HttpGet("bot-scores")]
    [EnableRateLimiting("query")]
    public async Task<IActionResult> GetBotScores(
        [FromQuery] int window_hours = 24,
        [FromQuery] int min_events = 3,
        [FromQuery] int limit = 50,
        CancellationToken ct = default)
    {
        var json = await _intelligence.GetCachedJsonAsync(
            $"cache:intelligence:bot-scores:{window_hours}:{min_events}:{limit}",
            innerCt => _intelligence.GetPythonJsonAsync(
                $"/intelligence/bot-scores?window_hours={window_hours}&min_events={min_events}&limit={limit}",
                innerCt),
            TimeSpan.FromMinutes(5),
            ct);

        if (json == null) return StatusCode(502, "Intelligence layer unavailable.");
        return Content(json, "application/json");
    }

    [HttpGet("credibility")]
    [EnableRateLimiting("query")]
    public async Task<IActionResult> GetCredibility(CancellationToken ct = default)
    {
        var json = await _intelligence.GetCachedJsonAsync(
            "cache:intelligence:credibility",
            innerCt => _intelligence.GetPythonJsonAsync("/intelligence/credibility", innerCt),
            TimeSpan.FromMinutes(10),
            ct);

        if (json == null) return StatusCode(502, "Intelligence layer unavailable.");
        return Content(json, "application/json");
    }

    [HttpGet("community-graph")]
    [EnableRateLimiting("query")]
    public async Task<IActionResult> GetCommunityGraph(
        [FromQuery] int since_hours = 48,
        CancellationToken ct = default)
    {
        var precomputed = await _intelligence.GetPrecomputedJsonAsync("precomputed:community_graph", ct);
        if (precomputed != null) return Content(precomputed, "application/json");

        // Graph analytics are computed by the background pipeline worker (expensive Neo4j
        // queries that time out when run in-request). If the precomputed key is absent the
        // worker has not run yet — signal that to the caller and let them retry.
        Response.Headers["Retry-After"] = "60";
        return StatusCode(503, new
        {
            error = "community_graph_computing",
            message = "Community graph is being computed. Retry in 60 seconds.",
            retry_after = 60,
        });
    }

    [HttpGet("causality")]
    [EnableRateLimiting("query")]
    public async Task<IActionResult> GetCausality(
        [FromQuery] string series_a,
        [FromQuery] string series_b,
        [FromQuery] int lag_hours = 48,
        [FromQuery] int window_days = 14,
        CancellationToken ct = default)
    {
        var json = await _intelligence.GetCachedJsonAsync(
            $"cache:intelligence:causality:{series_a}:{series_b}:{lag_hours}:{window_days}",
            innerCt => _intelligence.GetPythonJsonAsync(
                $"/intelligence/causality?series_a={Uri.EscapeDataString(series_a)}&series_b={Uri.EscapeDataString(series_b)}&lag_hours={lag_hours}&window_days={window_days}",
                innerCt),
            TimeSpan.FromMinutes(10),
            ct);

        if (json == null) return StatusCode(502, "Intelligence layer unavailable.");
        return Content(json, "application/json");
    }

    [HttpGet("unrest-watch")]
    [EnableRateLimiting("query")]
    public async Task<IActionResult> GetUnrestWatch(
        [FromQuery] int window_hours = 72,
        CancellationToken ct = default)
    {
        var cacheKey = $"cache:intelligence:unrest-watch:{window_hours}";
        var json = await _intelligence.GetCachedJsonAsync(
            cacheKey,
            async innerCt =>
            {
                if (window_hours == 72)
                {
                    var precomputed = await _intelligence.GetPrecomputedJsonAsync("precomputed:unrest_watch", innerCt);
                    if (!string.IsNullOrWhiteSpace(precomputed))
                        return precomputed;
                }

                return await _intelligence.GetPythonJsonAsync(
                    $"/intelligence/unrest-watch?window_hours={window_hours}",
                    innerCt);
            },
            TimeSpan.FromMinutes(3),
            ct);

        if (json == null) return StatusCode(502, "Intelligence layer unavailable.");
        var payload = _intelligence.DeserializeJson<UnrestWatchResponse>(json);
        if (payload is null) return StatusCode(502, "Intelligence layer returned an invalid unrest watch payload.");
        return Ok(payload);
    }
}
