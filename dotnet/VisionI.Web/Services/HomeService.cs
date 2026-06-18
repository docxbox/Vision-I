using System.Text.Json;
using VisionI.Web.Models;

namespace VisionI.Web.Services;

public sealed class HomeService : IDisposable
{
    private readonly ViStateService _state;
    private readonly ViLiveSession _liveSession;
    private readonly ApiService _api;
    private readonly RegionGeoService _geo;
    private readonly AuthService _auth;
    private readonly ILogger<HomeService> _log;

    private string _timeWindow = "24H";
    private string? _sourceFilter;
    private bool _initialized;
    private bool _disposed;
    private DateTime _countryHeatFetchedAt = DateTime.MinValue;
    private int _countryHeatDays = -1;
    private DateTime _watchFetchedAt = DateTime.MinValue;

    public static IReadOnlyList<string> TimeWindows { get; } = ["1H", "6H", "24H", "7D"];

    public ViStateService State => _state;
    public ViLiveSession LiveSession => _liveSession;
    public bool IsConnected => _liveSession.IsConnected;
    public bool BriefLoading { get; private set; }
    public string TimeWindow => _timeWindow;
    public string? SourceFilter => _sourceFilter;
    public DateTime? BriefTimestamp { get; private set; }
    public List<string> BriefBullets { get; private set; } = [];
    public List<EventDto> WindowEvents { get; private set; } = [];
    public List<EventDto> FeedPreview { get; private set; } = [];
    public List<ResolvedEscalationPoint> ResolvedHotspots { get; private set; } = [];
    public List<RiskLadderItem> RiskLadder { get; private set; } = [];
    public List<object> CountryHeatmap { get; private set; } = [];
    public List<EntityDto> TopActors { get; private set; } = [];
    public List<DetectedSituationDto> SituationCases { get; private set; } = [];
    public UnrestWatchDto? Watch { get; private set; }
    public List<AnalystIndicatorDto> SituationIndicators { get; private set; } = [];
    public List<AnalystIndicatorDto> RegionIndicators { get; private set; } = [];
    public List<AnalystIndicatorDto> NarrativeIndicators { get; private set; } = [];
    public List<AnalystIndicatorDto> AlertIndicators { get; private set; } = [];
    public List<AnalystIndicatorDto> ActorIndicators { get; private set; } = [];
    public double SentVolatility { get; private set; }
    public double AverageHotspotScore => ResolvedHotspots.Count == 0 ? 0 : ResolvedHotspots.Average(h => h.Score);
    public double AverageEventRisk => WindowEvents.Count == 0 ? 0 : WindowEvents.Average(ev => ev.RiskScore ?? 0);

    public event Action? OnChanged;

    public HomeService(
        ViStateService state,
        ViLiveSession liveSession,
        ApiService api,
        RegionGeoService geo,
        AuthService auth,
        ILogger<HomeService> log)
    {
        _state = state;
        _liveSession = liveSession;
        _api = api;
        _geo = geo;
        _auth = auth;
        _log = log;

        _state.OnStateChanged += HandleStateChanged;
    }

    private DateTime _lastLoaded = DateTime.MinValue;
    public async Task EnsureLoadedAsync()
    {
        if (DateTime.UtcNow - _lastLoaded < TimeSpan.FromMinutes(2)) return;
        _lastLoaded = DateTime.UtcNow;
        await InitializeAsync(null);
    }

    public async Task InitializeAsync(string? domainSource)
    {
        _sourceFilter = string.IsNullOrWhiteSpace(domainSource) ? null : domainSource;

        if (!_initialized)
        {
            await _liveSession.InitAsync(_auth.AccessToken ?? string.Empty);
            _initialized = true;
        }

        RefreshDerivedState();

        if (WindowEvents.Count == 0)
            await LoadWindowEventsDirectAsync();

        await EnsureCountryHeatmapAsync();
        await RefreshSituationsAsync();
        await RefreshWatchAsync();

        if (BriefBullets.Count == 0)
            await RefreshBriefAsync();

        NotifyChanged();
    }

    public async Task ApplySourceFilterAsync(string? domainSource)
    {
        var next = string.IsNullOrWhiteSpace(domainSource) ? null : domainSource;
        if (string.Equals(_sourceFilter, next, StringComparison.OrdinalIgnoreCase))
            return;

        _sourceFilter = next;
        RefreshDerivedState();
        await _liveSession.RefreshEventsAsync(_timeWindow, _sourceFilter);
        await EnsureCountryHeatmapAsync(force: true);
        await RefreshSituationsAsync(force: true);
        await RefreshWatchAsync(force: true);
        NotifyChanged();
    }

    public async Task SetWindowAsync(string window)
    {
        if (string.Equals(_timeWindow, window, StringComparison.OrdinalIgnoreCase))
            return;

        _timeWindow = window;
        await _liveSession.RefreshEventsAsync(window, _sourceFilter);
        RefreshDerivedState();
        await EnsureCountryHeatmapAsync(force: true);
        await RefreshSituationsAsync(force: true);
        await RefreshWatchAsync(force: true);
        NotifyChanged();
    }

    public async Task RefreshWatchAsync(bool force = false)
    {
        if (!force && Watch is not null && DateTime.UtcNow - _watchFetchedAt < TimeSpan.FromMinutes(2))
            return;

        try
        {
            Watch = await _api.GetUnrestWatchAsync(WindowHoursForWatch);
            _watchFetchedAt = DateTime.UtcNow;
            BuildIndicators();
        }
        catch (Exception ex)
        {
            _log.LogDebug(ex, "Unrest watch refresh failed");
        }
    }

    public async Task RefreshSituationsAsync(bool force = false)
    {
        if (!force && SituationCases.Count > 0 && DateTime.UtcNow - _watchFetchedAt < TimeSpan.FromMinutes(2))
            return;

        try
        {
            var payload = await _api.GetSituationsAsync(limit: 6, status: "active");
            SituationCases = payload?.Situations ?? [];
            BuildIndicators();
        }
        catch (Exception ex)
        {
            _log.LogDebug(ex, "Situation refresh failed");
        }
    }

    public async Task RefreshBriefAsync()
    {
        BriefLoading = true;
        NotifyChanged();

        try
        {
            var payload = await _api.GetAsync<JsonElement>("api/copilot/summary?window_hours=6");
            var summary = payload.ValueKind == JsonValueKind.Object && payload.TryGetProperty("summary", out var summaryProp)
                ? summaryProp.GetString()
                : payload.ValueKind == JsonValueKind.Object && payload.TryGetProperty("insight", out var insightProp)
                    ? insightProp.GetString()
                    : _state.JarvisInsight;

            BriefBullets = SplitBrief(summary);
            BriefTimestamp = DateTime.UtcNow;
        }
        catch (Exception ex)
        {
            _log.LogWarning(ex, "Failed to refresh Jarvis brief");
            BriefBullets = SplitBrief(_state.JarvisInsight);
            BriefTimestamp = DateTime.UtcNow;
        }
        finally
        {
            BriefLoading = false;
            NotifyChanged();
        }
    }

    public async Task AskJarvisAsync(string question)
    {
        if (string.IsNullOrWhiteSpace(question))
            return;

        BriefLoading = true;
        NotifyChanged();

        try
        {
            var response = await _api.CopilotAskAsync(new CopilotAskDto { Question = question.Trim() });
            BriefBullets = SplitBrief(response?.Answer);
            BriefTimestamp = DateTime.UtcNow;
        }
        catch (Exception ex)
        {
            _log.LogWarning(ex, "Failed to ask Jarvis from dashboard");
            BriefBullets = ["Jarvis could not complete the request right now."];
            BriefTimestamp = DateTime.UtcNow;
        }
        finally
        {
            BriefLoading = false;
            NotifyChanged();
        }
    }

    public async Task EnsureCountryHeatmapAsync(bool force = false)
    {
        var days = CountryHeatmapDays;
        if (!force &&
            CountryHeatmap.Count > 0 &&
            _countryHeatDays == days &&
            DateTime.UtcNow - _countryHeatFetchedAt < TimeSpan.FromMinutes(2))
        {
            return;
        }

        try
        {
            var payload = await _api.GetSentimentCountryHeatmapAsync(days);
            if (payload is not JsonElement json || json.ValueKind != JsonValueKind.Object)
                return;

            var points = new List<object>();
            if (json.TryGetProperty("countries", out var countries) && countries.ValueKind == JsonValueKind.Array)
            {
                foreach (var country in countries.EnumerateArray())
                {
                    if (!TryGetDouble(country, "lat", out var lat) || !TryGetDouble(country, "lon", out var lon))
                        continue;

                    var count = TryGetInt(country, "count", out var countValue)
                        ? countValue
                        : TryGetInt(country, "event_count", out var eventCount)
                            ? eventCount
                            : 1;
                    var sentiment = TryGetDouble(country, "sentiment", out var sentimentValue)
                        ? sentimentValue
                        : TryGetDouble(country, "avg_score", out var avgScore)
                            ? avgScore
                            : 0.5;
                    var riskScore = TryGetDouble(country, "risk_score", out var riskValue) ? riskValue : 0d;
                    var name = country.TryGetProperty("country", out var countryName) ? countryName.GetString() : null;

                    points.Add(new
                    {
                        country = name,
                        lat,
                        lon,
                        sentiment,
                        count = Math.Max(count, 1),
                        riskScore,
                    });
                }
            }

            CountryHeatmap = points;
            _countryHeatDays = days;
            _countryHeatFetchedAt = DateTime.UtcNow;
        }
        catch (Exception ex)
        {
            _log.LogDebug(ex, "Country heatmap refresh failed");
        }
    }

    public void RefreshDerivedState()
    {
        WindowEvents = _state.Events
            .Where(ev => string.IsNullOrWhiteSpace(_sourceFilter) ||
                         ev.Source?.Contains(_sourceFilter, StringComparison.OrdinalIgnoreCase) == true)
            .OrderByDescending(ev => ev.RiskScore ?? 0)
            .ThenByDescending(ev => ev.InfluenceScore ?? 0)
            .ThenByDescending(ev => ParseTimestamp(ev.Timestamp))
            .ToList();

        FeedPreview = WindowEvents.Take(10).ToList();

        TopActors = _state.Overview.Entities.Entities
            .Where(e => !string.IsNullOrEmpty(e.Name))
            .OrderByDescending(e => e.MentionCount)
            .Take(3)
            .ToList();

        var timeline = _state.Overview.SentimentTimeline.Timeline;
        SentVolatility = 0;
        if (timeline is { Count: > 1 })
        {
            var scores = timeline.Select(bucket => bucket.AvgScore).ToList();
            var mean = scores.Average();
            SentVolatility = Math.Sqrt(scores.Average(score => (score - mean) * (score - mean)));
        }

        ResolvedHotspots = _geo.ResolveHotspots(
                _state.Escalation.Scores.OrderByDescending(score => score.Score).Take(8),
                WindowEvents)
            .OrderByDescending(item => item.Score)
            .ToList();

        RiskLadder = BuildRiskLadder(ResolvedHotspots, WindowEvents);
        BuildIndicators();
    }

    private int WindowHoursForWatch => _timeWindow switch
    {
        "1H" => 1,
        "6H" => 6,
        "24H" => 24,
        "7D" => 168,
        _ => 24,
    };

    private void BuildIndicators()
    {
        SituationIndicators = SituationCases
            .Select(s => s.Indicator ?? AnalystIndicatorFactory.CreateCustom(
                id: s.SituationId ?? s.Title ?? "situation",
                label: s.Title ?? "Situation",
                category: "situation",
                indicatorKind: "situation",
                evidenceKind: "correlated",
                assessmentKind: "correlated_situation",
                severity: string.IsNullOrWhiteSpace(s.Severity) ? "medium" : s.Severity.ToLowerInvariant(),
                driver: s.ActorIds.Count > 1 ? "actor convergence" : "event clustering",
                trajectory: s.RiskScore >= 0.65 ? "rising" : "stable",
                recommendedAction: s.RiskScore >= 0.65 ? "investigate" : "monitor",
                score: s.RiskScore,
                confidence: Math.Min(1.0, 0.3 + (s.EventCount * 0.08)),
                corroboration: Math.Min(1.0, 0.2 + (s.EventCount * 0.10)),
                linked: new IndicatorLinkCountsDto
                {
                    Events = s.EventCount,
                    Actors = s.ActorIds.Count,
                    Regions = string.IsNullOrWhiteSpace(s.Region) ? 0 : 1
                },
                observationSummary: $"{s.EventCount} event(s) were grouped into {s.Title ?? "this situation"}.",
                assessmentSummary: $"{s.Title ?? "This situation"} is being tracked as {(s.Severity ?? "medium").ToLowerInvariant()} severity.",
                correlationSummary: $"{s.EventCount} linked event(s) and {s.ActorIds.Count} actor(s) support this case.",
                region: s.Region,
                summary: s.Description ?? s.Title ?? "Situation"))
            .Take(5)
            .ToList();
        RegionIndicators = Watch?.Regions.Take(5).Select(AnalystIndicatorFactory.FromRegion).ToList() ?? [];
        NarrativeIndicators = Watch?.Narratives.Take(5).Select(AnalystIndicatorFactory.FromNarrative).ToList() ?? [];
        AlertIndicators = Watch?.Alerts.Take(5).Select(AnalystIndicatorFactory.FromAlert).ToList() ?? [];
        ActorIndicators = Watch?.Actors.Take(5).Select(AnalystIndicatorFactory.FromActor).ToList() ?? [];
    }

    private int CountryHeatmapDays => _timeWindow switch
    {
        "7D" => 7,
        _ => 1,
    };

    private void HandleStateChanged()
    {
        if (_disposed)
            return;

        RefreshDerivedState();
        NotifyChanged();
    }

    private void NotifyChanged() => OnChanged?.Invoke();

    private List<RiskLadderItem> BuildRiskLadder(
        IReadOnlyList<ResolvedEscalationPoint> hotspots,
        IReadOnlyList<EventDto> events)
    {
        var average = hotspots.Count == 0 ? 0 : hotspots.Average(item => item.Score);

        return hotspots
            .Take(5)
            .Select(hotspot =>
            {
                var matching = events
                    .Where(ev => _geo.MatchesRegion(ev, hotspot.Region))
                    .ToList();
                var series = BuildBucketSeries(matching);
                return new RiskLadderItem(
                    hotspot,
                    hotspot.Score - average,
                    BuildSparklinePath(series),
                    series.Count > 1 && series[^1] > series[0] ? "^" : series.Count > 1 && series[^1] < series[0] ? "v" : "*",
                    series.Count > 1 && series[^1] > series[0] ? "up" : series.Count > 1 && series[^1] < series[0] ? "down" : "flat");
            })
            .ToList();
    }

    private static List<int> BuildBucketSeries(IReadOnlyList<EventDto> events)
    {
        const int bucketCount = 6;
        var buckets = Enumerable.Repeat(0, bucketCount).ToArray();
        var end = DateTime.UtcNow;
        var start = end.AddHours(-24);
        var bucketWidth = TimeSpan.FromHours(24d / bucketCount);

        foreach (var ev in events)
        {
            var timestamp = ParseTimestamp(ev.Timestamp);
            if (timestamp < start || timestamp > end)
                continue;

            var index = (int)((timestamp - start).TotalMinutes / bucketWidth.TotalMinutes);
            index = Math.Clamp(index, 0, bucketCount - 1);
            buckets[index]++;
        }

        return buckets.ToList();
    }

    private static string BuildSparklinePath(IReadOnlyList<int> values)
    {
        if (values.Count == 0)
            return "M0,14 L96,14";

        var max = Math.Max(values.Max(), 1);
        var step = values.Count == 1 ? 0 : 96d / (values.Count - 1);
        var points = values
            .Select((value, index) =>
            {
                var x = index * step;
                var y = 26 - ((value / (double)max) * 22) - 2;
                return $"{x:0.##},{y:0.##}";
            })
            .ToList();

        return $"M{string.Join(" L", points)}";
    }

    private static List<string> SplitBrief(string? summary)
    {
        if (string.IsNullOrWhiteSpace(summary))
            return [];

        var bullets = summary
            .Split(['\r', '\n'], StringSplitOptions.RemoveEmptyEntries)
            .Select(line => line.Trim().TrimStart('-', '*', '\u2022', ' '))
            .Where(line => line.Length > 0)
            .Take(3)
            .ToList();

        if (bullets.Count > 0)
            return bullets;

        return summary
            .Split(". ", StringSplitOptions.RemoveEmptyEntries)
            .Select(sentence => sentence.Trim())
            .Where(sentence => sentence.Length > 0)
            .Take(3)
            .ToList();
    }

    private static bool TryGetDouble(JsonElement element, string name, out double value)
    {
        value = 0;
        return element.TryGetProperty(name, out var property) &&
               property.ValueKind == JsonValueKind.Number &&
               property.TryGetDouble(out value);
    }

    private static bool TryGetInt(JsonElement element, string name, out int value)
    {
        value = 0;
        return element.TryGetProperty(name, out var property) &&
               property.ValueKind == JsonValueKind.Number &&
               property.TryGetInt32(out value);
    }

    private static DateTime ParseTimestamp(string? value)
        => DateTime.TryParse(value, out var parsed) ? parsed.ToUniversalTime() : DateTime.MinValue;

    async Task LoadWindowEventsDirectAsync()
    {
        try
        {
            var resp = await _api.GetAsync<EventsApiResponse>(
                $"api/events?limit=100&offset=0&sort_by=risk_score");
            if (resp?.Events is { Count: > 0 })
            {
                WindowEvents = resp.Events
                    .OrderByDescending(e => e.RiskScore ?? 0)
                    .ToList();
                FeedPreview = WindowEvents.Take(10).ToList();
                TopActors = WindowEvents
                    .Where(e => !string.IsNullOrEmpty(e.Source))
                    .GroupBy(e => e.Source!)
                    .Select(g => new EntityDto { Name = g.Key, Type = "source", MentionCount = g.Count() })
                    .OrderByDescending(x => x.MentionCount)
                    .Take(5)
                    .ToList();
                NotifyChanged();
            }
        }
        catch (Exception ex)
        {
            _log.LogDebug(ex, "Direct events fallback failed");
        }
    }

    private class EventsApiResponse
    {
        [System.Text.Json.Serialization.JsonPropertyName("events")]
        public List<EventDto> Events { get; set; } = new();
        [System.Text.Json.Serialization.JsonPropertyName("total")]
        public int Total { get; set; }
    }

    public void Dispose()
    {
        _disposed = true;
        _state.OnStateChanged -= HandleStateChanged;
    }
}

internal static class HomeIndicatorExtensions
{
    public static string OrIfBlank(this string? value, string fallback)
        => string.IsNullOrWhiteSpace(value) ? fallback : value!;
}

public sealed record RiskLadderItem(
    ResolvedEscalationPoint Hotspot,
    double Delta,
    string SparklinePath,
    string TrendArrow,
    string TrendClass);
