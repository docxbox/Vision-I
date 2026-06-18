using VisionI.Web.Models;

namespace VisionI.Web.Services;

/// <summary>
/// Scoped ViewModel service for the Map View page.
/// Owns the filtered marker list and coordinates JS map updates.
/// Every filter setter materializes the new marker set and fires OnMarkersChanged
/// so Map.razor can call JS.InvokeVoidAsync("intelMap.addMarkers", ...), fixing bug 2.4.
/// </summary>
public sealed class MapService : IDisposable
{
    private readonly ViStateService _state;
    private readonly ViLiveSession _liveSession;
    private readonly ApiService _api;
    private readonly ToastService _toast;
    private readonly ILogger<MapService> _log;
    private bool _showPositive = true;
    private bool _showNegative = true;
    private bool _showNeutral  = true;
    private string _timeFilter = "24h";
    public bool Loading { get; private set; } = true;
    public bool HasLocation { get; private set; }
    public UnrestWatchDto? Watch { get; private set; }
    public List<MapMarkerVm> FilteredMarkers { get; private set; } = new();
    public List<UnrestRegionDto> HotRegions => Watch?.Regions.Take(6).ToList() ?? new();
    public string TopRegion { get; private set; } = "No active region";
    public int TopRegionCount { get; private set; }
    public int DistinctRegionCount { get; private set; }
    public int DistinctSourceCount { get; private set; }
    public int HighRiskCount => FilteredMarkers.Count(m => m.Weight >= 0.7);
    public int NegativeCount => FilteredMarkers.Count(m => m.Sentiment < 0.4);
    public int AircraftCount => _state.Counts.Aircraft;
    public int VesselCount => _state.Counts.Vessels;
    // Viewport-driven assets: only what's inside the current map bounds, fetched on pan/zoom.
    // Replaces the old "render the entire global 30k fleet" path.
    public List<AssetDto> ViewportAssets { get; private set; } = new();
    private CancellationTokenSource? _viewportCts;
    private const double MinAssetZoom = 3.5;

    public List<AssetDto> GeoAssets => ViewportAssets
        .Where(a => a.LastLat.HasValue && a.LastLon.HasValue)
        .ToList();
    public MapMarkerVm? LeadMarker => FilteredMarkers.FirstOrDefault();
    public string WhatChangedSummary =>
        Watch?.Overview?.TopRegion is { Length: > 0 } hotRegion
            ? $"{hotRegion} is the lead unrest region, while {FilteredMarkers.Count} mapped events remain visible in the current filter window."
            : LeadMarker is null
                ? "No geolocated events are visible under the current filters."
                : $"{LeadMarker.Title} is the lead geolocated event, with {FilteredMarkers.Count} mapped events in the active window and {TopRegionCount} concentrated in {TopRegion}.";

    public string WhyItMattersSummary =>
        Watch?.Overview is { } overview
            ? $"{overview.HotRegionCount} region(s) and {overview.CorroboratedAlerts} corroborated alert(s) are contributing to the current unrest picture."
            : HighRiskCount > 0
                ? $"{HighRiskCount} mapped events are high risk, so regional concentration and source spread need immediate review."
                : "The map helps analysts understand where activity is concentrating even when the current slice is not highly escalatory.";

    public string WhatIsConnectedSummary =>
        $"{DistinctRegionCount} mapped regions, {DistinctSourceCount} source families, {NegativeCount} negative-sentiment markers, and {HotRegions.Count} unrest hotspot summaries are tied into the current geographic slice.";

    public string RecommendedActionSummary =>
        Watch?.Overview?.RecommendedAction is { Length: > 0 } recommendation
            ? recommendation
            : LeadMarker is null
                ? "Widen the time window or refresh live ingest to restore a usable geographic picture."
                : $"Center on {TopRegion}, then pivot into Live Events or Operations for the highest-risk incidents in that region.";

    public string? RegionFilter { get; set; }

    public bool ShowPositive
    {
        get => _showPositive;
        set { _showPositive = value; Materialize(); }
    }
    public bool ShowNegative
    {
        get => _showNegative;
        set { _showNegative = value; Materialize(); }
    }
    public bool ShowNeutral
    {
        get => _showNeutral;
        set { _showNeutral = value; Materialize(); }
    }
    public string TimeFilter
    {
        get => _timeFilter;
        set { _timeFilter = value; Materialize(); }
    }

    /// <summary>
    /// Fired after every filter change or data load.
    /// Map.razor subscribes to this to call JS map update.
    /// </summary>
    public VisionI.Web.Models.AirspaceClosuresResponse?  AirspaceData  { get; private set; }
    public VisionI.Web.Models.JammingHeatmapResponse?    JammingData   { get; private set; }
    public VisionI.Web.Models.ReroutesResponse?          RerouteData   { get; private set; }
    public VisionI.Web.Models.SatellitePassesResponse?   SatelliteData { get; private set; }
    public bool LoadingAirspace { get; private set; }

    public event Action? OnMarkersChanged;
    public event Action? OnChanged;

    public MapService(
        ViStateService state,
        ViLiveSession liveSession,
        ApiService api,
        ToastService toast,
        ILogger<MapService> log)
    {
        _state = state;
        _liveSession = liveSession;
        _api = api;
        _toast = toast;
        _log = log;
        _state.OnStateChanged += HandleStateChanged;
    }

    private DateTime _lastLoaded = DateTime.MinValue;
    public async Task EnsureLoadedAsync()
    {
        if (DateTime.UtcNow - _lastLoaded < TimeSpan.FromMinutes(2)) return;
        _lastLoaded = DateTime.UtcNow;
        await LoadAsync();
    }

    public async Task LoadAsync()
    {
        Loading = true;
        OnChanged?.Invoke();

        try
        {
            Watch = await _api.GetUnrestWatchAsync();
            if (_state.Events.Count == 0)
                await _liveSession.RefreshEventsAsync();
            // Assets are now viewport-driven (see LoadAssetsInViewportAsync), fired by the
            // map's moveend callback — no global fleet pull here.

            Materialize();
        }
        catch (Exception ex)
        {
            _log.LogError(ex, "MapService.LoadAsync failed");
            _toast.ShowError("Could not load map events.");
        }
        finally
        {
            Loading = false;
            OnChanged?.Invoke();
        }
    }

    /// <summary>
    /// Fetches assets within the current map viewport. Called (debounced) from the map's
    /// moveend JS callback. Cancels any in-flight fetch so fast panning doesn't pile up.
    /// </summary>
    public async Task LoadAssetsInViewportAsync(
        double south, double west, double north, double east, double zoom)
    {
        // Zoomed too far out → the bbox is basically the whole world; skip the heavy fetch.
        if (zoom < MinAssetZoom)
        {
            if (ViewportAssets.Count > 0)
            {
                ViewportAssets = new();
                OnMarkersChanged?.Invoke();
            }
            return;
        }

        _viewportCts?.Cancel();
        var cts = new CancellationTokenSource();
        _viewportCts = cts;

        try
        {
            var limit = zoom >= 6 ? 2500 : 1200;
            var resp = await _api.GetAssetsInBoundsAsync(
                minLat: south, maxLat: north, minLon: west, maxLon: east,
                limit: limit, ct: cts.Token);
            if (cts.IsCancellationRequested) return;
            ViewportAssets = resp?.Assets ?? new();
            OnMarkersChanged?.Invoke();
        }
        catch (OperationCanceledException) { /* superseded by a newer viewport */ }
        catch (Exception ex)
        {
            _log.LogWarning(ex, "MapService.LoadAssetsInViewportAsync failed");
        }
    }

    public async Task LoadAirspaceAsync()
    {
        LoadingAirspace = true;
        OnChanged?.Invoke();
        try
        {
            var t1 = _api.GetAirspaceAsync();
            var t2 = _api.GetJammingHeatmapAsync();
            var t3 = _api.GetReroutesAsync();
            await Task.WhenAll(t1, t2, t3);
            AirspaceData = t1.Result;
            JammingData  = t2.Result;
            RerouteData  = t3.Result;
        }
        catch (Exception ex)
        {
            _log.LogError(ex, "MapService.LoadAirspaceAsync failed");
            _toast.ShowError("Domain intelligence layer failed to load.");
        }
        finally { LoadingAirspace = false; OnChanged?.Invoke(); }
    }

    public async Task LoadSatelliteAsync(
        double latMin = -90, double lonMin = -180,
        double latMax = 90,  double lonMax = 180)
    {
        LoadingAirspace = true;
        OnChanged?.Invoke();
        try
        {
            SatelliteData = await _api.GetSatellitePassesAsync(latMin, lonMin, latMax, lonMax);
        }
        catch (Exception ex)
        {
            _log.LogError(ex, "MapService.LoadSatelliteAsync failed");
            _toast.ShowError("Satellite pass data failed to load.");
        }
        finally { LoadingAirspace = false; OnChanged?.Invoke(); }
    }

    /// <summary>
    /// Filters and projects events into map markers.
    /// Always called synchronously; fires both OnChanged and OnMarkersChanged.
    /// </summary>
    public void Materialize()
    {
        var cutoff = _timeFilter switch
        {
            "1h"  => DateTime.UtcNow.AddHours(-1),
            "6h"  => DateTime.UtcNow.AddHours(-6),
            "24h" => DateTime.UtcNow.AddHours(-24),
            "7d"  => DateTime.UtcNow.AddDays(-7),
            _     => DateTime.UtcNow.AddHours(-24),
        };

        FilteredMarkers = _state.Events
            .Where(e => DateTime.TryParse(e.Timestamp, out var ts) && ts.ToUniversalTime() >= cutoff)
            .Where(e => e.Location?.Lat is not null && e.Location?.Lon is not null)
            .Where(e => FilterBySentiment(e.Sentiment?.Score))
            .Where(e => RegionFilter is null ||
                        (e.Location?.Name ?? e.Location?.Country ?? "")
                            .Contains(RegionFilter, StringComparison.OrdinalIgnoreCase))
            .OrderByDescending(e => e.RiskScore ?? 0)
            .Take(300)
            .Select(e => new MapMarkerVm(
                Id:        e.EventId ?? "",
                Lat:       e.Location!.Lat!.Value,
                Lng:       e.Location!.Lon!.Value,
                Title:     e.Title ?? "Untitled",
                Sentiment: e.Sentiment?.Score ?? 0.5,
                Source:    e.Source ?? "unknown",
                Location:  e.Location.Name ?? e.Location.Country ?? "Unknown",
                Timestamp: FormatTime(e.Timestamp),
                Weight:    (double)(e.RiskScore ?? 0.1)))
            .ToList();

        HasLocation = FilteredMarkers.Count > 0;
        DistinctRegionCount = FilteredMarkers
            .Select(m => m.Location)
            .Where(v => !string.IsNullOrWhiteSpace(v))
            .Distinct(StringComparer.OrdinalIgnoreCase)
            .Count();
        DistinctSourceCount = FilteredMarkers
            .Select(m => m.Source)
            .Where(v => !string.IsNullOrWhiteSpace(v))
            .Distinct(StringComparer.OrdinalIgnoreCase)
            .Count();
        var topRegion = FilteredMarkers
            .Where(m => !string.IsNullOrWhiteSpace(m.Location))
            .GroupBy(m => m.Location, StringComparer.OrdinalIgnoreCase)
            .OrderByDescending(g => g.Count())
            .ThenByDescending(g => g.Max(x => x.Weight))
            .FirstOrDefault();
        TopRegion = topRegion?.Key ?? "No active region";
        TopRegionCount = topRegion?.Count() ?? 0;
        OnChanged?.Invoke();
        OnMarkersChanged?.Invoke();
    }

    private bool FilterBySentiment(double? score)
    {
        if (!score.HasValue) return _showNeutral;
        if (score.Value > 0.6)  return _showPositive;
        if (score.Value < 0.4)  return _showNegative;
        return _showNeutral;
    }

    public static string GetSentimentClass(double sentiment)
        => sentiment > 0.6 ? "positive" : sentiment < 0.4 ? "negative" : "neutral";

    public static string FormatTime(string? raw)
    {
        if (!DateTime.TryParse(raw, out var dt)) return "--";
        var diff = DateTime.UtcNow - dt.ToUniversalTime();
        return diff.TotalMinutes < 1 ? "just now" :
               diff.TotalHours   < 1 ? $"{(int)diff.TotalMinutes}m ago" :
               diff.TotalDays    < 1 ? $"{(int)diff.TotalHours}h ago" :
                                       $"{(int)diff.TotalDays}d ago";
    }

    private void HandleStateChanged()
    {
        Materialize();
    }

    public void Dispose()
    {
        _state.OnStateChanged -= HandleStateChanged;
        _viewportCts?.Cancel();
        _viewportCts?.Dispose();
    }
}

public sealed record MapMarkerVm(
    string Id, double Lat, double Lng,
    string Title, double Sentiment,
    string Source,
    string Location, string Timestamp, double Weight);
