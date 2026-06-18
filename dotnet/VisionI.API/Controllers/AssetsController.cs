using System.Data;
using System.Text.Json;
using Microsoft.AspNetCore.Authorization;
using Microsoft.AspNetCore.Mvc;
using Microsoft.AspNetCore.RateLimiting;
using Microsoft.EntityFrameworkCore;
using VisionI.API.Infrastructure;
using VisionI.API.Services;

namespace VisionI.API.Controllers;

/// <summary>
/// Asset tracking endpoints - aircraft, vessels, facilities.
/// Proxies to the Python /assets endpoints.
/// </summary>
[ApiController]
[Route("api/assets")]
[Authorize]
[Produces("application/json")]
public class AssetsController : ControllerBase
{
    private readonly IIntelligenceService _intelligence;
    private readonly AppDbContext _db;

    public AssetsController(IIntelligenceService intelligence, AppDbContext db)
    {
        _intelligence = intelligence;
        _db = db;
    }

    /// <summary>List tracked assets - cached 5 minutes.</summary>
    [HttpGet]
    [EnableRateLimiting("query")]
    public async Task<IActionResult> GetAssets(
        [FromQuery] string? asset_type = null,
        [FromQuery] int limit = 50,
        CancellationToken ct = default)
    {
        limit = Math.Clamp(limit, 1, 30000);
        var key = $"cache:assets:{asset_type}:{limit}";
        var json = await _intelligence.ReadCacheJsonAsync(key, ct);

        if (json == null)
        {
            var path = $"/assets?limit={limit}";
            if (!string.IsNullOrWhiteSpace(asset_type))
            {
                path += $"&asset_type={Uri.EscapeDataString(asset_type)}";
            }

            json = await _intelligence.GetPythonJsonAsync(path, ct);
            if (json != null)
            {
                using var payload = JsonDocument.Parse(json);
                await _intelligence.SetCachedJsonAsync(key, json, ResolveAssetsTtl(payload.RootElement), ct);
            }
        }

        if (json == null) return StatusCode(502, "Intelligence layer unavailable.");
        return Content(json, "application/json");
    }

    /// <summary>Asset counts by type.</summary>
    [HttpGet("counts")]
    [EnableRateLimiting("query")]
    public async Task<IActionResult> GetAssetCounts(CancellationToken ct = default)
    {
        var fast = await TryGetAssetCountsFromDbAsync(ct);
        if (fast is not null)
            return Content(fast, "application/json");

        var key = "cache:assets:counts";
        var json = await _intelligence.GetCachedJsonAsync(
            key,
            innerCt => _intelligence.GetPythonJsonAsync("/assets/counts", innerCt),
            TimeSpan.FromMinutes(5),
            ct);

        if (json == null) return StatusCode(502, "Intelligence layer unavailable.");
        return Content(json, "application/json");
    }

    private async Task<string?> TryGetAssetCountsFromDbAsync(CancellationToken ct)
    {
        try
        {
            await using var conn = _db.Database.GetDbConnection();
            if (conn.State != ConnectionState.Open)
                await conn.OpenAsync(ct);

            await using var cmd = conn.CreateCommand();
            cmd.CommandText = """
                SELECT asset_type, COUNT(*)::int AS total
                FROM assets
                GROUP BY asset_type
                ORDER BY asset_type
                """;

            var counts = new Dictionary<string, int>(StringComparer.OrdinalIgnoreCase);
            var total = 0;

            await using var reader = await cmd.ExecuteReaderAsync(ct);
            while (await reader.ReadAsync(ct))
            {
                var assetType = reader.IsDBNull(0) ? "unknown" : reader.GetString(0);
                var count = reader.IsDBNull(1) ? 0 : reader.GetInt32(1);
                counts[assetType] = count;
                total += count;
            }

            return _intelligence.SerializeJson(new
            {
                counts,
                total,
                served_from = "db",
            });
        }
        catch
        {
            return null;
        }
    }

    /// <summary>Assets within the current map viewport — short-cached on a coarse bbox key.</summary>
    [HttpGet("in-bounds")]
    [EnableRateLimiting("query")]
    public async Task<IActionResult> GetAssetsInBounds(
        [FromQuery] double min_lat,
        [FromQuery] double max_lat,
        [FromQuery] double min_lon,
        [FromQuery] double max_lon,
        [FromQuery] string? asset_type = null,
        [FromQuery] int limit = 2000,
        CancellationToken ct = default)
    {
        limit = Math.Clamp(limit, 1, 10000);
        var normalizedType = string.IsNullOrWhiteSpace(asset_type) ? null : asset_type.Trim();
        // Snap to 1-degree grid: small pans reuse the same cache entry (matches Python TTL).
        var key = $"assets:bounds:v1:{normalizedType ?? "all"}:{limit}:" +
                  $"{Math.Round(min_lat)}:{Math.Round(max_lat)}:{Math.Round(min_lon)}:{Math.Round(max_lon)}";

        var cached = await _intelligence.ReadCacheJsonAsync(key, ct);
        if (!string.IsNullOrWhiteSpace(cached))
            return Content(cached, "application/json");

        var path = $"/assets/in-bounds?min_lat={min_lat}&max_lat={max_lat}&min_lon={min_lon}&max_lon={max_lon}&limit={limit}";
        if (normalizedType is not null)
            path += $"&asset_type={Uri.EscapeDataString(normalizedType)}";

        var json = await _intelligence.GetPythonJsonAsync(path, ct);
        if (json == null) return StatusCode(502, "Intelligence layer unavailable.");
        await _intelligence.SetCachedJsonAsync(key, json, TimeSpan.FromSeconds(30), ct);
        return Content(json, "application/json");
    }

    /// <summary>Single asset detail with track history.</summary>
    [HttpGet("{assetId}")]
    [EnableRateLimiting("query")]
    public async Task<IActionResult> GetAsset(string assetId, CancellationToken ct = default)
    {
        var json = await _intelligence.GetPythonJsonAsync($"/assets/{Uri.EscapeDataString(assetId)}", ct);
        if (json == null) return NotFound();
        return Content(json, "application/json");
    }

    [HttpGet("snapshot/latest")]
    [EnableRateLimiting("query")]
    public async Task<IActionResult> GetLatestSnapshot(
        [FromQuery] string? asset_type = null,
        [FromQuery] int limit = 500,
        CancellationToken ct = default)
    {
        limit = Math.Clamp(limit, 1, 30000);
        var normalizedType = string.IsNullOrWhiteSpace(asset_type) ? null : asset_type.Trim();
        var cacheKey = $"snapshot:assets:latest:v2:{normalizedType ?? "all"}:{limit}";

        var cached = await _intelligence.ReadCacheJsonAsync(cacheKey, ct);
        if (!string.IsNullOrWhiteSpace(cached))
            return Content(cached, "application/json");

        var path = $"/assets/snapshot/latest?limit={limit}";
        if (!string.IsNullOrWhiteSpace(normalizedType))
            path += $"&asset_type={Uri.EscapeDataString(normalizedType)}";

        var json = await _intelligence.GetPythonJsonAsync(path, ct);
        if (json == null) return StatusCode(502, "Intelligence layer unavailable.");
        await _intelligence.SetCachedJsonAsync(cacheKey, json, TimeSpan.FromSeconds(120), ct);
        return Content(json, "application/json");
    }

    [HttpGet("snapshot")]
    [EnableRateLimiting("query")]
    public async Task<IActionResult> GetSnapshotAtTime(
        [FromQuery] DateTimeOffset? at = null,
        [FromQuery] int? lookback_minutes = null,
        CancellationToken ct = default)
    {
        var targetAt = at ?? (lookback_minutes.HasValue
            ? DateTimeOffset.UtcNow.AddMinutes(-lookback_minutes.Value)
            : DateTimeOffset.UtcNow);
        var key = $"snapshot:assets:{targetAt.UtcDateTime:yyyyMMddHHmm}";
        var cached = await _intelligence.ReadCacheJsonAsync(key, ct);
        if (string.IsNullOrWhiteSpace(cached))
            return NotFound(new { error = "SNAPSHOT_NOT_FOUND", key });

        return Content(cached, "application/json");
    }

    private static TimeSpan ResolveAssetsTtl(JsonElement payload)
    {
        if (payload.ValueKind == JsonValueKind.Object &&
            payload.TryGetProperty("assets", out var assets) &&
            assets.ValueKind == JsonValueKind.Array &&
            assets.GetArrayLength() == 0)
        {
            return TimeSpan.FromSeconds(20);
        }

        return TimeSpan.FromMinutes(5);
    }
}
