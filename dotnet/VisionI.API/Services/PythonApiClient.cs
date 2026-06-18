using System.Net.Http.Json;
using System.Text.Json;

namespace VisionI.API.Services;

/// <summary>
/// Client for the internal Python API.
/// </summary>
public class PythonApiClient : IIntelligenceClient
{
    private readonly HttpClient _http;
    private readonly ILogger<PythonApiClient> _log;

    // Set the internal key per request because handlers may not forward default headers.
    private readonly string _apiKey;

    private const int GetMaxRetries = 1;
    private const int PostMaxRetries = 0;
    private static readonly TimeSpan RetryDelay = TimeSpan.FromSeconds(2);

    public PythonApiClient(HttpClient http, ILogger<PythonApiClient> log)
    {
        _http = http;
        _log = log;
        _apiKey = http.DefaultRequestHeaders.TryGetValues("X-Internal-Key", out var vals)
            ? vals.FirstOrDefault() ?? ""
            : "";
    }

    public async Task<JsonDocument?> HealthAsync(CancellationToken ct = default)
        => await GetJsonAsync("/health", ct);

    public async Task<JsonDocument?> TriggerIngestAsync(
        string query, int limit = 10, bool enrich = false,
        string[]? sources = null, CancellationToken ct = default)
    {
        var payload = new { query, limit, enrich, sources };
        return await PostJsonAsync("/ingest", payload, ct);
    }

    public async Task<JsonDocument?> GetIngestStatusAsync(string jobId, CancellationToken ct = default)
        => await GetJsonAsync($"/ingest/{Uri.EscapeDataString(jobId)}", ct);

    public async Task<JsonDocument?> GetEventsAsync(
        string? source = null, string? eventType = null, string? query = null,
        string? sentiment = null,
        string? from = null, string? to = null,
        int limit = 50, int offset = 0, string? jobId = null,
        CancellationToken ct = default)
    {
        var qs = BuildQs(
            ("source", source),
            ("event_type", eventType),
            ("query", query),
            ("sentiment", sentiment),
            ("from", from),
            ("to", to),
            ("limit", limit.ToString()),
            ("offset", offset.ToString()),
            ("job_id", jobId)
        );
        return await GetJsonAsync($"/events{qs}", ct);
    }

    public async Task<JsonDocument?> GetEventMapAsync(
        string? source = null, string? eventType = null,
        string? from = null, string? to = null, int limit = 500,
        CancellationToken ct = default)
    {
        var qs = BuildQs(
            ("source", source),
            ("event_type", eventType),
            ("from", from),
            ("to", to),
            ("limit", limit.ToString())
        );
        return await GetJsonAsync($"/events/map{qs}", ct);
    }

    public async Task<JsonDocument?> GetEventAsync(string eventId, CancellationToken ct = default)
        => await GetJsonAsync($"/events/{Uri.EscapeDataString(eventId)}", ct);

    public async Task<JsonDocument?> GetEventIntelligenceAsync(string eventId, CancellationToken ct = default)
        => await GetJsonAsync($"/event/{Uri.EscapeDataString(eventId)}", ct);

    public async Task<JsonDocument?> GetEventContextAsync(string eventId, CancellationToken ct = default)
        => await GetJsonAsync($"/event/{Uri.EscapeDataString(eventId)}/context", ct);

    public async Task<JsonDocument?> GetEventSocialAsync(string eventId, int limit = 20, CancellationToken ct = default)
        => await GetJsonAsync($"/events/{Uri.EscapeDataString(eventId)}/social?limit={limit}", ct);

    public async Task<JsonDocument?> GetEntitiesAsync(
        string? type = null, int minMentions = 1,
        int limit = 100, int offset = 0,
        CancellationToken ct = default)
    {
        var qs = BuildQs(
            ("type", type),
            ("min_mentions", minMentions.ToString()),
            ("limit", limit.ToString()),
            ("offset", offset.ToString())
        );
        return await GetJsonAsync($"/entities{qs}", ct);
    }

    public async Task<JsonDocument?> GetEntityGraphAsync(
        string entityId, int depth = 1, CancellationToken ct = default)
        => await GetJsonAsync($"/entities/{Uri.EscapeDataString(entityId)}/graph?depth={depth}", ct);

    public async Task<JsonDocument?> GetLiveStreamsAsync(
        int limit = 20, string? sources = null, CancellationToken ct = default)
    {
        var qs = BuildQs(("limit", limit.ToString()), ("sources", sources));
        return await GetJsonAsync($"/streams/live{qs}", ct);
    }

    public async Task<JsonDocument?> GetNarrativeForecastAsync(
        string narrativeId, int horizon = 12, CancellationToken ct = default)
        => await GetJsonAsync(
            $"/narratives/{Uri.EscapeDataString(narrativeId)}/forecast?horizon={horizon}",
            ct);

    public async Task<JsonDocument?> GetSentimentTimelineAsync(
        string? query = null, string? source = null,
        string? entityId = null, string? from = null, string? to = null,
        int? hours = null, string bucket = "day", CancellationToken ct = default)
    {
        var qs = BuildQs(
            ("query", query),
            ("source", source),
            ("entity_id", entityId),
            ("from", from),
            ("to", to),
            ("hours", hours?.ToString()),
            ("bucket", bucket)
        );
        return await GetJsonAsync($"/sentiment/timeline{qs}", ct);
    }

    public async Task<JsonDocument?> SearchSourceAsync(
        string source, Dictionary<string, string?> queryParams,
        CancellationToken ct = default)
    {
        var pairs = queryParams
            .Where(kv => kv.Value != null)
            .Select(kv => (kv.Key, (string?)kv.Value));
        var qs = BuildQs(pairs.ToArray());
        return await GetJsonAsync($"/sources/{source}{qs}", ct);
    }

    public async Task<JsonDocument?> GetSourceCatalogAsync(CancellationToken ct = default)
        => await GetJsonAsync("/sources/catalog", ct);

    public async Task<JsonDocument?> AdminHealthAsync(CancellationToken ct = default)
        => await GetJsonAsync("/admin/health", ct);

    public async Task<JsonDocument?> GetTrackedQueriesAsync(CancellationToken ct = default)
        => await GetJsonAsync("/admin/queries", ct);

    public async Task<JsonDocument?> AddTrackedQueryAsync(string query, CancellationToken ct = default)
        => await PostJsonAsync("/admin/queries", new { query }, ct);

    public async Task<JsonDocument?> DeleteTrackedQueryAsync(int queryId, CancellationToken ct = default)
    {
        try
        {
            using var req = new HttpRequestMessage(HttpMethod.Delete, $"/admin/queries/{queryId}");
            if (_apiKey.Length > 0) req.Headers.TryAddWithoutValidation("X-Internal-Key", _apiKey);
            var resp = await _http.SendAsync(req, ct);
            if (!resp.IsSuccessStatusCode)
            {
                _log.LogWarning("Python DELETE /admin/queries/{Id} -> {Status}", queryId, resp.StatusCode);
                return null;
            }
            return await resp.Content.ReadFromJsonAsync<JsonDocument>(cancellationToken: ct);
        }
        catch (Exception ex)
        {
            _log.LogError(ex, "Python DELETE /admin/queries/{Id} failed", queryId);
            return null;
        }
    }

    public async Task<JsonDocument?> GetJobsAsync(
        int limit = 20, string? status = null, CancellationToken ct = default)
    {
        var qs = BuildQs(("limit", limit.ToString()), ("status", status));
        return await GetJsonAsync($"/admin/jobs{qs}", ct);
    }

    public async Task<JsonDocument?> GetStatsAsync(CancellationToken ct = default)
        => await GetJsonAsync("/admin/stats", ct);

    public async Task<JsonDocument?> GetPipelineTopologyAsync(CancellationToken ct = default)
        => await GetJsonAsync("/admin/pipeline-topology", ct);

    public async Task<JsonDocument?> GetDeadLetterQueueAsync(int limit = 50, CancellationToken ct = default)
        => await GetJsonAsync($"/admin/dlq?limit={limit}", ct);

    public async Task<JsonDocument?> RetryDeadLetterQueueAsync(int index = 0, CancellationToken ct = default)
        => await PostJsonAsync($"/admin/dlq/retry?index={index}", new { }, ct);

    public async Task<JsonDocument?> GetSignalStatsAsync(CancellationToken ct = default)
        => await GetJsonAsync("/admin/signals/stats", ct);

    public async Task<JsonDocument?> TriggerLiveAsync(CancellationToken ct = default)
        => await PostJsonAsync("/admin/trigger-live", new { }, ct);

    public async Task<JsonDocument?> GetRuntimeLlmAsync(CancellationToken ct = default)
        => await GetJsonAsync("/admin/llm/runtime", ct);

    public async Task<JsonDocument?> SetRuntimeLlmAsync(object payload, CancellationToken ct = default)
        => await PostJsonAsync("/admin/llm/runtime", payload, ct);

    public async Task<JsonDocument?> TestRuntimeLlmAsync(object payload, CancellationToken ct = default)
        => await PostJsonAsync("/admin/llm/runtime/test", payload, ct);

    public async Task<JsonDocument?> GetOntologyOverviewAsync(int limit = 12, CancellationToken ct = default)
        => await GetJsonAsync($"/ontology/overview?limit={limit}", ct);

    public async Task<JsonDocument?> GetOntologyEventAsync(string eventId, CancellationToken ct = default)
        => await GetJsonAsync($"/ontology/events/{Uri.EscapeDataString(eventId)}", ct);

    public async Task<JsonDocument?> GetOntologyActorAsync(string actorId, CancellationToken ct = default)
        => await GetJsonAsync($"/ontology/actors/{Uri.EscapeDataString(actorId)}", ct);

    public async Task<JsonDocument?> GetOntologyGraphAsync(int limit = 10, CancellationToken ct = default)
        => await GetJsonAsync($"/ontology/graph?limit={limit}", ct);

    public async Task<JsonDocument?> GetOperationsOverviewAsync(int limit = 8, CancellationToken ct = default)
        => await GetJsonAsync($"/ontology/operations/overview?limit={limit}", ct);

    public async Task<JsonDocument?> GetPlaybooksAsync(CancellationToken ct = default)
        => await GetJsonAsync("/api/playbooks", ct);

    public async Task<JsonDocument?> GetPlaybookAsync(string playbookId, CancellationToken ct = default)
        => await GetJsonAsync($"/api/playbooks/{Uri.EscapeDataString(playbookId)}", ct);

    public async Task<JsonDocument?> MatchPlaybooksAsync(object payload, CancellationToken ct = default)
        => await PostJsonAsync("/api/playbooks/match", payload, ct);

    public async Task<JsonDocument?> ExecutePlaybookAsync(
        string playbookId, object payload, CancellationToken ct = default)
        => await PostJsonAsync($"/api/playbooks/{Uri.EscapeDataString(playbookId)}/execute", payload, ct);

    public async Task<JsonDocument?> GetSignalsAsync(
        string? source = null, string? clusterId = null,
        int limit = 50, CancellationToken ct = default)
    {
        var qs = BuildQs(
            ("source", source),
            ("cluster_id", clusterId),
            ("limit", limit.ToString())
        );
        return await GetJsonAsync($"/signals{qs}", ct);
    }

    public async Task<JsonDocument?> SearchSignalsAsync(
        string query, float threshold = 0.5f, int limit = 20,
        CancellationToken ct = default)
    {
        var qs = BuildQs(
            ("q", query),
            ("threshold", threshold.ToString("F2")),
            ("limit", limit.ToString())
        );
        return await GetJsonAsync($"/signals/search{qs}", ct);
    }

    public async Task<JsonDocument?> GetSignalClustersAsync(
        int limit = 20, CancellationToken ct = default)
        => await GetJsonAsync($"/signals/clusters?limit={limit}", ct);

    public async Task<JsonDocument?> GetSignalAsync(
        string signalId, CancellationToken ct = default)
        => await GetJsonAsync($"/signals/{Uri.EscapeDataString(signalId)}", ct);

    public async Task<JsonDocument?> GetAssetsAsync(
        string? assetType = null, int limit = 50,
        CancellationToken ct = default)
    {
        var qs = BuildQs(
            ("asset_type", assetType),
            ("limit", limit.ToString())
        );
        return await GetJsonAsync($"/assets{qs}", ct);
    }

    public async Task<JsonDocument?> GetAssetAsync(
        string assetId, CancellationToken ct = default)
        => await GetJsonAsync($"/assets/{Uri.EscapeDataString(assetId)}", ct);

    public async Task<JsonDocument?> GetAssetCountsAsync(CancellationToken ct = default)
        => await GetJsonAsync("/assets/counts", ct);

    public async Task<JsonDocument?> GetAgentLlmStatusAsync(CancellationToken ct = default)
        => await GetJsonAsync("/agents/llm-status", ct);

    public async Task<JsonDocument?> GetSentimentCountryHeatmapAsync(
        int daysBack = 7, CancellationToken ct = default)
        => await GetJsonAsync($"/sentiment/country-heatmap?days_back={daysBack}", ct);

    public async Task<JsonDocument?> GetNarrativesTimelineAsync(
        string? topic = null, string bucket = "day", int daysBack = 7,
        CancellationToken ct = default)
    {
        var qs = BuildQs(
            ("topic", topic),
            ("bucket", bucket),
            ("days_back", daysBack.ToString())
        );
        return await GetJsonAsync($"/narratives/timeline{qs}", ct);
    }

    public async Task<JsonDocument?> GetCopilotSummaryAsync(
        int windowHours = 6, CancellationToken ct = default)
        => await GetJsonAsync($"/copilot/summary?window_hours={windowHours}", ct);

    public async Task<JsonDocument?> GetClassificationAsync(CancellationToken ct = default)
        => await GetJsonAsync("/config/classification", ct);

    public async Task<JsonDocument?> ExecuteCypherAsync(
        string query, Dictionary<string, object?>? parameters = null,
        CancellationToken ct = default)
        => await PostJsonAsync("/ontology/cypher", new { query, parameters }, ct);

    public async Task<JsonDocument?> ResolveWorkspaceEventsAsync(object body, CancellationToken ct = default)
        => await PostJsonAsync("/workspace/resolve-events", body, ct);

    public async Task<JsonDocument?> ResolveWorkspaceAssetsAsync(object body, CancellationToken ct = default)
        => await PostJsonAsync("/workspace/resolve-assets", body, ct);

    public async Task<JsonDocument?> ResolveWorkspaceSentimentAsync(object body, CancellationToken ct = default)
        => await PostJsonAsync("/workspace/resolve-sentiment", body, ct);

    public async Task<JsonDocument?> ResolveWorkspaceEntitiesAsync(object body, CancellationToken ct = default)
        => await PostJsonAsync("/workspace/resolve-entities", body, ct);

    public async Task<JsonDocument?> ResolveWorkspaceCorrelationAsync(object body, CancellationToken ct = default)
        => await PostJsonAsync("/workspace/resolve-correlation", body, ct);

    /// <summary>Forwards a GET request directly to the Python API.</summary>
    public Task<JsonDocument?> GetAsync(string path, CancellationToken ct = default)
        => GetJsonAsync(path.StartsWith("/") ? path : $"/{path}", ct);

    /// <summary>Forwards a POST request directly to the Python API.</summary>
    public Task<JsonDocument?> PostAsync(string path, object? body, CancellationToken ct = default)
        => PostJsonAsync(path.StartsWith("/") ? path : $"/{path}", body ?? new { }, ct);

    /// <summary>Forwards a DELETE request directly to the Python API.</summary>
    public Task<bool> DeleteAsync(string path, CancellationToken ct = default)
        => DeleteJsonAsync(path.StartsWith("/") ? path : $"/{path}", ct);

    private HttpRequestMessage BuildGet(string path)
    {
        var req = new HttpRequestMessage(HttpMethod.Get, path);
        if (_apiKey.Length > 0)
            req.Headers.TryAddWithoutValidation("X-Internal-Key", _apiKey);
        return req;
    }

    private HttpRequestMessage BuildPost(string path, object body)
    {
        var req = new HttpRequestMessage(HttpMethod.Post, path)
        {
            Content = JsonContent.Create(body)
        };
        if (_apiKey.Length > 0)
            req.Headers.TryAddWithoutValidation("X-Internal-Key", _apiKey);
        return req;
    }

    private async Task<JsonDocument?> GetJsonAsync(string path, CancellationToken ct)
    {
        for (int attempt = 0; attempt <= GetMaxRetries; attempt++)
        {
            try
            {
                using var req = BuildGet(path);
                var resp = await _http.SendAsync(req, ct);
                if (resp.IsSuccessStatusCode)
                    return await resp.Content.ReadFromJsonAsync<JsonDocument>(cancellationToken: ct);

                _log.LogWarning("Python GET {Path} -> {Status} (attempt {Attempt})",
                    path, (int)resp.StatusCode, attempt + 1);

                // Retry only transport and server failures.
                if ((int)resp.StatusCode < 500)
                    return null;
            }
            catch (TaskCanceledException) when (ct.IsCancellationRequested)
            {
                return null;
            }
            catch (Exception ex)
            {
                _log.LogWarning(ex, "Python GET {Path} failed (attempt {Attempt})",
                    path, attempt + 1);
            }

            if (attempt < GetMaxRetries)
            {
                _log.LogInformation("Retrying Python GET {Path} in {Delay}s", path, RetryDelay.TotalSeconds);
                await Task.Delay(RetryDelay, ct);
            }
        }

        _log.LogError("Python GET {Path} failed after {Attempts} attempts", path, GetMaxRetries + 1);
        return null;
    }

    private async Task<JsonDocument?> PostJsonAsync(string path, object body, CancellationToken ct)
    {
        for (int attempt = 0; attempt <= PostMaxRetries; attempt++)
        {
            try
            {
                using var req = BuildPost(path, body);
                var resp = await _http.SendAsync(req, ct);
                if (resp.IsSuccessStatusCode)
                    return await resp.Content.ReadFromJsonAsync<JsonDocument>(cancellationToken: ct);

                _log.LogWarning("Python POST {Path} -> {Status} (attempt {Attempt})",
                    path, (int)resp.StatusCode, attempt + 1);

                if ((int)resp.StatusCode < 500)
                    return null;
            }
            catch (TaskCanceledException) when (ct.IsCancellationRequested)
            {
                return null;
            }
            catch (Exception ex)
            {
                _log.LogWarning(ex, "Python POST {Path} failed (attempt {Attempt})",
                    path, attempt + 1);
            }

            if (attempt < PostMaxRetries)
            {
                _log.LogInformation("Retrying Python POST {Path} in {Delay}s", path, RetryDelay.TotalSeconds);
                await Task.Delay(RetryDelay, ct);
            }
        }

        _log.LogError("Python POST {Path} failed after {Attempts} attempts", path, PostMaxRetries + 1);
        return null;
    }

    private async Task<bool> DeleteJsonAsync(string path, CancellationToken ct)
    {
        try
        {
            using var req = new HttpRequestMessage(HttpMethod.Delete, path);
            if (_apiKey.Length > 0)
                req.Headers.TryAddWithoutValidation("X-Internal-Key", _apiKey);

            var resp = await _http.SendAsync(req, ct);
            if (resp.IsSuccessStatusCode)
                return true;

            _log.LogWarning("Python DELETE {Path} -> {Status}", path, (int)resp.StatusCode);
            return false;
        }
        catch (TaskCanceledException) when (ct.IsCancellationRequested)
        {
            return false;
        }
        catch (Exception ex)
        {
            _log.LogWarning(ex, "Python DELETE {Path} failed", path);
            return false;
        }
    }

    private static string BuildQs(params (string Key, string? Value)[] pairs)
    {
        var parts = pairs
            .Where(p => p.Value != null)
            .Select(p => $"{Uri.EscapeDataString(p.Key)}={Uri.EscapeDataString(p.Value!)}");
        var qs = string.Join("&", parts);
        return qs.Length > 0 ? $"?{qs}" : string.Empty;
    }
}
