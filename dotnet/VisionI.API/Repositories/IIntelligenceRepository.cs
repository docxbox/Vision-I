using System.Text.Json;

namespace VisionI.API.Repositories;

public interface IIntelligenceRepository
{
    Task<string?> ReadCacheJsonAsync(string cacheKey, CancellationToken ct = default);
    Task<string?> GetCachedJsonAsync(
        string cacheKey,
        Func<CancellationToken, Task<string?>> loader,
        TimeSpan ttl,
        CancellationToken ct = default);

    Task SetCachedJsonAsync(string cacheKey, string value, TimeSpan ttl, CancellationToken ct = default);
    Task<string?> GetPrecomputedJsonAsync(string key, CancellationToken ct = default);
    Task RemoveCacheAsync(string key, CancellationToken ct = default);
    Task<bool> CanConnectDbAsync(CancellationToken ct = default);
    Task<JsonDocument?> GetPythonAsync(string path, CancellationToken ct = default);
    Task<JsonDocument?> PostPythonAsync(string path, object? body, CancellationToken ct = default);
    Task<JsonDocument?> DeleteTrackedQueryAsync(int queryId, CancellationToken ct = default);
}
