using System.Net.Http.Headers;
using System.Text.Json;
using Microsoft.AspNetCore.SignalR.Client;
using VisionI.Web.Models;

namespace VisionI.Web.Services;

/// <summary>
/// Scoped per-circuit service. Owns the authenticated SignalR hub connection and
/// all REST fetches that require the user's JWT. Writes results into the singleton
/// ViStateService cache. Replaces the hub logic that was previously in ViStateService.
///
/// Lifecycle:
///   MainLayout calls InitAsync(token) once after login.
///   On token refresh, call UpdateTokenAsync(newToken). The hub reconnects with the fresh token.
///   On logout, call ResetAsync(). It stops the hub and polling, then clears writes.
/// </summary>
public sealed class ViLiveSession : IAsyncDisposable
{
    private readonly ViStateService          _state;
    private readonly IHttpClientFactory      _factory;
    private readonly IConfiguration          _config;
    private readonly ILogger<ViLiveSession>  _log;
    private readonly JsonSerializerOptions   _json = new() { PropertyNameCaseInsensitive = true };

    private string  _token   = "";
    private string  _apiBase = "";
    private string  _eventWindow = "24H";
    private string? _eventSource;
    private string? _eventCursor;   // newest ingest_time we've applied; drives delta pulls

    private HubConnection?         _hub;
    private System.Timers.Timer?   _recoveryTimer;
    private System.Timers.Timer?   _notifyTimer;
    private bool                   _initialized;
    private readonly SemaphoreSlim _initLock = new(1, 1);

    // Coalesces bursts of hub events into a single refresh. Hub ticks set the
    // pending flags and (re)arm a short debounce; the drain runs at most once at
    // a time and re-checks the flags so nothing is lost while a fetch is in flight.
    private readonly object        _refreshGate = new();
    private bool                   _pendEvents, _pendAssets, _pendOverview, _refreshRunning;
    private System.Timers.Timer?   _refreshDebounce;

    // Page-level live triggers. Subscribed by pages that load their own data
    // (Alerts, Signals, Narratives) instead of reading ViStateService.
    public event Func<Task>? OnIngestComplete;
    public event Func<Task>? OnIntelligenceUpdate;
    public event Func<Task>? OnCorrelationUpdate;

    private static readonly SemaphoreSlim _assetHydrationLock = new(1, 1);
    private static List<AssetDto>? _cachedFleetAssets;
    private static (int Aircraft, int Vessels) _cachedFleetCounts;
    private static DateTime _cachedFleetAt = DateTime.MinValue;

    public bool IsConnected => _hub?.State == HubConnectionState.Connected;
    public HubConnectionState ConnectionState => _hub?.State ?? HubConnectionState.Disconnected;

    public ViLiveSession(ViStateService state, IHttpClientFactory factory,
                         IConfiguration config, ILogger<ViLiveSession> log)
    {
        _state   = state;
        _factory = factory;
        _config  = config;
        _log     = log;
        _apiBase = (config["InternalApiBaseUrl"] ?? config["ApiBaseUrl"] ?? "http://dotnet-api:5000").TrimEnd('/');
    }

    public async Task InitAsync(string token)
    {
        _token = token;
        if (_initialized) return;

        await _initLock.WaitAsync();
        try
        {
            if (_initialized) return;
            _log.LogInformation("[ViLive] Init circuit, token len={L}", token.Length);
            await ConnectHubAsync();
            StartRecovery();
            _initialized = true;
            NotifyDebounced();
            _ = WarmInitialStateAsync();
        }
        finally { _initLock.Release(); }
    }

    /// <summary>Call after token refresh. The hub uses a lambda so it picks up the new token automatically.</summary>
    public void UpdateToken(string newToken)
    {
        _token = newToken;
        _log.LogDebug("[ViLive] Token updated");
    }

    /// <summary>Call on logout. Stop the hub and recovery flow, then clear state.</summary>
    public async Task ResetAsync()
    {
        _recoveryTimer?.Stop();
        _recoveryTimer?.Dispose();
        _recoveryTimer = null;

        if (_hub != null)
        {
            try { await _hub.StopAsync(); } catch { }
            try { await _hub.DisposeAsync(); } catch { }
            _hub = null;
        }

        _token       = "";
        _initialized = false;
        _log.LogInformation("[ViLive] Session reset (logout)");
    }

    public Task RefreshEventsAsync(string window = "24H", string? source = null)
    {
        _eventWindow = string.IsNullOrWhiteSpace(window) ? "24H" : window;
        _eventSource = string.IsNullOrWhiteSpace(source) ? null : source;
        return FetchEventsAsync();
    }

    public Task RefreshAssetsAsync() => FetchAssetsAsync();

    private async Task<JsonElement> GetAsync(string path)
    {
        try
        {
            var clientName = path.StartsWith("api/assets", StringComparison.OrdinalIgnoreCase) ? "fleet" : "api";
            var client = _factory.CreateClient(clientName);
            client.DefaultRequestHeaders.Authorization =
                new AuthenticationHeaderValue("Bearer", _token);
            var resp = await client.GetAsync(path);
            if (!resp.IsSuccessStatusCode)
            {
                _log.LogWarning("[ViLive] GET {P} -> {C}", path, (int)resp.StatusCode);
                return default;
            }
            var json = await resp.Content.ReadAsStringAsync();
            return JsonSerializer.Deserialize<JsonElement>(json, _json);
        }
        catch (Exception ex)
        {
            _log.LogWarning("[ViLive] GET {P}: {E}", path, ex.Message);
            return default;
        }
    }

    private async Task FetchEventsAsync()
    {
        var to   = DateTime.UtcNow;
        var from = _eventWindow switch
        {
            "1H" => to.AddHours(-1), "6H" => to.AddHours(-6),
            "7D" => to.AddDays(-7),  _    => to.AddHours(-24)
        };
        var src     = string.IsNullOrEmpty(_eventSource) ? "" : $"&source={Uri.EscapeDataString(_eventSource)}";
        var liveSrc = string.IsNullOrEmpty(_eventSource) ? "" : $"&sources={Uri.EscapeDataString(_eventSource)}";

        var evTask   = GetAsync($"api/events?limit=250&from={from:O}&to={to:O}{src}");
        var liveTask = GetAsync($"api/streams/live?limit=150{liveSrc}");
        await Task.WhenAll(evTask, liveTask);

        var liveList = ExtractEvents(liveTask.Result);
        var evList   = ExtractEvents(evTask.Result);
        if (evList.Count == 0) evList = liveList;
        _state.SetEvents(evList, liveList);
        // Full pull is complete as of `to`; anything ingested after that is a delta.
        // Second precision keeps the cursor Python fromisoformat-safe (no 7-digit ticks).
        _eventCursor = to.ToString("yyyy-MM-ddTHH:mm:ssZ");
    }

    /// <summary>
    /// Incremental events refresh: pull only events ingested since the cursor and merge,
    /// instead of refetching the whole window. Falls back to a full pull on cold start or
    /// when a source filter is active (the delta endpoint is unfiltered).
    /// </summary>
    private async Task FetchEventDeltaAsync()
    {
        if (_eventCursor is null || !string.IsNullOrEmpty(_eventSource))
        {
            await FetchEventsAsync();
            return;
        }

        var resp = await GetAsync($"api/events/delta?since={Uri.EscapeDataString(_eventCursor)}&limit=200");
        if (resp.ValueKind == JsonValueKind.Object &&
            resp.TryGetProperty("cursor", out var c) && c.ValueKind == JsonValueKind.String)
            _eventCursor = c.GetString();

        var delta = ExtractEvents(resp);
        if (delta.Count > 0)
            _state.AppendEvents(delta);
    }

    private async Task FetchAssetsAsync()
    {
        var cacheAge = DateTime.UtcNow - _cachedFleetAt;
        if (_cachedFleetAssets is { Count: > 0 } cached && cacheAge < TimeSpan.FromSeconds(45))
        {
            _state.PatchAssets(cached, _cachedFleetCounts);
            return;
        }

        await _assetHydrationLock.WaitAsync();
        try
        {
            cacheAge = DateTime.UtcNow - _cachedFleetAt;
            if (_cachedFleetAssets is { Count: > 0 } warmed && cacheAge < TimeSpan.FromSeconds(45))
            {
                _state.PatchAssets(warmed, _cachedFleetCounts);
                return;
            }

            // Light sample only — the map fetches its own assets per-viewport now. This set
            // just backs copilot context, the overview mini-map, and footer counts, so a few
            // thousand is plenty (was 25k/5k, a multi-MB pull on every refresh).
            var vesselTask = GetAsync("api/assets/snapshot/latest?asset_type=vessel&limit=4000");
            var aircraftTask = GetAsync("api/assets/snapshot/latest?asset_type=aircraft&limit=1500");
            var cTask = GetAsync("api/assets/counts");

            var countsRoot = await cTask;
            var counts = (0, 0);
            if (countsRoot.ValueKind != JsonValueKind.Undefined &&
                countsRoot.TryGetProperty("counts", out var c))
                counts = (c.TryGetProperty("aircraft", out var ac) ? ac.GetInt32() : 0,
                          c.TryGetProperty("vessel",   out var v)  ? v.GetInt32()  : 0);

            // Counts are cheap and should update the map chrome before the heavy vessel payload lands.
            _state.PatchAssets(new List<AssetDto>(), counts);
            NotifyDebounced();

            var aircraftAssets = ExtractAssets(await aircraftTask);
            if (aircraftAssets.Count > 0)
            {
                _state.PatchAssets(aircraftAssets, counts);
                NotifyDebounced();
            }

            var vesselAssets = ExtractAssets(await vesselTask);
            var assets = MergeAssets(vesselAssets, aircraftAssets);

            if (assets.Count > 0)
            {
                _cachedFleetAssets = assets;
                _cachedFleetCounts = counts;
                _cachedFleetAt = DateTime.UtcNow;
            }

            _state.PatchAssets(assets, counts);
        }
        finally
        {
            _assetHydrationLock.Release();
        }
    }

    private async Task FetchOverviewAsync()
    {
        var ovTask  = GetAsync("api/dashboard/overview");
        var escTask = GetAsync("api/intelligence/escalation");
        await Task.WhenAll(ovTask, escTask);

        var overview   = new DashboardOverviewDto();
        var escalation = new EscalationResponse();

        if (ovTask.Result.ValueKind != JsonValueKind.Undefined)
            overview = JsonSerializer.Deserialize<DashboardOverviewDto>(
                ovTask.Result.GetRawText(), _json) ?? overview;

        if (escTask.Result.ValueKind != JsonValueKind.Undefined)
            escalation = JsonSerializer.Deserialize<EscalationResponse>(
                escTask.Result.GetRawText(), _json) ?? escalation;

        _state.SetOverview(overview, escalation);
    }

    private async Task ConnectHubAsync()
    {
        if (_hub != null) return;
        try
        {
            var hubUrl = $"{_apiBase}/hubs/events";
            _hub = new HubConnectionBuilder()
                .WithUrl(hubUrl, opts =>
        // Lambda captures the _token field and automatically uses the refreshed token on reconnect.
                    opts.AccessTokenProvider = () => Task.FromResult<string?>(_token))
                .WithAutomaticReconnect(new[] { TimeSpan.Zero, TimeSpan.FromSeconds(3), TimeSpan.FromSeconds(10) })
                .Build();

            _hub.On<object>("IngestComplete",     _ => { ScheduleRefresh(events: true, assets: true, overview: true); return FireAsync(OnIngestComplete); });
            _hub.On<object>("IntelligenceUpdate", _ => { ScheduleRefresh(overview: true); return FireAsync(OnIntelligenceUpdate); });
            _hub.On<object>("CorrelationUpdate",  _ => { ScheduleRefresh(overview: true); return FireAsync(OnCorrelationUpdate); });
            _hub.On<object>("NewEvent",           _ => { ScheduleRefresh(events: true, overview: true); return Task.CompletedTask; });
            _hub.On<object>("LiveUpdate",         _ => { ScheduleRefresh(assets: true, overview: true); return Task.CompletedTask; });
            _hub.On<object>("AssetStreamUpdate",  data =>
            {
                try
                {
                    var el = JsonSerializer.SerializeToElement(data, _json);
                    if (el.TryGetProperty("assets", out var arr))
                    {
                        var list = JsonSerializer.Deserialize<List<AssetDto>>(arr.GetRawText(), _json);
                        if (list != null) { _state.PatchAssets(list, _state.Counts); NotifyDebounced(); }
                    }
                }
                catch { }
                return Task.CompletedTask;
            });
            _hub.On<string>("MissionUpdate", msg => { _state.SetJarvisInsight(msg); NotifyDebounced(); return Task.CompletedTask; });
            _hub.Reconnected += _ => { ScheduleRefresh(events: true, assets: true, overview: true); return Task.CompletedTask; };

            await _hub.StartAsync();
            try { await _hub.InvokeAsync("Subscribe", "all"); } catch { }
            _log.LogInformation("[ViLive] Hub connected -> {U}", hubUrl);
        }
        catch (Exception ex) { _log.LogWarning("[ViLive] Hub connect failed: {E}", ex.Message); }
    }

    private void StartRecovery()
    {
        _recoveryTimer = new System.Timers.Timer(60_000) { AutoReset = true };
        _recoveryTimer.Elapsed += (_, _) =>
        {
            var age = DateTime.UtcNow - _state.LastUpdated;
            if (IsConnected && age < TimeSpan.FromMinutes(2)) return;
            ScheduleRefresh(events: true, assets: true, overview: true);
        };
        _recoveryTimer.Start();
    }

    private static Task FireAsync(Func<Task>? ev) => ev?.Invoke() ?? Task.CompletedTask;

    /// <summary>
    /// Coalesces hub-driven refreshes. ORs the requested data sets into the
    /// pending flags and arms a short debounce so a burst of pipeline events
    /// collapses into one refresh instead of N overlapping fetches per circuit.
    /// </summary>
    private void ScheduleRefresh(bool events = false, bool assets = false, bool overview = false)
    {
        lock (_refreshGate)
        {
            _pendEvents   |= events;
            _pendAssets   |= assets;
            _pendOverview |= overview;

            if (_refreshDebounce == null)
            {
                _refreshDebounce = new System.Timers.Timer(600) { AutoReset = false };
                _refreshDebounce.Elapsed += (_, _) => _ = DrainRefreshAsync();
            }
            _refreshDebounce.Stop();
            _refreshDebounce.Start();
        }
    }

    private async Task DrainRefreshAsync()
    {
        lock (_refreshGate)
        {
            // A drain is already running; it re-checks the flags after each fetch,
            // so anything queued now will be picked up there.
            if (_refreshRunning) return;
            _refreshRunning = true;
        }

        try
        {
            while (true)
            {
                bool events, assets, overview;
                lock (_refreshGate)
                {
                    events   = _pendEvents;
                    assets   = _pendAssets;
                    overview = _pendOverview;
                    _pendEvents = _pendAssets = _pendOverview = false;
                    if (!events && !assets && !overview)
                    {
                        _refreshRunning = false;
                        return;
                    }
                }
                await RefreshInBackgroundAsync(events, assets, overview);
            }
        }
        catch (Exception ex)
        {
            lock (_refreshGate) { _refreshRunning = false; }
            _log.LogDebug(ex, "[ViLive] Drain refresh failed");
        }
    }

    private async Task WarmInitialStateAsync()
    {
        await RefreshInBackgroundAsync(includeOverview: true);
        await RefreshInBackgroundAsync(includeEvents: true, includeAssets: true);
    }

    private async Task RefreshInBackgroundAsync(
        bool includeEvents = false,
        bool includeAssets = false,
        bool includeOverview = false)
    {
        try
        {
            var tasks = new List<Task>(3);
            if (includeEvents) tasks.Add(FetchEventDeltaAsync());
            if (includeAssets) tasks.Add(FetchAssetsAsync());
            if (includeOverview) tasks.Add(FetchOverviewAsync());
            if (tasks.Count == 0) return;
            await Task.WhenAll(tasks);
        }
        catch (Exception ex)
        {
            _log.LogDebug(ex, "[ViLive] Background refresh degraded");
        }
        finally
        {
            NotifyDebounced();
        }
    }

    private void NotifyDebounced()
    {
        lock (_refreshGate)
        {
            if (_notifyTimer == null)
            {
                _notifyTimer = new System.Timers.Timer(150) { AutoReset = false };
                _notifyTimer.Elapsed += (_, _) =>
                {
                    try { _state.NotifyChanged(); }
                    catch (Exception ex) { _log.LogWarning("[ViLive] notify: {E}", ex.Message); }
                };
            }
            _notifyTimer.Stop();
            _notifyTimer.Start();
        }
    }

    private List<EventDto> ExtractEvents(JsonElement root)
    {
        if (root.ValueKind == JsonValueKind.Undefined || !root.TryGetProperty("events", out var ev))
            return new();
        return JsonSerializer.Deserialize<List<EventDto>>(ev.GetRawText(), _json) ?? new();
    }

    private List<AssetDto> ExtractAssets(JsonElement root)
    {
        if (root.ValueKind == JsonValueKind.Undefined || !root.TryGetProperty("assets", out var assets))
            return new();
        return JsonSerializer.Deserialize<List<AssetDto>>(assets.GetRawText(), _json) ?? new();
    }

    private static List<AssetDto> MergeAssets(params IEnumerable<AssetDto>[] groups)
    {
        // Dedupe only. ViStateService.PatchAssets does the final ordering + cap,
        // so sorting here would be thrown away — pure wasted work on ~30k items.
        var merged = new Dictionary<string, AssetDto>(StringComparer.OrdinalIgnoreCase);
        foreach (var asset in groups.SelectMany(g => g))
        {
            if (string.IsNullOrWhiteSpace(asset.AssetId))
                continue;
            merged[asset.AssetId] = asset;
        }

        return merged.Values.ToList();
    }

    public async ValueTask DisposeAsync()
    {
        _notifyTimer?.Dispose();
        _refreshDebounce?.Stop();
        _refreshDebounce?.Dispose();
        _recoveryTimer?.Stop();
        _recoveryTimer?.Dispose();
        _initLock.Dispose();
        if (_hub != null) { try { await _hub.DisposeAsync(); } catch { } }
    }
}

