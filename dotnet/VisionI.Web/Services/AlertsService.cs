using System.Text.Json;
using VisionI.Web.Models;

namespace VisionI.Web.Services;

/// <summary>
/// Scoped ViewModel service for the Alerts page.
/// Subscribes to ViStateService.OnStateChanged so alerts auto-refresh
/// when new alerts arrive via SignalR, fixing audit bug 2.5.
/// Full filter support including ShowAcknowledged, fixing bug 4.8.
/// </summary>
public sealed class AlertsService : IDisposable
{
    private readonly ViStateService _state;
    private readonly ApiService _api;
    private readonly ToastService _toast;
    private readonly ILogger<AlertsService> _log;
    public string SeverityFilter { get; set; } = "";
    public bool ShowAcknowledged { get; set; } = false;
    public bool Loading { get; private set; } = true;
    private readonly HashSet<string> _actioning = new(StringComparer.OrdinalIgnoreCase);
    public bool ActionInProgress => _actioning.Count > 0;
    public bool IsActioning(string alertId) => _actioning.Contains(alertId);
    public UnrestWatchDto? Watch { get; private set; }
    public List<AlertDto> Alerts { get; private set; } = new();
    public List<UnrestAlertDto> CorroboratedAlerts => Watch?.Alerts.Take(6).ToList() ?? new();
    public List<AnalystIndicatorDto> CorroboratedIndicators => CorroboratedAlerts.Select(AnalystIndicatorFactory.FromAlert).ToList();
    public List<AnalystIndicatorDto> AlertIndicators => PagedAlerts.Select(AlertIndicator).ToList();
    public AlertSummaryVm Summary { get; private set; } = new();
    public int LinkedEntityCount => Alerts.Count(a => !string.IsNullOrWhiteSpace(a.Entity));
    public int LinkedLocationCount => Alerts.Count(a => !string.IsNullOrWhiteSpace(a.Location));
    public AlertDto? LeadAlert => PagedAlerts.FirstOrDefault();
    public AnalystIndicatorDto? LeadCorroboratedIndicator => CorroboratedIndicators.FirstOrDefault();
    public AnalystIndicatorDto? LeadAlertIndicator => AlertIndicators.FirstOrDefault();
    public string WhatChangedSummary
    {
        get
        {
            if (LeadCorroboratedIndicator is { } leadIndicator)
                return leadIndicator.ObservationSummary;

            var lead = LeadAlertIndicator;
            return lead is null
                ? "No alerts match the current filters."
                : lead.ObservationSummary;
        }
    }
    public string WhyItMattersSummary =>
        LeadCorroboratedIndicator is { } leadIndicator
            ? leadIndicator.AssessmentSummary
            : LeadAlertIndicator is { } rawIndicator
            ? rawIndicator.AssessmentSummary
            : Watch?.Overview is { } overview
            ? $"{overview.CorroboratedAlerts} corroborated alert(s) and {overview.HotRegionCount} hotspot region(s) are contributing to the current watch picture."
            : Summary.Critical > 0 || Summary.High > 0
            ? $"{Summary.Critical + Summary.High} alert(s) are high-severity or critical, so analyst acknowledgement and routing matter right now."
            : "The console keeps anomaly and narrative alerts visible even when the current batch is less severe.";
    public string WhatIsConnectedSummary =>
        LeadCorroboratedIndicator is { } leadIndicator
            ? leadIndicator.CorrelationSummary
            : LeadAlertIndicator is { } rawIndicator
            ? rawIndicator.CorrelationSummary
            : $"{Summary.Total} alerts, {LinkedEntityCount} linked entities, and {LinkedLocationCount} linked locations are in the current review set.";
    public string RecommendedActionSummary =>
        LeadCorroboratedIndicator?.RecommendedAction
        ?? LeadAlertIndicator?.RecommendedAction
        ?? "Clear critical and high alerts first, then drill into linked entities or operations when the alert suggests a broader incident.";

    public event Action? OnChanged;

    private System.Threading.Timer? _debounce;

    public AlertsService(ViStateService state, ApiService api, ToastService toast, ILogger<AlertsService> log)
    {
        _state = state;
        _api = api;
        _toast = toast;
        _log = log;

    // Subscribe to real-time state changes, debounced to avoid glitch on every SignalR event
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
            var qs = $"api/alerts?limit=100";
            if (!string.IsNullOrEmpty(SeverityFilter)) qs += $"&severity={Uri.EscapeDataString(SeverityFilter)}";
            if (!ShowAcknowledged) qs += "&acknowledged=false"; // fixes bug 4.8

            var response = await _api.GetAsync<AlertsResponse>(qs);
            if (response is not null)
            {
                Alerts = response.Alerts ?? new();
                BuildSummary();
            }
            else
            {
                // Fall back to state cache
                SyncFromState();
            }
        }
        catch (Exception ex)
        {
            _log.LogError(ex, "AlertsService.LoadAsync failed");
            _toast.ShowError("Could not load alerts.");
            SyncFromState();
        }
        finally
        {
            Loading = false;
            OnChanged?.Invoke();
        }
    }

    public async Task ApplyFiltersAsync()
    {
        Page = 1;
        await LoadAsync();
    }

    public const int PageSize = 25;
    public int Page { get; private set; } = 1;
    public int TotalPages => (int)Math.Ceiling(Alerts.Count / (double)PageSize);
    public List<AlertDto> PagedAlerts => Alerts.Skip((Page - 1) * PageSize).Take(PageSize).ToList();

    public void GoToPage(int page)
    {
        Page = Math.Clamp(page, 1, Math.Max(TotalPages, 1));
        OnChanged?.Invoke();
    }

    public async Task AckAsync(string alertId)
    {
        _actioning.Add(alertId);
        OnChanged?.Invoke();
        try
        {
            await _api.AckAlertAsync(alertId);
            _toast.ShowSuccess("Alert acknowledged.");
            await LoadAsync();
        }
        catch (Exception ex)
        {
            _log.LogError(ex, "AckAsync failed for {AlertId}", alertId);
            _toast.ShowError("Failed to acknowledge alert.");
        }
        finally { _actioning.Remove(alertId); OnChanged?.Invoke(); }
    }

    public async Task ResolveAsync(string alertId)
    {
        _actioning.Add(alertId);
        OnChanged?.Invoke();
        try
        {
            await _api.ResolveAlertAsync(alertId);
            _toast.ShowSuccess("Alert resolved.");
            await LoadAsync();
        }
        catch (Exception ex)
        {
            _log.LogError(ex, "ResolveAsync failed for {AlertId}", alertId);
            _toast.ShowError("Failed to resolve alert.");
        }
        finally { _actioning.Remove(alertId); OnChanged?.Invoke(); }
    }

    public async Task EscalateAsync(string alertId)
    {
        _actioning.Add(alertId);
        OnChanged?.Invoke();
        try
        {
            await _api.PostAsync<object>($"api/alerts/{alertId}/escalate", new { });
            _toast.ShowSuccess("Alert escalated.");
            await LoadAsync();
        }
        catch (Exception ex)
        {
            _log.LogError(ex, "EscalateAsync failed for {AlertId}", alertId);
            _toast.ShowError("Failed to escalate alert.");
        }
        finally { _actioning.Remove(alertId); OnChanged?.Invoke(); }
    }

    public async Task DismissAsync(string alertId)
    {
        _actioning.Add(alertId);
        OnChanged?.Invoke();
        try
        {
            await _api.PostAsync<object>($"api/alerts/{alertId}/dismiss", new { });
            _toast.ShowSuccess("Alert dismissed.");
            await LoadAsync();
        }
        catch (Exception ex)
        {
            _log.LogError(ex, "DismissAsync failed for {AlertId}", alertId);
            _toast.ShowError("Failed to dismiss alert.");
        }
        finally { _actioning.Remove(alertId); OnChanged?.Invoke(); }
    }

    public async Task ScanAsync()
    {
        _actioning.Add("__scan__");
        OnChanged?.Invoke();
        try
        {
            await _api.PostAsync<JsonElement?>("api/alerts/scan", new { });
            _toast.ShowSuccess("Anomaly scan triggered.");
            await Task.Delay(800);
            await LoadAsync();
        }
        catch (Exception ex)
        {
            _log.LogError(ex, "ScanAsync failed");
            _toast.ShowError("Could not trigger scan.");
        }
        finally { _actioning.Remove("__scan__"); OnChanged?.Invoke(); }
    }

    public static string GetSeverityClass(string? severity) => severity?.ToLower() switch
    {
        "critical" => "severity-critical",
        "high"     => "severity-high",
        "medium"   => "severity-medium",
        "low"      => "severity-low",
        _          => "severity-info",
    };

    public static string FormatTime(string? raw)
    {
        if (!DateTime.TryParse(raw, out var dt)) return "--";
        var diff = DateTime.UtcNow - dt.ToUniversalTime();
        return diff.TotalMinutes < 1 ? "just now" :
               diff.TotalHours   < 1 ? $"{(int)diff.TotalMinutes}m ago" :
               diff.TotalDays    < 1 ? $"{(int)diff.TotalHours}h ago" :
                                       $"{(int)diff.TotalDays}d ago";
    }

    private void SyncFromState()
    {
        Alerts = _state.Alerts
            .OrderByDescending(a => a.DetectedAt)
            .Where(a => string.IsNullOrEmpty(SeverityFilter) ||
                        string.Equals(a.Severity, SeverityFilter, StringComparison.OrdinalIgnoreCase))
            .Where(a => ShowAcknowledged || !a.Acknowledged)
            .ToList();
        BuildSummary();
    }

    private void BuildSummary()
    {
        Summary = new AlertSummaryVm
        {
            Total      = Alerts.Count,
            Critical   = Alerts.Count(a => string.Equals(a.Severity, "critical", StringComparison.OrdinalIgnoreCase)),
            High       = Alerts.Count(a => string.Equals(a.Severity, "high",     StringComparison.OrdinalIgnoreCase)),
            Medium     = Alerts.Count(a => string.Equals(a.Severity, "medium",   StringComparison.OrdinalIgnoreCase)),
            Low        = Alerts.Count(a => string.Equals(a.Severity, "low",      StringComparison.OrdinalIgnoreCase)),
            Unacked    = Alerts.Count(a => !a.Acknowledged),
        };
    }

    public AnalystIndicatorDto AlertIndicator(AlertDto alert)
        => alert.Indicator ?? BuildAlertIndicator(alert);

    private static AnalystIndicatorDto BuildAlertIndicator(AlertDto alert)
    {
        var alertType = NormalizeAlertType(alert.AlertType);
        var driver = alertType switch
        {
            "sentiment_deterioration" => "sentiment deterioration",
            "geographic_cluster" => "geographic clustering",
            "entity_spike" => "actor convergence",
            "source_silence" => "source silence",
            "coordinated_amplification" => "coordinated amplification",
            "escalation_risk" => "escalation pressure",
            _ => string.IsNullOrWhiteSpace(alert.AlertType) ? "corroborated anomaly" : alert.AlertType.Replace("_", " "),
        };
        var severity = string.IsNullOrWhiteSpace(alert.Severity) ? "medium" : alert.Severity.ToLowerInvariant();
        var trajectory = severity is "critical" or "high" ? "rising" : "stable";
        var sourceCount = alert.Sources?.Count ?? 0;
        var corroboration = Math.Min(1.0,
            (sourceCount * 0.18) +
            (alert.EventCount * 0.08) +
            (Math.Max(alert.ZScore, 0) * 0.08));
        var action = severity switch
        {
            "critical" => "triage now",
            "high" => "investigate",
            "medium" => "review",
            _ => "monitor"
        };

        return AnalystIndicatorFactory.CreateCustom(
            id: alert.AlertId ?? alert.Title,
            label: alert.Title,
            category: "alert",
            indicatorKind: "alert",
            evidenceKind: sourceCount > 1 || alert.EventCount > 1 ? "correlated" : "observed",
            assessmentKind: alertType,
            severity: severity,
            driver: driver,
            trajectory: trajectory,
            recommendedAction: action,
            score: alert.ZScore,
            confidence: corroboration,
            corroboration: corroboration,
            linked: new IndicatorLinkCountsDto
            {
                Alerts = 1,
                Sources = sourceCount,
                Events = alert.EventCount,
                Actors = string.IsNullOrWhiteSpace(alert.Entity) ? 0 : 1,
                Regions = string.IsNullOrWhiteSpace(alert.Location) ? 0 : 1,
            },
            observationSummary: $"{sourceCount} source(s) observed {alert.Title.ToLowerInvariant()} with {alert.EventCount} linked event(s).",
            assessmentSummary: $"{alert.Title} is classified as {severity} severity with z-score {alert.ZScore:0.0}.",
            correlationSummary: $"{sourceCount} source(s), {(string.IsNullOrWhiteSpace(alert.Entity) ? "no linked entity" : $"entity {alert.Entity}")}, and {(string.IsNullOrWhiteSpace(alert.Location) ? "no linked region" : $"location {alert.Location}")} support this alert.",
            region: alert.Location,
            summary: alert.Description ?? alert.Title);
    }

    private static string NormalizeAlertType(string? alertType)
        => (alertType ?? "").Trim().ToLowerInvariant() switch
        {
            "sentiment_shift" => "sentiment_deterioration",
            "geo_cluster" => "geographic_cluster",
            "" => "corroborated_alert",
            var raw => raw
        };

    private void HandleStateChanged()
    {
        // Debounce: only sync once per 2s burst of SignalR events to prevent glitching
        _debounce?.Dispose();
        _debounce = new System.Threading.Timer(_ =>
        {
            SyncFromState();
            OnChanged?.Invoke();
        }, null, 2000, System.Threading.Timeout.Infinite);
    }

    public void Dispose()
    {
        _state.OnStateChanged -= HandleStateChanged;
        _debounce?.Dispose();
    }
}

public sealed class AlertSummaryVm
{
    public int Total  { get; init; }
    public int Critical { get; init; }
    public int High   { get; init; }
    public int Medium { get; init; }
    public int Low    { get; init; }
    public int Unacked { get; init; }
}

