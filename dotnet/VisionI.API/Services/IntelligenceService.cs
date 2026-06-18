using System.Text.Json;
using VisionI.API.Repositories;

namespace VisionI.API.Services;

public sealed class IntelligenceService : IIntelligenceService
{
    private static readonly JsonSerializerOptions JsonOptions = new()
    {
        PropertyNameCaseInsensitive = true,
        PropertyNamingPolicy = JsonNamingPolicy.SnakeCaseLower
    };

    private readonly IIntelligenceRepository _repository;

    public IntelligenceService(IIntelligenceRepository repository)
    {
        _repository = repository;
    }

    public Task<string?> ReadCacheJsonAsync(string cacheKey, CancellationToken ct = default)
        => _repository.ReadCacheJsonAsync(cacheKey, ct);

    public Task<string?> GetCachedJsonAsync(
        string cacheKey,
        Func<CancellationToken, Task<string?>> loader,
        TimeSpan ttl,
        CancellationToken ct = default)
        => _repository.GetCachedJsonAsync(cacheKey, loader, ttl, ct);

    public Task SetCachedJsonAsync(string cacheKey, string value, TimeSpan ttl, CancellationToken ct = default)
        => _repository.SetCachedJsonAsync(cacheKey, value, ttl, ct);

    public async Task<string?> GetPythonJsonAsync(string path, CancellationToken ct = default)
    {
        var result = await _repository.GetPythonAsync(path, ct);
        return result?.RootElement.GetRawText();
    }

    public async Task<string?> PostPythonJsonAsync(string path, object? body, CancellationToken ct = default)
    {
        var result = await _repository.PostPythonAsync(path, body, ct);
        return result?.RootElement.GetRawText();
    }

    public Task<string?> GetPrecomputedJsonAsync(string key, CancellationToken ct = default)
        => _repository.GetPrecomputedJsonAsync(key, ct);

    public Task RemoveCacheAsync(string key, CancellationToken ct = default)
        => _repository.RemoveCacheAsync(key, ct);

    public Task<bool> CanConnectDbAsync(CancellationToken ct = default)
        => _repository.CanConnectDbAsync(ct);

    public Task<JsonDocument?> GetPythonDocumentAsync(string path, CancellationToken ct = default)
        => _repository.GetPythonAsync(path, ct);

    public Task<JsonDocument?> PostPythonDocumentAsync(string path, object? body, CancellationToken ct = default)
        => _repository.PostPythonAsync(path, body, ct);

    public Task<JsonDocument?> DeleteTrackedQueryAsync(int queryId, CancellationToken ct = default)
        => _repository.DeleteTrackedQueryAsync(queryId, ct);

    public async Task<T?> GetPythonModelAsync<T>(string path, CancellationToken ct = default)
    {
        var document = await _repository.GetPythonAsync(path, ct);
        if (document is null)
            return default;

        return document.RootElement.Deserialize<T>(JsonOptions);
    }

    public async Task<T?> PostPythonModelAsync<T>(string path, object? body, CancellationToken ct = default)
    {
        var document = await _repository.PostPythonAsync(path, body, ct);
        if (document is null)
            return default;

        return document.RootElement.Deserialize<T>(JsonOptions);
    }

    public T? DeserializeJson<T>(string? json)
    {
        if (string.IsNullOrWhiteSpace(json))
            return default;

        return JsonSerializer.Deserialize<T>(json, JsonOptions);
    }

    public string SerializeJson<T>(T value)
        => JsonSerializer.Serialize(value, JsonOptions);
}
