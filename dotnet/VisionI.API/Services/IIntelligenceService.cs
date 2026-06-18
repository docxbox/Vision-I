using System.Text.Json;

namespace VisionI.API.Services;

public interface IIntelligenceService
{
    Task<string?> ReadCacheJsonAsync(string cacheKey, CancellationToken ct = default);
    Task<string?> GetCachedJsonAsync(
        string cacheKey,
        Func<CancellationToken, Task<string?>> loader,
        TimeSpan ttl,
        CancellationToken ct = default);

    Task SetCachedJsonAsync(string cacheKey, string value, TimeSpan ttl, CancellationToken ct = default);
    Task<string?> GetPythonJsonAsync(string path, CancellationToken ct = default);
    Task<string?> PostPythonJsonAsync(string path, object? body, CancellationToken ct = default);
    Task<string?> GetPrecomputedJsonAsync(string key, CancellationToken ct = default);
    Task RemoveCacheAsync(string key, CancellationToken ct = default);
    Task<bool> CanConnectDbAsync(CancellationToken ct = default);
    Task<JsonDocument?> GetPythonDocumentAsync(string path, CancellationToken ct = default);
    Task<JsonDocument?> PostPythonDocumentAsync(string path, object? body, CancellationToken ct = default);
    Task<JsonDocument?> DeleteTrackedQueryAsync(int queryId, CancellationToken ct = default);
    Task<T?> GetPythonModelAsync<T>(string path, CancellationToken ct = default);
    Task<T?> PostPythonModelAsync<T>(string path, object? body, CancellationToken ct = default);
    T? DeserializeJson<T>(string? json);
    string SerializeJson<T>(T value);
}
