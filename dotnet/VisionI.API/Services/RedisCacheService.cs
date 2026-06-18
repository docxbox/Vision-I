using Microsoft.Extensions.DependencyInjection;
using Microsoft.Extensions.Caching.Distributed;
using StackExchange.Redis;

namespace VisionI.API.Services;

/// <summary>
/// Redis-backed distributed cache service.
/// Wraps IDistributedCache with type-safe get/set operations and
/// precomputed key reading from the Python intelligence layer.
/// </summary>
public class RedisCacheService
{
    private readonly IDistributedCache _cache;
    private readonly ILogger<RedisCacheService> _log;
    private readonly IConnectionMultiplexer? _redis;

    public RedisCacheService(
        IDistributedCache cache,
        ILogger<RedisCacheService> log,
        IServiceProvider services)
    {
        _cache = cache;
        _log = log;
        _redis = services.GetService<IConnectionMultiplexer>();
    }

    /// <summary>
    /// Read a cached string value directly.
    /// Returns null if the key doesn't exist or Redis is unavailable.
    /// </summary>
    public async Task<string?> GetAsync(string key, CancellationToken ct = default)
    {
        try
        {
            return await _cache.GetStringAsync(key, ct);
        }
        catch (Exception ex)
        {
            _log.LogWarning(ex, "Redis read failed for key {Key}", key);
            return null;
        }
    }

    /// <summary>
    /// Store a cached string value with an absolute expiration.
    /// </summary>
    public async Task SetAsync(string key, string value, TimeSpan ttl, CancellationToken ct = default)
    {
        try
        {
            await _cache.SetStringAsync(key, value, new DistributedCacheEntryOptions
            {
                AbsoluteExpirationRelativeToNow = ttl
            }, ct);
        }
        catch (Exception ex)
        {
            _log.LogWarning(ex, "Redis write failed for key {Key}", key);
        }
    }

    /// <summary>
    /// Get a cached value or compute and store it.
    /// Returns null only if factory returns null (never caches null).
    /// </summary>
    public async Task<string?> GetOrSetAsync(
        string key,
        Func<Task<string?>> factory,
        TimeSpan ttl,
        CancellationToken ct = default)
    {
        try
        {
            var cached = await GetAsync(key, ct);
            if (cached != null) return cached;
        }
        catch (Exception ex)
        {
            _log.LogWarning(ex, "Redis read failed for key {Key}, falling back to factory", key);
        }

        var result = await factory();
        if (result != null)
        {
            await SetAsync(key, result, ttl, ct);
        }
        return result;
    }

    /// <summary>
    /// Read a precomputed key (written by Python intelligence workers).
    /// Returns null if the key doesn't exist or Redis is unavailable.
    /// </summary>
    public async Task<string?> GetPrecomputedAsync(string key, CancellationToken ct = default)
    {
        if (_redis != null)
        {
            try
            {
                var db = _redis.GetDatabase();

                // Python workers write shared precomputed keys directly, without
                // the distributed-cache instance prefix used by .NET application caches.
                var rawValue = await db.StringGetAsync(key);
                if (rawValue.HasValue)
                    return rawValue.ToString();

                // Keep supporting namespaced keys as a fallback for local/manual writes.
                var namespacedValue = await db.StringGetAsync($"vision:{key}");
                if (namespacedValue.HasValue)
                    return namespacedValue.ToString();
            }
            catch (Exception ex)
            {
                _log.LogWarning(ex, "Direct Redis read failed for precomputed key {Key}", key);
            }
        }

        try
        {
            return await _cache.GetStringAsync(key, ct);
        }
        catch (Exception ex)
        {
            _log.LogWarning(ex, "Redis read failed for precomputed key {Key}", key);
            return null;
        }
    }

    /// <summary>Remove a specific cache key.</summary>
    public async Task RemoveAsync(string key, CancellationToken ct = default)
    {
        try
        {
            await _cache.RemoveAsync(key, ct);
        }
        catch (Exception ex)
        {
            _log.LogWarning(ex, "Redis remove failed for key {Key}", key);
        }
    }

    /// <summary>
    /// Remove cache entries that start with any of the supplied logical prefixes.
    /// Distributed-cache keys are scanned using the configured Redis instance prefix.
    /// </summary>
    public async Task<int> RemoveByPrefixesAsync(IEnumerable<string> prefixes, CancellationToken ct = default)
    {
        if (_redis == null)
            return 0;

        try
        {
            var db = _redis.GetDatabase();
            var prefixList = prefixes
                .Where(p => !string.IsNullOrWhiteSpace(p))
                .Select(p => p.Trim())
                .Distinct(StringComparer.OrdinalIgnoreCase)
                .ToList();

            if (prefixList.Count == 0)
                return 0;

            var removed = 0;
            foreach (var endpoint in _redis.GetEndPoints())
            {
                ct.ThrowIfCancellationRequested();

                var server = _redis.GetServer(endpoint);
                if (!server.IsConnected)
                    continue;

                foreach (var prefix in prefixList)
                {
                    foreach (var pattern in BuildPatterns(prefix))
                    {
                        foreach (var key in server.Keys(pattern: pattern))
                        {
                            ct.ThrowIfCancellationRequested();
                            if (await db.KeyDeleteAsync(key).ConfigureAwait(false))
                                removed++;
                        }
                    }
                }
            }

            if (removed > 0)
                _log.LogInformation("Redis invalidation removed {Count} keys for prefixes: {Prefixes}", removed, string.Join(", ", prefixList));

            return removed;
        }
        catch (Exception ex)
        {
            _log.LogWarning(ex, "Redis prefix invalidation failed");
            return 0;
        }
    }

    private static IEnumerable<string> BuildPatterns(string prefix)
    {
        yield return $"vision:{prefix}*";

        if (!prefix.StartsWith("vision:", StringComparison.OrdinalIgnoreCase))
            yield return $"{prefix}*";
    }
}
