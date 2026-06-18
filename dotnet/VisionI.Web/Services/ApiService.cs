using System.Net.Http.Json;
using System.Text.Json;
using System.Text.Json.Serialization;
using VisionI.Web.Models;

namespace VisionI.Web.Services;

/// <summary>
/// Shared API client for authenticated calls from the web app.
/// </summary>
public class ApiService
{
    private readonly HttpClient _http;
    private readonly AuthService _auth;
    private readonly ILogger<ApiService> _log;

    // Allow only one refresh attempt at a time.
    private readonly SemaphoreSlim _refreshLock = new(1, 1);

    private static readonly JsonSerializerOptions _json = new()
    {
        PropertyNameCaseInsensitive = true,
        DefaultIgnoreCondition = JsonIgnoreCondition.WhenWritingNull,
    };

    public ApiService(HttpClient http, AuthService auth, ILogger<ApiService> log)
    {
        _http = http;
        _auth = auth;
        _log = log;
    }

    /// <summary>
    /// Handles a 401 and returns whether the request should be retried.
    /// </summary>
    private async Task<bool> HandleUnauthorizedAsync(CancellationToken ct)
    {
        var tokenBefore = _auth.AccessToken;

        await _refreshLock.WaitAsync(ct);
        try
        {
            // Another request already refreshed the token.
            if (_auth.AccessToken != tokenBefore && !string.IsNullOrEmpty(_auth.AccessToken))
                return true;

            if (await _auth.TryRefreshAsync(ct))
            {
                SetAuthHeader();
                return true;
            }

            // Refresh failed, so clear the session.
            if (!string.IsNullOrEmpty(_auth.AccessToken))
                await _auth.LogoutAsync(ct);

            return false;
        }
        finally
        {
            _refreshLock.Release();
        }
    }

    // Standard: light reads & mutations. Extended: multi-source reads. Heavy: LLM/ML/graph.
    private static readonly TimeSpan _shortTimeout    = TimeSpan.FromSeconds(15);
    private static readonly TimeSpan _standardTimeout = TimeSpan.FromSeconds(30);
    private static readonly TimeSpan _heavyTimeout    = TimeSpan.FromSeconds(60);

    public Task<T?> GetAsync<T>(string path, CancellationToken ct = default)
        => CoreGetAsync<T>(path, _shortTimeout, ct);

    public Task<T?> GetAsync<T>(string path, TimeSpan timeout, CancellationToken ct = default)
        => CoreGetAsync<T>(path, timeout, ct);

    private async Task<T?> CoreGetAsync<T>(string path, TimeSpan timeout, CancellationToken ct)
    {
        try
        {
            using var linked = CancellationTokenSource.CreateLinkedTokenSource(ct);
            linked.CancelAfter(timeout);
            ct = linked.Token;
            SetAuthHeader();
            var resp = await _http.GetAsync(path, ct);

            if (resp.StatusCode == System.Net.HttpStatusCode.Unauthorized)
            {
                if (!await HandleUnauthorizedAsync(ct)) return default;
                resp = await _http.GetAsync(path, ct);
                if (resp.StatusCode == System.Net.HttpStatusCode.Unauthorized) return default;
            }

            if (resp.StatusCode == System.Net.HttpStatusCode.Forbidden)
            {
                _log.LogWarning("GET {Path} -> 403 Forbidden (check Admin role)", path);
                return default;
            }

            resp.EnsureSuccessStatusCode();
            return await resp.Content.ReadFromJsonAsync<T>(_json, ct);
        }
        catch (Exception ex)
        {
            _log.LogWarning("GET {Path} failed: {Error}", path, ex.Message);
            return default;
        }
    }

    public Task<T?> PostAsync<T>(string path, object body, CancellationToken ct = default)
        => CorePostAsync<T>(path, body, _shortTimeout, ct);

    public Task<T?> PostAsync<T>(string path, object body, TimeSpan timeout, CancellationToken ct = default)
        => CorePostAsync<T>(path, body, timeout, ct);

    private async Task<T?> CorePostAsync<T>(string path, object body, TimeSpan timeout, CancellationToken ct)
    {
        var (value, _) = await CorePostWithErrorAsync<T>(path, body, timeout, ct);
        return value;
    }

    public Task<(T? Value, string? Error)> PostWithErrorAsync<T>(string path, object body, CancellationToken ct = default)
        => CorePostWithErrorAsync<T>(path, body, _shortTimeout, ct);

    private async Task<(T? Value, string? Error)> CorePostWithErrorAsync<T>(string path, object body, TimeSpan timeout, CancellationToken ct)
    {
        try
        {
            using var linked = CancellationTokenSource.CreateLinkedTokenSource(ct);
            linked.CancelAfter(timeout);
            ct = linked.Token;
            SetAuthHeader();
            var resp = await _http.PostAsJsonAsync(path, body, _json, ct);

            if (resp.StatusCode == System.Net.HttpStatusCode.Unauthorized)
            {
                if (!await HandleUnauthorizedAsync(ct)) return (default, "Authentication failed.");
                resp = await _http.PostAsJsonAsync(path, body, _json, ct);
                if (resp.StatusCode == System.Net.HttpStatusCode.Unauthorized) return (default, "Authentication failed.");
            }

            if (!resp.IsSuccessStatusCode)
            {
                var errBody = await resp.Content.ReadAsStringAsync(ct);
                string? message = null;
                try
                {
                    using var doc = JsonDocument.Parse(errBody);
                    if (doc.RootElement.TryGetProperty("error", out var ep))
                        message = ep.GetString();
                    else if (doc.RootElement.TryGetProperty("message", out var mp))
                        message = mp.GetString();
                }
                catch { }
                var errorMsg = message ?? $"Server returned {(int)resp.StatusCode}";
                _log.LogWarning("POST {Path} -> {Status}: {Error}", path, resp.StatusCode, errBody);
                return (default, errorMsg);
            }

            var value = await resp.Content.ReadFromJsonAsync<T>(_json, ct);
            return (value, null);
        }
        catch (Exception ex)
        {
            _log.LogWarning("POST {Path} failed: {Error}", path, ex.Message);
            return (default, $"Request failed: {ex.Message}");
        }
    }

    public async Task<T?> PatchAsync<T>(string path, object body, CancellationToken ct = default)
    {
        try
        {
            using var linked = CancellationTokenSource.CreateLinkedTokenSource(ct);
            linked.CancelAfter(_shortTimeout);
            ct = linked.Token;
            SetAuthHeader();
            var content = new StringContent(
                JsonSerializer.Serialize(body, _json),
                System.Text.Encoding.UTF8, "application/json");
            var resp = await _http.PatchAsync(path, content, ct);

            if (resp.StatusCode == System.Net.HttpStatusCode.Unauthorized)
            {
                if (!await HandleUnauthorizedAsync(ct)) return default;
                var content2 = new StringContent(
                    JsonSerializer.Serialize(body, _json),
                    System.Text.Encoding.UTF8, "application/json");
                resp = await _http.PatchAsync(path, content2, ct);
                if (resp.StatusCode == System.Net.HttpStatusCode.Unauthorized) return default;
            }

            if (!resp.IsSuccessStatusCode)
            {
                _log.LogWarning("PATCH {Path} -> {Status}", path, resp.StatusCode);
                return default;
            }

            return await resp.Content.ReadFromJsonAsync<T>(_json, ct);
        }
        catch (Exception ex)
        {
            _log.LogWarning("PATCH {Path} failed: {Error}", path, ex.Message);
            return default;
        }
    }

    public async Task<bool> DeleteAsync(string path, CancellationToken ct = default)
    {
        try
        {
            using var linked = CancellationTokenSource.CreateLinkedTokenSource(ct);
            linked.CancelAfter(_shortTimeout);
            ct = linked.Token;
            SetAuthHeader();
            var resp = await _http.DeleteAsync(path, ct);

            if (resp.StatusCode == System.Net.HttpStatusCode.Unauthorized)
            {
                if (!await HandleUnauthorizedAsync(ct)) return false;
                resp = await _http.DeleteAsync(path, ct);
                if (resp.StatusCode == System.Net.HttpStatusCode.Unauthorized) return false;
            }

            return resp.IsSuccessStatusCode;
        }
        catch (Exception ex)
        {
            _log.LogWarning("DELETE {Path} failed: {Error}", path, ex.Message);
            return false;
        }
    }

    /// <summary>
    /// Returns an optimistic result immediately and updates later with the server response.
    /// </summary>
    public T PostOptimisticAsync<T>(
        string path,
        object body,
        T optimisticResult,
        Action<T?> onServerResponse,
        CancellationToken ct = default)
    {
        _ = Task.Run(async () =>
        {
            try
            {
                var result = await PostAsync<T>(path, body, ct);
                onServerResponse(result);
            }
            catch (Exception ex)
            {
                _log.LogWarning("Optimistic POST {Path} failed: {Error}", path, ex.Message);
                onServerResponse(default);
            }
        }, ct);

        return optimisticResult;
    }

    public Task<VisionI.Web.Models.DecisionsResponse?> GetDecisionsAsync(int limit = 50, CancellationToken ct = default)
        => GetAsync<VisionI.Web.Models.DecisionsResponse>($"api/decisions?limit={limit}", ct);

    public Task<VisionI.Web.Models.DecisionDto?> CreateDecisionAsync(VisionI.Web.Models.CreateDecisionDto dto, CancellationToken ct = default)
        => PostAsync<VisionI.Web.Models.DecisionDto>("api/decisions", dto, ct);

    public Task<VisionI.Web.Models.DecisionDto?> RecordOutcomeAsync(string decisionId, VisionI.Web.Models.RecordOutcomeDto dto, CancellationToken ct = default)
        => PostAsync<VisionI.Web.Models.DecisionDto>($"api/decisions/{Uri.EscapeDataString(decisionId)}/outcome", dto, ct);

    public Task<WorkspaceDecisionResultDto?> CreateWorkspaceDecisionAsync(
        string slug, WorkspaceDecisionRequest req, CancellationToken ct = default)
        => PostAsync<WorkspaceDecisionResultDto>($"api/workspaces/{Uri.EscapeDataString(slug)}/decisions", req, ct);

    public Task<VisionI.Web.Models.CopilotResponseDto?> CopilotAskAsync(VisionI.Web.Models.CopilotAskDto dto, CancellationToken ct = default)
        => PostAsync<VisionI.Web.Models.CopilotResponseDto>("api/copilot/ask", dto, _heavyTimeout, ct);

    public Task<VisionI.Web.Models.CopilotExplainDto?> CopilotExplainAsync(string eventId, CancellationToken ct = default)
        => PostAsync<VisionI.Web.Models.CopilotExplainDto>($"api/copilot/explain/{Uri.EscapeDataString(eventId)}", new { }, _heavyTimeout, ct);

    public Task<VisionI.Web.Models.CopilotRecommendDto?> CopilotRecommendAsync(string eventId, CancellationToken ct = default)
        => GetAsync<VisionI.Web.Models.CopilotRecommendDto>($"api/copilot/recommend/{Uri.EscapeDataString(eventId)}", _heavyTimeout, ct);

    public Task<VisionI.Web.Models.CopilotSimilarDto?> CopilotSimilarAsync(string eventId, int limit = 5, CancellationToken ct = default)
        => GetAsync<VisionI.Web.Models.CopilotSimilarDto>($"api/copilot/similar/{Uri.EscapeDataString(eventId)}?limit={limit}", ct);

    public Task<VisionI.Web.Models.EventIntelligenceDto?> GetEventIntelligenceAsync(string eventId, CancellationToken ct = default)
        => GetAsync<VisionI.Web.Models.EventIntelligenceDto>($"api/events/{Uri.EscapeDataString(eventId)}/intelligence", ct);

    public Task<VisionI.Web.Models.EventSocialDto?> GetEventSocialAsync(string eventId, int limit = 20, CancellationToken ct = default)
        => GetAsync<VisionI.Web.Models.EventSocialDto>($"api/events/{Uri.EscapeDataString(eventId)}/social?limit={limit}", ct);

    public Task<VisionI.Web.Models.EntitiesResponse?> GetEntitiesAsync(
        string? type = null,
        int minMentions = 1,
        int limit = 100,
        int offset = 0,
        CancellationToken ct = default)
    {
        var qs = $"api/entities?min_mentions={minMentions}&limit={limit}&offset={offset}";
        if (!string.IsNullOrEmpty(type)) qs += $"&type={Uri.EscapeDataString(type)}";
        return GetAsync<VisionI.Web.Models.EntitiesResponse>(qs, _standardTimeout, ct);
    }

    public Task<JsonElement?> GetEntityActorDetailAsync(string entityId, CancellationToken ct = default)
        => GetAsync<JsonElement?>($"api/actors/{Uri.EscapeDataString(entityId)}", _standardTimeout, ct);

    public Task<JsonElement?> GetEntityWikipediaAsync(string entityId, CancellationToken ct = default)
        => GetAsync<JsonElement?>($"api/entities/{Uri.EscapeDataString(entityId)}/wikipedia", ct);

    public Task<JsonElement?> GetEntityGraphRawAsync(string entityId, int depth = 1, CancellationToken ct = default)
        => GetAsync<JsonElement?>($"api/entities/{Uri.EscapeDataString(entityId)}/graph?depth={depth}", _standardTimeout, ct);

    public Task<JsonElement?> MapGraphEntityAsync(string? id, string label, string? group, CancellationToken ct = default)
        => PostAsync<JsonElement?>("api/entities/map", new
        {
            id,
            label,
            group,
            source = "graph_click"
        }, _standardTimeout, ct);

    public Task<VisionI.Web.Models.EventsResponse?> GetEventsAsync(
        string? source = null, string? eventType = null, string? query = null,
        string? sentiment = null, string? from = null, string? to = null,
        int limit = 50, int offset = 0,
        CancellationToken ct = default)
    {
        var qs = $"api/events?limit={limit}&offset={offset}";
        if (!string.IsNullOrEmpty(source))    qs += $"&source={Uri.EscapeDataString(source)}";
        if (!string.IsNullOrEmpty(eventType)) qs += $"&event_type={Uri.EscapeDataString(eventType)}";
        if (!string.IsNullOrEmpty(query))     qs += $"&query={Uri.EscapeDataString(query)}";
        if (!string.IsNullOrEmpty(sentiment)) qs += $"&sentiment={Uri.EscapeDataString(sentiment)}";
        if (!string.IsNullOrEmpty(from))      qs += $"&from={Uri.EscapeDataString(from)}";
        if (!string.IsNullOrEmpty(to))        qs += $"&to={Uri.EscapeDataString(to)}";
        return GetAsync<VisionI.Web.Models.EventsResponse>(qs, ct);
    }

    public Task<VisionI.Web.Models.EventsResponse?> GetEventFeedAsync(
        string? source = null, string? eventType = null, string? query = null, string? sentiment = null,
        string? from = null, string? to = null, string? sort = "latest",
        string? groupBy = "none",
        int limit = 50, int offset = 0,
        CancellationToken ct = default)
    {
        var qs = $"api/events/feed?limit={limit}&offset={offset}";
        if (!string.IsNullOrEmpty(source))    qs += $"&source={Uri.EscapeDataString(source)}";
        if (!string.IsNullOrEmpty(eventType)) qs += $"&event_type={Uri.EscapeDataString(eventType)}";
        if (!string.IsNullOrEmpty(query))     qs += $"&query={Uri.EscapeDataString(query)}";
        if (!string.IsNullOrEmpty(sentiment)) qs += $"&sentiment={Uri.EscapeDataString(sentiment)}";
        if (!string.IsNullOrEmpty(from))      qs += $"&from={Uri.EscapeDataString(from)}";
        if (!string.IsNullOrEmpty(to))        qs += $"&to={Uri.EscapeDataString(to)}";
        if (!string.IsNullOrEmpty(sort))      qs += $"&sort={Uri.EscapeDataString(sort)}";
        if (!string.IsNullOrEmpty(groupBy))   qs += $"&group_by={Uri.EscapeDataString(groupBy)}";
        return GetAsync<VisionI.Web.Models.EventsResponse>(qs, ct);
    }

    public Task<VisionI.Web.Models.LlmProvidersResponse?> GetLlmProvidersAsync(CancellationToken ct = default)
        => GetAsync<VisionI.Web.Models.LlmProvidersResponse>("api/admin/llm/providers", ct);

    public Task<System.Text.Json.JsonElement?> UpsertLlmProviderAsync(VisionI.Web.Models.UpsertLlmProviderDto dto, CancellationToken ct = default)
        => PostAsync<System.Text.Json.JsonElement?>("api/admin/llm/providers", dto, ct);

    public Task<VisionI.Web.Models.LlmTestResponse?> TestLlmProviderAsync(VisionI.Web.Models.UpsertLlmProviderDto dto, CancellationToken ct = default)
        => PostAsync<VisionI.Web.Models.LlmTestResponse>("api/admin/llm/providers/test", new
        {
            provider = dto.Provider,
            model = dto.Model,
            base_url = dto.BaseUrl,
            api_key = dto.ApiKey,
            enabled = dto.IsEnabled,
        }, ct);

    public Task<VisionI.Web.Models.AgentLlmStatusDto?> GetAgentLlmStatusAsync(CancellationToken ct = default)
        => GetAsync<VisionI.Web.Models.AgentLlmStatusDto>("api/agents/llm-status", ct);

    public Task<VisionI.Web.Models.AgentsListResponse?> GetAgentsAsync(CancellationToken ct = default)
        => GetAsync<VisionI.Web.Models.AgentsListResponse>("api/agents", ct);

    public Task<VisionI.Web.Models.MissionLogResponse?> GetAgentLogsAsync(int limit = 50, CancellationToken ct = default)
        => GetAsync<VisionI.Web.Models.MissionLogResponse>($"api/agents/log?limit={limit}", ct);

    public Task<AirspaceClosuresResponse?> GetAirspaceAsync(CancellationToken ct = default)
        => GetAsync<AirspaceClosuresResponse>("api/airspace/closures", ct);

    public Task<JammingHeatmapResponse?> GetJammingHeatmapAsync(CancellationToken ct = default)
        => GetAsync<JammingHeatmapResponse>("api/airspace/jamming-heatmap", ct);

    public Task<ReroutesResponse?> GetReroutesAsync(CancellationToken ct = default)
        => GetAsync<ReroutesResponse>("api/airspace/reroutes", ct);

    public Task<SatellitePassesResponse?> GetSatellitePassesAsync(
        double latMin = -90, double lonMin = -180,
        double latMax = 90,  double lonMax = 180,
        int hoursAhead = 6, CancellationToken ct = default)
        => GetAsync<SatellitePassesResponse>(
            $"api/airspace/satellite-passes?lat_min={latMin}&lon_min={lonMin}&lat_max={latMax}&lon_max={lonMax}&hours_ahead={hoursAhead}",
            ct);

    public Task<VisionI.Web.Models.EventContextEnvelopeDto?> GetEventContextAsync(string eventId, CancellationToken ct = default)
        => GetAsync<VisionI.Web.Models.EventContextEnvelopeDto>($"api/events/{Uri.EscapeDataString(eventId)}/context", ct);

    /// <summary>
    /// Fetches the full event detail payload in a single round trip.
    /// </summary>
    public Task<VisionI.Web.Models.EventFullDto?> GetEventFullAsync(
        string eventId, int socialLimit = 20, int similarLimit = 5, CancellationToken ct = default)
        => GetAsync<VisionI.Web.Models.EventFullDto>(
            $"api/events/{Uri.EscapeDataString(eventId)}/full?social_limit={socialLimit}&similar_limit={similarLimit}",
            _standardTimeout, ct);

    public Task<VisionI.Web.Models.NarrativeForecastDto?> GetNarrativeForecastAsync(
        string narrativeId, int horizon = 12, CancellationToken ct = default)
        => GetAsync<VisionI.Web.Models.NarrativeForecastDto>(
            $"api/narratives/{Uri.EscapeDataString(narrativeId)}/forecast?horizon={horizon}",
            _standardTimeout, ct);

    public Task<VisionI.Web.Models.PlaybooksResponse?> GetPlaybooksAsync(CancellationToken ct = default)
        => GetAsync<VisionI.Web.Models.PlaybooksResponse>("api/playbooks", ct);

    public Task<VisionI.Web.Models.PlaybookDefinitionDto?> GetPlaybookAsync(string playbookId, CancellationToken ct = default)
        => GetAsync<VisionI.Web.Models.PlaybookDefinitionDto>($"api/playbooks/{Uri.EscapeDataString(playbookId)}", ct);

    public Task<VisionI.Web.Models.PlaybooksResponse?> MatchPlaybooksAsync(object context, CancellationToken ct = default)
        => PostAsync<VisionI.Web.Models.PlaybooksResponse>("api/playbooks/match", context, ct);

    public Task<VisionI.Web.Models.PlaybookExecutionResponseDto?> ExecutePlaybookAsync(
        string playbookId, object context, CancellationToken ct = default)
        => PostAsync<VisionI.Web.Models.PlaybookExecutionResponseDto>(
            $"api/playbooks/{Uri.EscapeDataString(playbookId)}/execute",
            context,
            ct);

    public Task<VisionI.Web.Models.SignalStatsDto?> GetSignalStatsAsync(CancellationToken ct = default)
        => GetAsync<VisionI.Web.Models.SignalStatsDto>("api/admin/signals/stats", ct);

    public Task<VisionI.Web.Models.DeadLetterQueueResponse?> GetDeadLetterQueueAsync(int limit = 50, CancellationToken ct = default)
        => GetAsync<VisionI.Web.Models.DeadLetterQueueResponse>($"api/admin/dlq?limit={limit}", ct);

    public Task<VisionI.Web.Models.DeadLetterQueueRetryResponse?> RetryDeadLetterQueueAsync(int index = 0, CancellationToken ct = default)
        => PostAsync<VisionI.Web.Models.DeadLetterQueueRetryResponse>($"api/admin/dlq/retry?index={index}", new { }, ct);

    public Task<VisionI.Web.Models.TriggerLiveResponse?> TriggerLiveAsync(CancellationToken ct = default)
        => PostAsync<VisionI.Web.Models.TriggerLiveResponse>("api/admin/trigger-live", new { }, ct);

    public Task<VisionI.Web.Models.EscalationResponse?> GetEscalationScoresAsync(CancellationToken ct = default)
        => GetAsync<VisionI.Web.Models.EscalationResponse>("api/intelligence/escalation", _standardTimeout, ct);

    public Task<VisionI.Web.Models.BotScoresResponse?> GetBotScoresAsync(int windowHours = 24, int limit = 50, CancellationToken ct = default)
        => GetAsync<VisionI.Web.Models.BotScoresResponse>($"api/intelligence/bot-scores?window_hours={windowHours}&limit={limit}", _standardTimeout, ct);

    public Task<VisionI.Web.Models.CredibilityResponse?> GetCredibilityAsync(CancellationToken ct = default)
        => GetAsync<VisionI.Web.Models.CredibilityResponse>("api/intelligence/credibility", _standardTimeout, ct);

    public Task<VisionI.Web.Models.UnrestWatchDto?> GetUnrestWatchAsync(int windowHours = 72, CancellationToken ct = default)
        => GetAsync<VisionI.Web.Models.UnrestWatchDto>($"api/intelligence/unrest-watch?window_hours={windowHours}", _standardTimeout, ct);

    public Task<VisionI.Web.Models.CommunityGraphResponse?> GetCommunityGraphAsync(int sinceHours = 48, CancellationToken ct = default)
        => GetAsync<VisionI.Web.Models.CommunityGraphResponse>($"api/intelligence/community-graph?since_hours={sinceHours}", _heavyTimeout, ct);

    public Task<VisionI.Web.Models.CausalityResponse?> GetCausalityAsync(string seriesA, string seriesB, int lagHours = 48, CancellationToken ct = default)
        => GetAsync<VisionI.Web.Models.CausalityResponse>($"api/intelligence/causality?series_a={Uri.EscapeDataString(seriesA)}&series_b={Uri.EscapeDataString(seriesB)}&lag_hours={lagHours}", _heavyTimeout, ct);

    public Task<VisionI.Web.Models.AlertsResponse?> GetAlertsAsync(int limit = 50, string? severity = null, CancellationToken ct = default)
        => GetAsync<VisionI.Web.Models.AlertsResponse>($"api/alerts?limit={limit}" + (string.IsNullOrEmpty(severity) ? "" : $"&severity={Uri.EscapeDataString(severity)}"), ct);

    public Task<VisionI.Web.Models.SituationsResponse?> GetSituationsAsync(int limit = 24, string? severity = null, string? status = "active", CancellationToken ct = default)
    {
        var path = $"api/situations?limit={limit}";
        if (!string.IsNullOrWhiteSpace(severity)) path += $"&severity={Uri.EscapeDataString(severity)}";
        if (!string.IsNullOrWhiteSpace(status)) path += $"&status={Uri.EscapeDataString(status)}";
        return GetAsync<VisionI.Web.Models.SituationsResponse>(path, ct);
    }

    public Task<VisionI.Web.Models.DetectedSituationDto?> GetSituationAsync(string situationId, CancellationToken ct = default)
        => GetAsync<VisionI.Web.Models.DetectedSituationDto>($"api/situations/{Uri.EscapeDataString(situationId)}", ct);

    public Task<VisionI.Web.Models.SignalDto?> GetSignalAsync(string signalId, CancellationToken ct = default)
        => GetAsync<VisionI.Web.Models.SignalDto>($"api/signals/{Uri.EscapeDataString(signalId)}", ct);

    public Task<VisionI.Web.Models.SignalsResponse?> GetSignalsAsync(string? source = null, string? clusterId = null, int limit = 50, CancellationToken ct = default)
    {
        var path = $"api/signals?limit={limit}";
        if (!string.IsNullOrWhiteSpace(source)) path += $"&source={Uri.EscapeDataString(source)}";
        if (!string.IsNullOrWhiteSpace(clusterId)) path += $"&cluster_id={Uri.EscapeDataString(clusterId)}";
        return GetAsync<VisionI.Web.Models.SignalsResponse>(path, _standardTimeout, ct);
    }

    public Task<VisionI.Web.Models.SignalsResponse?> SearchSignalsAsync(string query, double threshold = 0.5, int limit = 20, CancellationToken ct = default)
        => GetAsync<VisionI.Web.Models.SignalsResponse>($"api/signals/search?q={Uri.EscapeDataString(query)}&threshold={threshold}&limit={limit}", _standardTimeout, ct);

    public Task<VisionI.Web.Models.SignalClustersResponse?> GetSignalClustersAsync(int limit = 20, CancellationToken ct = default)
        => GetAsync<VisionI.Web.Models.SignalClustersResponse>($"api/signals/clusters?limit={limit}", _standardTimeout, ct);

    public Task<VisionI.Web.Models.CorrelationSummaryDto?> GetSignalCorrelationSummaryAsync(CancellationToken ct = default)
        => GetAsync<VisionI.Web.Models.CorrelationSummaryDto>("api/signals/correlation-summary", ct);

    public Task<VisionI.Web.Models.ConfidenceDistributionDto?> GetSignalConfidenceDistributionAsync(CancellationToken ct = default)
        => GetAsync<VisionI.Web.Models.ConfidenceDistributionDto>("api/signals/confidence-distribution", ct);

    public Task<JsonElement?> AckAlertAsync(string alertId, CancellationToken ct = default)
        => PostAsync<JsonElement?>($"api/alerts/{Uri.EscapeDataString(alertId)}/ack", new { }, ct);

    public Task<JsonElement?> ResolveAlertAsync(string alertId, CancellationToken ct = default)
        => PostAsync<JsonElement?>($"api/alerts/{Uri.EscapeDataString(alertId)}/resolve", new { }, ct);

    public Task<JsonElement?> GetOntologyOverviewAsync(int limit = 25, CancellationToken ct = default)
        => GetAsync<JsonElement?>($"api/ontology/overview?limit={limit}", ct);

    public Task<JsonElement?> GetOntologyGraphAsync(int limit = 36, CancellationToken ct = default)
        => GetAsync<JsonElement?>($"api/graph?limit={limit}", _standardTimeout, ct);

    public Task<JsonElement?> GetSentimentCountryHeatmapAsync(int days = 3, CancellationToken ct = default)
        => GetAsync<JsonElement?>($"api/sentiment/country-heatmap?days_back={days}", ct);

    public Task<JsonElement?> GetSentimentTimelineAsync(
        string? query = null, int days = 7,
        string? entityId = null, string? bucket = null,
        int? hours = null, CancellationToken ct = default)
    {
        var path = $"api/sentiment/timeline?days={days}";
        if (!string.IsNullOrWhiteSpace(query))    path += $"&query={Uri.EscapeDataString(query)}";
        if (!string.IsNullOrWhiteSpace(entityId)) path += $"&entity_id={Uri.EscapeDataString(entityId)}";
        if (!string.IsNullOrWhiteSpace(bucket))   path += $"&bucket={Uri.EscapeDataString(bucket)}";
        if (hours.HasValue)                        path += $"&hours={hours.Value}";
        return GetAsync<JsonElement?>(path, ct);
    }

    public Task<JsonElement?> GetInfluenceActorsAsync(int limit = 20, string? entityType = null, int windowDays = 30, CancellationToken ct = default)
    {
        var path = $"api/influence/actors?limit={limit}&window_days={windowDays}";
        if (!string.IsNullOrWhiteSpace(entityType)) path += $"&entity_type={Uri.EscapeDataString(entityType)}";
        return GetAsync<JsonElement?>(path, ct);
    }

    public Task<JsonElement?> GetInfluencePropagandaAsync(int windowHours = 6, CancellationToken ct = default)
        => GetAsync<JsonElement?>($"api/influence/propaganda?window_hours={windowHours}", ct);

    public Task<JsonElement?> GetInfluenceHerdAsync(int windowHours = 6, CancellationToken ct = default)
        => GetAsync<JsonElement?>($"api/influence/herd?window_hours={windowHours}", ct);

    public Task<JsonElement?> GetNarrativesAsync(int limit = 50, CancellationToken ct = default)
        => GetAsync<JsonElement?>($"api/narratives?limit={limit}", _standardTimeout, ct);

    public Task<JsonElement?> GetNarrativesSummaryAsync(CancellationToken ct = default)
        => GetAsync<JsonElement?>("api/narratives/summary", ct);

    public Task<JsonElement?> GetSourcesCatalogAsync(CancellationToken ct = default)
        => GetAsync<JsonElement?>("api/sources/catalog", ct);

    public Task<JsonElement?> IntelGetAsync(string pythonPath, CancellationToken ct = default)
        => GetAsync<JsonElement?>($"api/intel/{pythonPath.TrimStart('/')}", ct);

    public Task<JsonElement?> PyGetAsync(string pythonPath, CancellationToken ct = default)
        => GetAsync<JsonElement?>($"api/python/{pythonPath.TrimStart('/')}", ct);

    public Task<JsonElement?> PyPostAsync(string pythonPath, object body, CancellationToken ct = default)
        => PostAsync<JsonElement?>($"api/python/{pythonPath.TrimStart('/')}", body, ct);

    public Task<bool> PyDeleteAsync(string pythonPath, CancellationToken ct = default)
        => DeleteAsync($"api/python/{pythonPath.TrimStart('/')}", ct);

    public Task<VisionI.Web.Models.TriageSummaryDto?> GetTriageSummaryAsync(CancellationToken ct = default)
        => GetAsync<VisionI.Web.Models.TriageSummaryDto>("api/triage/summary", ct);

    public Task<VisionI.Web.Models.TriageListResponse<VisionI.Web.Models.TriageCandidateDto>?> GetTriageCandidatesAsync(
        int limit = 25,
        string? source = null,
        string? query = null,
        CancellationToken ct = default)
    {
        var path = $"api/triage/candidates?limit={limit}";
        if (!string.IsNullOrWhiteSpace(source)) path += $"&source={Uri.EscapeDataString(source)}";
        if (!string.IsNullOrWhiteSpace(query)) path += $"&query={Uri.EscapeDataString(query)}";
        return GetAsync<VisionI.Web.Models.TriageListResponse<VisionI.Web.Models.TriageCandidateDto>>(path, ct);
    }

    public Task<VisionI.Web.Models.TriageListResponse<VisionI.Web.Models.TriageRecordDto>?> GetTriageQueueAsync(
        string? eventId = null,
        string? status = null,
        bool mine = false,
        int limit = 100,
        int offset = 0,
        CancellationToken ct = default)
    {
        var path = $"api/triage/queue?limit={limit}&offset={offset}&mine={mine.ToString().ToLowerInvariant()}";
        if (!string.IsNullOrWhiteSpace(eventId)) path += $"&eventId={Uri.EscapeDataString(eventId)}";
        if (!string.IsNullOrWhiteSpace(status)) path += $"&status={Uri.EscapeDataString(status)}";
        return GetAsync<VisionI.Web.Models.TriageListResponse<VisionI.Web.Models.TriageRecordDto>>(path, ct);
    }

    public Task<VisionI.Web.Models.TriageRecordDto?> GetTriageRecordAsync(string eventId, CancellationToken ct = default)
        => GetAsync<VisionI.Web.Models.TriageRecordDto>($"api/triage/{Uri.EscapeDataString(eventId)}", ct);

    public Task<VisionI.Web.Models.TriageRecordDto?> UpsertTriageAsync(
        VisionI.Web.Models.UpsertTriageDto dto,
        CancellationToken ct = default)
        => PostAsync<VisionI.Web.Models.TriageRecordDto>($"api/triage/{Uri.EscapeDataString(dto.EventId)}", dto, ct);

    public Task<VisionI.Web.Models.OperationsOverviewDto?> GetOperationsOverviewAsync(int limit = 8, CancellationToken ct = default)
        => GetAsync<VisionI.Web.Models.OperationsOverviewDto>($"api/operations/overview?limit={limit}", _standardTimeout, ct);

    public Task<JsonElement?> SimulateCoaAsync(string alertId, CancellationToken ct = default)
        => PostAsync<JsonElement?>($"api/operations/{Uri.EscapeDataString(alertId)}/simulate", new { }, ct);

    public Task<VisionI.Web.Models.AssetsResponse?> GetAssetsAsync(string? assetType = null, int limit = 100, CancellationToken ct = default)
    {
        var path = $"api/assets?limit={limit}";
        if (!string.IsNullOrWhiteSpace(assetType)) path += $"&asset_type={Uri.EscapeDataString(assetType)}";
        return GetAsync<VisionI.Web.Models.AssetsResponse>(path, ct);
    }

    public Task<VisionI.Web.Models.AssetCountsDto?> GetAssetCountsAsync(CancellationToken ct = default)
        => GetAsync<VisionI.Web.Models.AssetCountsDto>("api/assets/counts", ct);

    // Unified ontology object read-model.
    public Task<JsonElement?> GetObjectAsync(string type, string id, CancellationToken ct = default)
        => GetAsync<JsonElement?>($"api/objects/{Uri.EscapeDataString(type)}/{Uri.EscapeDataString(id)}", ct);

    public Task<JsonElement?> GetObjectLineageAsync(string type, string id, CancellationToken ct = default)
        => GetAsync<JsonElement?>($"api/objects/{Uri.EscapeDataString(type)}/{Uri.EscapeDataString(id)}/lineage", ct);

    public Task<JsonElement?> GetObjectNeighborsAsync(string type, string id, int limit = 120, CancellationToken ct = default)
        => GetAsync<JsonElement?>($"api/objects/{Uri.EscapeDataString(type)}/{Uri.EscapeDataString(id)}/neighbors?limit={limit}", _standardTimeout, ct);

    public Task<VisionI.Web.Models.AssetsResponse?> GetAssetsInBoundsAsync(
        double minLat, double maxLat, double minLon, double maxLon,
        string? assetType = null, int limit = 2000, CancellationToken ct = default)
    {
        var path = $"api/assets/in-bounds?min_lat={minLat:0.####}&max_lat={maxLat:0.####}" +
                   $"&min_lon={minLon:0.####}&max_lon={maxLon:0.####}&limit={limit}";
        if (!string.IsNullOrWhiteSpace(assetType)) path += $"&asset_type={Uri.EscapeDataString(assetType)}";
        return GetAsync<VisionI.Web.Models.AssetsResponse>(path, _standardTimeout, ct);
    }

    public Task<VisionI.Web.Models.AssetDto?> GetAssetAsync(string assetId, CancellationToken ct = default)
        => GetAsync<VisionI.Web.Models.AssetDto>($"api/assets/{Uri.EscapeDataString(assetId)}", ct);

    public Task<VisionI.Web.Models.AssetSnapshotDto?> GetLatestAssetSnapshotAsync(string? assetType = null, int limit = 500, CancellationToken ct = default)
    {
        var path = $"api/assets/snapshot/latest?limit={limit}";
        if (!string.IsNullOrWhiteSpace(assetType)) path += $"&asset_type={Uri.EscapeDataString(assetType)}";
        return GetAsync<VisionI.Web.Models.AssetSnapshotDto>(path, ct);
    }

    public Task<VisionI.Web.Models.AssetSnapshotDto?> GetAssetSnapshotAsync(string at, string? assetType = null, int limit = 500, CancellationToken ct = default)
    {
        var path = $"api/assets/snapshot?at={Uri.EscapeDataString(at)}&limit={limit}";
        if (!string.IsNullOrWhiteSpace(assetType)) path += $"&asset_type={Uri.EscapeDataString(assetType)}";
        return GetAsync<VisionI.Web.Models.AssetSnapshotDto>(path, ct);
    }

    private void SetAuthHeader()
    {
        if (!string.IsNullOrEmpty(_auth.AccessToken))
            _http.DefaultRequestHeaders.Authorization =
                new System.Net.Http.Headers.AuthenticationHeaderValue("Bearer", _auth.AccessToken);
        else
            _http.DefaultRequestHeaders.Authorization = null;
    }
}
