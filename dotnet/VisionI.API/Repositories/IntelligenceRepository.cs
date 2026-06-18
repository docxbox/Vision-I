using System.Text.Json;
using VisionI.API.Infrastructure;
using VisionI.API.Services;

namespace VisionI.API.Repositories;

public sealed class IntelligenceRepository : IIntelligenceRepository
{
    private readonly AppDbContext _db;
    private readonly PythonApiClient _python;
    private readonly RedisCacheService _cache;

    public IntelligenceRepository(
        AppDbContext db,
        PythonApiClient python,
        RedisCacheService cache)
    {
        _db = db;
        _python = python;
        _cache = cache;
    }

    public Task<string?> ReadCacheJsonAsync(string cacheKey, CancellationToken ct = default)
        => _cache.GetAsync(cacheKey, ct);

    public Task<string?> GetCachedJsonAsync(
        string cacheKey,
        Func<CancellationToken, Task<string?>> loader,
        TimeSpan ttl,
        CancellationToken ct = default)
        => _cache.GetOrSetAsync(cacheKey, () => loader(ct), ttl, ct);

    public Task SetCachedJsonAsync(string cacheKey, string value, TimeSpan ttl, CancellationToken ct = default)
        => _cache.SetAsync(cacheKey, value, ttl, ct);

    public Task<string?> GetPrecomputedJsonAsync(string key, CancellationToken ct = default)
        => _cache.GetPrecomputedAsync(key, ct);

    public Task RemoveCacheAsync(string key, CancellationToken ct = default)
        => _cache.RemoveAsync(key, ct);

    public async Task<bool> CanConnectDbAsync(CancellationToken ct = default)
    {
        try
        {
            return await _db.Database.CanConnectAsync(ct);
        }
        catch
        {
            return false;
        }
    }

    public Task<JsonDocument?> GetPythonAsync(string path, CancellationToken ct = default)
        => _python.GetAsync(path, ct);

    public Task<JsonDocument?> PostPythonAsync(string path, object? body, CancellationToken ct = default)
        => _python.PostAsync(path, body, ct);

    public Task<JsonDocument?> DeleteTrackedQueryAsync(int queryId, CancellationToken ct = default)
        => _python.DeleteTrackedQueryAsync(queryId, ct);
}
