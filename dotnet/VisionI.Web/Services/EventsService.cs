using VisionI.Web.Models;

namespace VisionI.Web.Services;

/// <summary>
/// Scoped ViewModel service for the Live Events page.
/// Uses the backend's intelligence-feed projection so operators browse
/// feed-worthy items instead of raw telemetry churn.
/// </summary>
public sealed class EventsService : IDisposable
{
    private readonly ViStateService _state;
    private readonly ApiService _api;
    private readonly ToastService _toast;
    private readonly ILogger<EventsService> _log;
    private static readonly string[] _staticKnownSources =
        { "news", "reddit", "hackernews", "gdelt", "usgs", "stocks", "opensky", "ais", "rss", "youtube", "crypto", "nws", "firms", "twitter" };

    private readonly HashSet<string> _knownSources = new(_staticKnownSources, StringComparer.OrdinalIgnoreCase);

    private string _search = "";
    private string _source = "";
    private string _eventType = "";
    private string _riskFilter = "";
    private string _sentiment = "";
    private string _timeWindow = "all";
    private string _feedMode = "priority";
    private string _sortField = "time";
    private bool _sortAsc;
    private CancellationTokenSource? _searchCts;
    private bool _disposed;

    public const int PageSize = 50;

    public int Page { get; private set; } = 1;
    public int TotalCount { get; private set; }
    public int TotalPages => Math.Max(1, (int)Math.Ceiling(TotalCount / (double)PageSize));

    public bool Loading { get; private set; } = true;
    public bool DetailLoading { get; private set; }
    public bool ViewModeGrid { get; private set; }
    public int PageResultCount => FilteredEvents.Count;
    public int SourceCatalogCount => _knownSources.Count;

    public List<EventDto> FilteredEvents { get; private set; } = new();
    public EventDto? SelectedEvent { get; private set; }
    public EventFullDto? SelectedEventFull { get; private set; }
    public TriageRecordDto? SelectedTriage { get; private set; }
    public List<EventFeedGroupDto> FeedGroups { get; private set; } = new();
    public bool DetailPanelOpen => SelectedEvent is not null;

    public string Search { get => _search; set => _search = value; }
    public string Source { get => _source; set => _source = value; }
    public string EventType { get => _eventType; set => _eventType = value; }
    public string RiskFilter { get => _riskFilter; set => _riskFilter = value; }
    public string Sentiment { get => _sentiment; set => _sentiment = value; }
    public string TimeWindow { get => _timeWindow; set => _timeWindow = value; }
    public string FeedMode { get => _feedMode; set => _feedMode = value; }
    public string SortField => _sortField;
    public bool SortAscending => _sortAsc;
    public IEnumerable<string> AvailableSources => _knownSources.OrderBy(static s => s);
    public bool GroupByCase => string.Equals(_feedMode, "by_case", StringComparison.OrdinalIgnoreCase);

    public event Action? OnChanged;

    public EventsService(ViStateService state, ApiService api, ToastService toast, ILogger<EventsService> log)
    {
        _state = state;
        _api = api;
        _toast = toast;
        _log = log;
        SeedSourcesFromState();
        _state.OnStateChanged += HandleStateChanged;
    }

    private DateTime _lastLoaded = DateTime.MinValue;
    public async Task EnsureLoadedAsync(string? focusEventId = null)
    {
        if (DateTime.UtcNow - _lastLoaded < TimeSpan.FromMinutes(2)) return;
        _lastLoaded = DateTime.UtcNow;
        await LoadAsync(focusEventId);
    }

    public async Task LoadAsync(string? focusEventId = null)
    {
        Loading = true;
        NotifyChanged();

        try
        {
            await LoadPageAsync();

            if (!string.IsNullOrWhiteSpace(focusEventId))
            {
                var target = FilteredEvents.FirstOrDefault(e =>
                    string.Equals(e.EventId, focusEventId, StringComparison.OrdinalIgnoreCase));

                if (target is null)
                    target = await _api.GetAsync<EventDto>($"api/events/{Uri.EscapeDataString(focusEventId)}");

                if (target is not null)
                    await SelectEventAsync(target);
            }
        }
        catch (Exception ex)
        {
            _log.LogError(ex, "EventsService.LoadAsync failed");
            _toast.ShowError("Failed to load events.");
        }
        finally
        {
            Loading = false;
            NotifyChanged();
        }
    }

    public async Task ApplyFiltersAsync()
    {
        Page = 1;
        await LoadPageAsync();
    }

    public async Task GoToPageAsync(int page)
    {
        var next = Math.Clamp(page, 1, TotalPages);
        if (next == Page && FilteredEvents.Count > 0)
            return;

        Page = next;
        await LoadPageAsync();
    }

    public async Task RefreshAsync() => await LoadPageAsync();

    public async Task OnSearchInputAsync(string value)
    {
        _search = value;
        _searchCts?.Cancel();
        _searchCts?.Dispose();
        _searchCts = new CancellationTokenSource();
        var token = _searchCts.Token;

        try
        {
            await Task.Delay(350, token);
            if (token.IsCancellationRequested) return;
            Page = 1;
            await LoadPageAsync();
        }
        catch (OperationCanceledException) { }
    }

    public void SetSort(string field)
    {
        if (_sortField == field)
            _sortAsc = !_sortAsc;
        else
        {
            _sortField = field;
            _sortAsc = false;
        }

        SortCurrentPage();
        NotifyChanged();
    }

    public async Task SetFeedModeAsync(string mode)
    {
        var normalized = mode?.Trim().ToLowerInvariant() switch
        {
            "priority" => "priority",
            "by_case" => "by_case",
            _ => "latest",
        };

        if (_feedMode == normalized)
            return;

        _feedMode = normalized;
        Page = 1;
        await LoadPageAsync();
    }

    public void ToggleViewMode()
    {
        ViewModeGrid = !ViewModeGrid;
        NotifyChanged();
    }

    public async Task SelectEventAsync(EventDto? evt)
    {
        SelectedEvent = evt;
        SelectedEventFull = null;
        SelectedTriage = null;
        DetailLoading = evt is not null;
        NotifyChanged();

        if (evt is null || string.IsNullOrWhiteSpace(evt.EventId))
        {
            DetailLoading = false;
            NotifyChanged();
            return;
        }

        try
        {
            SelectedEventFull = await _api.GetEventFullAsync(evt.EventId, socialLimit: 12, similarLimit: 6);
            SelectedTriage = await _api.GetTriageRecordAsync(evt.EventId);
        }
        catch (Exception ex)
        {
            _log.LogWarning(ex, "Failed to load live event detail bundle for {EventId}", evt.EventId);

            try
            {
                var contextTask = _api.GetEventContextAsync(evt.EventId);
                var socialTask = _api.GetEventSocialAsync(evt.EventId, limit: 8);
                var explainTask = _api.CopilotExplainAsync(evt.EventId);
                await Task.WhenAll(contextTask, socialTask, explainTask);

                SelectedEventFull = new EventFullDto
                {
                    EventId = evt.EventId,
                    Context = await contextTask,
                    Social = await socialTask,
                    Explain = await explainTask,
                    FetchedAt = DateTime.UtcNow.ToString("O"),
                };
                SelectedTriage = await _api.GetTriageRecordAsync(evt.EventId);
            }
            catch (Exception fallbackEx)
            {
                _log.LogWarning(fallbackEx, "Fallback live event detail load failed for {EventId}", evt.EventId);
            }
        }
        finally
        {
            DetailLoading = false;
            NotifyChanged();
        }
    }

    public void CloseDetail()
    {
        SelectedEvent = null;
        SelectedEventFull = null;
        SelectedTriage = null;
        DetailLoading = false;
        NotifyChanged();
    }

    public static string GetSentimentClass(EventDto? e)
    {
        var score = e?.Sentiment?.Score ?? 0.5;
        return score > 0.6 ? "positive" : score < 0.4 ? "negative" : "neutral";
    }

    public static string GetRiskClass(double? risk) => (risk ?? 0) switch
    {
        >= 0.7 => "high",
        >= 0.4 => "medium",
        _ => "low",
    };

    public static string FormatTime(string? raw)
    {
        if (!DateTime.TryParse(raw, out var dt)) return "--";
        var diff = DateTime.UtcNow - dt.ToUniversalTime();
        return diff.TotalMinutes < 1 ? "just now" :
               diff.TotalHours < 1 ? $"{(int)diff.TotalMinutes}m ago" :
               diff.TotalDays < 1 ? $"{(int)diff.TotalHours}h ago" :
               $"{(int)diff.TotalDays}d ago";
    }

    private async Task LoadPageAsync()
    {
        Loading = true;
        NotifyChanged();

        try
        {
            SeedSourcesFromState();

            var (from, to) = ResolveWindow();

            // Clamp page using estimated TotalPages before first fetch if TotalCount is known
            if (TotalCount > 0)
                Page = Math.Clamp(Page, 1, TotalPages);

            var response = await _api.GetEventFeedAsync(
                source: string.IsNullOrWhiteSpace(_source) ? null : _source,
                eventType: string.IsNullOrWhiteSpace(_eventType) ? null : _eventType,
                query: string.IsNullOrWhiteSpace(_search) ? null : _search.Trim(),
                sentiment: string.IsNullOrWhiteSpace(_sentiment) ? null : _sentiment,
                from: from,
                to: to,
                sort: GroupByCase ? "priority" : _feedMode,
                groupBy: GroupByCase ? "case" : "none",
                limit: PageSize,
                offset: (Page - 1) * PageSize);

            TotalCount = response?.Total ?? 0;
            // Re-clamp after we have accurate TotalCount, then re-fetch only if page shifted
            var clampedPage = Math.Clamp(Page, 1, TotalPages);
            if (clampedPage != Page)
            {
                Page = clampedPage;
                response = await _api.GetEventFeedAsync(
                    source: string.IsNullOrWhiteSpace(_source) ? null : _source,
                    eventType: string.IsNullOrWhiteSpace(_eventType) ? null : _eventType,
                    query: string.IsNullOrWhiteSpace(_search) ? null : _search.Trim(),
                    sentiment: string.IsNullOrWhiteSpace(_sentiment) ? null : _sentiment,
                    from: from,
                    to: to,
                    sort: GroupByCase ? "priority" : _feedMode,
                    groupBy: GroupByCase ? "case" : "none",
                    limit: PageSize,
                    offset: (Page - 1) * PageSize);
                TotalCount = response?.Total ?? TotalCount;
            }
            var allEvents = response?.Events ?? new();
            FilteredEvents = string.IsNullOrEmpty(_riskFilter) ? allEvents : _riskFilter switch
            {
                "high"   => allEvents.Where(e => (e.RiskScore ?? 0) >= 0.7).ToList(),
                "medium" => allEvents.Where(e => (e.RiskScore ?? 0) >= 0.4 && (e.RiskScore ?? 0) < 0.7).ToList(),
                "low"    => allEvents.Where(e => (e.RiskScore ?? 0) < 0.4).ToList(),
                _ => allEvents
            };
            FeedGroups = response?.Groups ?? new();

            MergeSources(FilteredEvents.Select(e => e.Source));
            if (!GroupByCase)
                SortCurrentPage();

            if (SelectedEvent is not null)
            {
                var updated = FilteredEvents.FirstOrDefault(e =>
                    string.Equals(e.EventId, SelectedEvent.EventId, StringComparison.OrdinalIgnoreCase));
                if (updated is not null)
                    SelectedEvent = updated;
            }
        }
        catch (Exception ex)
        {
            _log.LogError(ex, "EventsService.LoadPageAsync failed");
            _toast.ShowError("Could not refresh the event feed.");
        }
        finally
        {
            Loading = false;
            NotifyChanged();
        }
    }

    private void SortCurrentPage()
    {
        IEnumerable<EventDto> ordered = _sortField switch
        {
            "risk" => _sortAsc
                ? FilteredEvents.OrderBy(e => e.RiskScore ?? 0)
                : FilteredEvents.OrderByDescending(e => e.RiskScore ?? 0),
            "priority" => _sortAsc
                ? FilteredEvents.OrderBy(e => e.FeedScore ?? 0)
                : FilteredEvents.OrderByDescending(e => e.FeedScore ?? 0),
            "sentiment" => _sortAsc
                ? FilteredEvents.OrderBy(e => e.Sentiment?.Score ?? 0.5)
                : FilteredEvents.OrderByDescending(e => e.Sentiment?.Score ?? 0.5),
            _ => _sortAsc
                ? FilteredEvents.OrderBy(e => ParseTimestamp(e.Timestamp))
                : FilteredEvents.OrderByDescending(e => ParseTimestamp(e.Timestamp)),
        };

        FilteredEvents = ordered.ToList();
    }

    private (string? From, string? To) ResolveWindow()
    {
        var to = DateTime.UtcNow;
        var from = _timeWindow switch
        {
            "1h" => to.AddHours(-1),
            "6h" => to.AddHours(-6),
            "24h" => to.AddHours(-24),
            "7d" => to.AddDays(-7),
            "30d" => to.AddDays(-30),
            _ => (DateTime?)null,
        };

        return (
            from?.ToString("O"),
            from is null ? null : to.ToString("O")
        );
    }

    private void SeedSourcesFromState()
    {
        MergeSources(_state.Overview.Stats.BySource.Keys);
        MergeSources(_state.Events.Select(e => e.Source));
    }

    private void MergeSources(IEnumerable<string?> sources)
    {
        foreach (var source in sources)
        {
            if (!string.IsNullOrWhiteSpace(source))
                _knownSources.Add(source);
        }
    }

    private void HandleStateChanged()
    {
        if (_disposed || Loading)
            return;

        SeedSourcesFromState();
        NotifyChanged();
    }

    private void NotifyChanged() => OnChanged?.Invoke();

    private static DateTime ParseTimestamp(string? value)
        => DateTime.TryParse(value, out var parsed) ? parsed.ToUniversalTime() : DateTime.MinValue;

    public void Dispose()
    {
        _disposed = true;
        _state.OnStateChanged -= HandleStateChanged;
        _searchCts?.Cancel();
        _searchCts?.Dispose();
    }
}
