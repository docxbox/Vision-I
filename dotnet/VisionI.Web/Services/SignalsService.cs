using VisionI.Web.Models;

namespace VisionI.Web.Services;

/// <summary>
/// Scoped ViewModel service for the Signals page.
/// Keeps signal retrieval, typed result mapping, and analyst indicator shaping out of the Razor page.
/// </summary>
public sealed class SignalsService
{
    private readonly ApiService _api;
    private readonly ToastService _toast;
    private readonly ILogger<SignalsService> _log;

    public string Tab { get; private set; } = "list";
    public string LimitStr { get; set; } = "50";
    public string Query { get; set; } = "";

    public SignalsResponse? List { get; private set; }
    public SignalsResponse? SearchResult { get; private set; }
    public SignalClustersResponse? Clusters { get; private set; }
    public SignalStatsDto? Stats { get; private set; }
    public CorrelationSummaryDto? Correlation { get; private set; }
    public ConfidenceDistributionDto? ConfidenceDistribution { get; private set; }

    public bool Loading { get; private set; }
    public int CurrentCount => CurrentRows.Count;
    public List<SignalListRowVm> CurrentRows => BuildRows();
    public SignalListRowVm? LeadSignal => CurrentRows.FirstOrDefault();
    public AnalystIndicatorDto? LeadIndicator => LeadSignal is null ? null : BuildIndicator(LeadSignal);
    public int ClusteredCount => CurrentRows.Count(s => !string.IsNullOrWhiteSpace(s.ClusterId));

    public string WhatChangedSummary
        => LeadIndicator?.ObservationSummary ?? "No signal data is visible under the current tab.";
    public string WhyItMattersSummary
        => LeadIndicator?.AssessmentSummary
        ?? "Signals help analysts spot weak indicators, cluster related evidence, and surface semantic matches before they mature into full incidents.";
    public string WhatIsConnectedSummary
        => LeadIndicator?.CorrelationSummary
        ?? $"{CurrentCount} items are visible in the current tab, spanning raw signals, semantic results, clusters, and correlation summaries.";
    public string RecommendedActionSummary
        => LeadIndicator?.RecommendedAction
        ?? "Use semantic search when you have a hypothesis, and use clusters when you want to understand whether multiple weak signals are converging.";

    public event Action? OnChanged;

    public SignalsService(ApiService api, ToastService toast, ILogger<SignalsService> log)
    {
        _api = api;
        _toast = toast;
        _log = log;
    }

    public async Task SwitchTabAsync(string tab)
    {
        Tab = tab;
        OnChanged?.Invoke();

        if (tab == "clusters" && Clusters is null) await LoadClustersAsync();
        else if (tab == "stats" && (Stats is null || Correlation is null || ConfidenceDistribution is null)) await LoadStatsAsync();
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
            var limit = int.TryParse(LimitStr, out var parsed) ? Math.Clamp(parsed, 1, 200) : 50;
            List = await _api.GetSignalsAsync(limit: limit);
        }
        catch (Exception ex)
        {
            _log.LogError(ex, "SignalsService.LoadAsync failed");
            _toast.ShowError("Could not load signals.");
            List = null;
        }
        finally
        {
            Loading = false;
            OnChanged?.Invoke();
        }
    }

    public async Task SearchAsync()
    {
        if (string.IsNullOrWhiteSpace(Query)) return;
        Loading = true;
        OnChanged?.Invoke();
        try
        {
            SearchResult = await _api.SearchSignalsAsync(Query, limit: 20);
        }
        catch (Exception ex)
        {
            _log.LogError(ex, "SignalsService.SearchAsync failed");
            _toast.ShowError("Signal search failed.");
            SearchResult = null;
        }
        finally
        {
            Loading = false;
            OnChanged?.Invoke();
        }
    }

    public async Task LoadClustersAsync()
    {
        Loading = true;
        OnChanged?.Invoke();
        try
        {
            Clusters = await _api.GetSignalClustersAsync();
        }
        catch (Exception ex)
        {
            _log.LogError(ex, "SignalsService.LoadClustersAsync failed");
            _toast.ShowError("Could not load signal clusters.");
            Clusters = null;
        }
        finally
        {
            Loading = false;
            OnChanged?.Invoke();
        }
    }

    public async Task LoadStatsAsync()
    {
        Loading = true;
        OnChanged?.Invoke();
        try
        {
            var t1 = _api.GetSignalStatsAsync();
            var t2 = _api.GetSignalCorrelationSummaryAsync();
            var t3 = _api.GetSignalConfidenceDistributionAsync();
            await Task.WhenAll(t1, t2, t3);
            Stats = t1.Result;
            Correlation = t2.Result;
            ConfidenceDistribution = t3.Result;
        }
        catch (Exception ex)
        {
            _log.LogError(ex, "SignalsService.LoadStatsAsync failed");
            _toast.ShowError("Could not load signal stats.");
        }
        finally
        {
            Loading = false;
            OnChanged?.Invoke();
        }
    }

    public AnalystIndicatorDto SignalIndicator(SignalListRowVm row) => row.Indicator ?? BuildIndicator(row);

    private List<SignalListRowVm> BuildRows()
    {
        return Tab switch
        {
            "search" => BuildSignalRows(SearchResult?.Signals ?? new()),
            "clusters" => BuildClusterRows(Clusters?.Clusters ?? new()),
            _ => BuildSignalRows(List?.Signals ?? new()),
        };
    }

    private static List<SignalListRowVm> BuildSignalRows(IEnumerable<SignalDto> signals)
        => signals.Select(signal =>
        {
            var confidence = signal.Confidence;
            var driver = !string.IsNullOrWhiteSpace(signal.ClusterId) ? "clustered signal"
                : confidence >= 0.75 ? "strong confidence"
                : !string.IsNullOrWhiteSpace(signal.Source) ? $"{signal.Source} pickup"
                : "emerging signal";
            var action = confidence >= 0.8 ? "investigate"
                : !string.IsNullOrWhiteSpace(signal.ClusterId) ? "compare cluster"
                : "watch";

            return new SignalListRowVm(
                signal.SignalId ?? "",
                string.IsNullOrWhiteSpace(signal.Title) ? "Signal" : signal.Title,
                string.IsNullOrWhiteSpace(signal.SignalType) ? "signal" : signal.SignalType,
                signal.Source ?? "",
                signal.ClusterId ?? "",
                confidence,
                driver,
                action,
                signal.Actors.Count,
                string.IsNullOrWhiteSpace(signal.LocationName) ? null : signal.LocationName,
                1,
                1,
                signal.Indicator);
        }).ToList();

    private static List<SignalListRowVm> BuildClusterRows(IEnumerable<SignalClusterDto> clusters)
        => clusters.Select(cluster =>
            new SignalListRowVm(
                cluster.ClusterId ?? "",
                string.IsNullOrWhiteSpace(cluster.RepresentativeTitle) ? $"Cluster {cluster.ClusterId}" : cluster.RepresentativeTitle,
                "cluster",
                string.Join(", ", cluster.Sources.Take(2)),
                cluster.ClusterId ?? "",
                cluster.CompositeScore,
                cluster.SharedActors.Count > 0 ? "actor overlap" : "semantic convergence",
                cluster.SignalCount >= 4 ? "investigate" : "compare cluster",
                cluster.SharedActors.Count,
                null,
                Math.Max(cluster.Sources.Count, 1),
                cluster.SignalCount,
                cluster.Indicator))
            .ToList();

    private static AnalystIndicatorDto BuildIndicator(SignalListRowVm row)
    {
        var severity = row.Confidence switch
        {
            >= 0.85 => "critical",
            >= 0.65 => "high",
            >= 0.45 => "medium",
            _ => "low",
        };
        var trajectory = row.Action switch
        {
            "investigate" => "rising",
            "compare cluster" => "watch",
            _ => "stable"
        };
        var correlationSummary = row.Type.Equals("cluster", StringComparison.OrdinalIgnoreCase)
            ? $"{row.EventCount} clustered signal(s), {row.SourceCount} source(s), and {row.ActorCount} shared actor(s) are grouped together."
            : $"{row.SourceCount} source(s), {row.ActorCount} actor(s), and {(string.IsNullOrWhiteSpace(row.ClusterId) ? "no cluster" : $"cluster {row.ClusterId}")} support this signal.";

        return AnalystIndicatorFactory.CreateCustom(
            id: row.Id,
            label: row.Title,
            category: "signal",
            indicatorKind: "signal",
            evidenceKind: string.IsNullOrWhiteSpace(row.ClusterId) ? "observed" : "correlated",
            assessmentKind: row.Type.Equals("cluster", StringComparison.OrdinalIgnoreCase) ? "signal_cluster" : "signal_detection",
            severity: severity,
            driver: row.Driver,
            trajectory: trajectory,
            recommendedAction: row.Action,
            score: row.Confidence,
            confidence: row.Confidence,
            corroboration: Math.Min(1.0, (row.SourceCount * 0.2) + (row.ActorCount * 0.12) + (row.EventCount * 0.08)),
            linked: new IndicatorLinkCountsDto
            {
                Signals = row.EventCount,
                Sources = row.SourceCount,
                Actors = row.ActorCount,
                Regions = string.IsNullOrWhiteSpace(row.Location) ? 0 : 1,
            },
            observationSummary: row.Type.Equals("cluster", StringComparison.OrdinalIgnoreCase)
                ? $"{row.Title} groups related weak signals into a common cluster."
                : $"{row.Title} is the leading signal in the current result set.",
            assessmentSummary: row.Type.Equals("cluster", StringComparison.OrdinalIgnoreCase)
                ? $"{row.Title} suggests multiple weak indicators are converging."
                : $"{row.Title} carries confidence {row.Confidence:0.00} and is best treated as {severity} priority evidence.",
            correlationSummary: correlationSummary,
            region: row.Location,
            summary: row.Title);
    }
}

public sealed record SignalListRowVm(
    string Id,
    string Title,
    string Type,
    string Source,
    string ClusterId,
    double Confidence,
    string Driver,
    string Action,
    int ActorCount,
    string? Location,
    int SourceCount,
    int EventCount = 1,
    AnalystIndicatorDto? Indicator = null);
