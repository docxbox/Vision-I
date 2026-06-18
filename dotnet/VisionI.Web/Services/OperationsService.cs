using VisionI.Web.Models;

namespace VisionI.Web.Services;

public sealed class OperationsService : IDisposable
{
    private readonly ApiService _api;
    private readonly ViStateService _state;
    private readonly AuthService _auth;
    private CancellationTokenSource? _refreshCts;
    private CancellationTokenSource? _selectCts;

    public OperationsOverviewDto Overview { get; private set; } = new();
    public List<OperationQueueItemDto> Queue { get; private set; } = new();
    public List<DecisionDto> Decisions { get; private set; } = new();
    public List<AlertDto> Alerts { get; private set; } = new();
    public OperationQueueItemDto? SelectedItem { get; private set; }
    public DetectedSituationDto? SelectedSituation { get; private set; }
    public EventFullDto? SelectedFull { get; private set; }
    public EventDto? SelectedEvent { get; private set; }
    public TriageRecordDto? SelectedTriage { get; private set; }
    public bool LoadingReco { get; private set; }
    public bool Submitting { get; private set; }
    public bool SavingTriage { get; private set; }
    public bool Refreshing { get; private set; }
    public string CoaText { get; set; } = "";
    public string Rationale { get; set; } = "";
    public string TriageStatus { get; set; } = "reviewing";
    public string TriagePriority { get; set; } = "medium";
    public string TriageNote { get; set; } = "";
    public string Msg { get; private set; } = "";

    public string ThreatPosture => Overview.ThreatPosture?.ToUpperInvariant() ?? "STEADY";
    public string? SituationCaseTitle => SelectedSituation?.Title ?? SelectedFull?.Context?.Situation?.Title;
    public string? SituationCaseId => SelectedSituation?.SituationId;
    public int LinkedAlertCount => SelectedItem?.Alerts?.Count ?? 0;
    public int LinkedNarrativeCount => SelectedItem?.Narratives?.Count ?? 0;
    public int RelatedEventCount => SelectedFull?.Context?.RelatedEvents?.Count ?? 0;
    public int SimilarDecisionCount => SelectedFull?.Similar?.SimilarDecisions?.Count ?? 0;
    public int PlaybookCount => Math.Max(SelectedFull?.Context?.Playbooks?.Count ?? 0, SelectedItem?.Playbook is null ? 0 : 1);
    public int SupportingSignalCount => SelectedFull?.Intelligence?.Signals?.Count ?? SelectedItem?.SignalCount ?? 0;
    public int ClusteredSignalCount =>
        SelectedFull?.Intelligence?.Signals?
            .Where(s => !string.IsNullOrWhiteSpace(s.ClusterId))
            .Select(s => s.ClusterId!)
            .Distinct(StringComparer.OrdinalIgnoreCase)
            .Count()
        ?? 0;
    public int PhysicalAssetCount => SelectedFull?.Intelligence?.PhysicalSignals?.Assets?.Count ?? 0;
    public int SourceFamilyCount => SelectedFull?.Context?.Amplification?.SourceFamilies ?? SelectedItem?.SourceFamilyCount ?? 0;
    public string RecommendedAction => SelectedItem?.RecommendedNextAction ?? SelectedFull?.Recommend?.PrimaryRecommendation ?? "Continue monitoring";
    public string Driver => SelectedItem is null
        ? "awaiting selection"
        : LinkedAlertCount > 0 ? "corroborated alerts"
        : SupportingSignalCount > 0 ? "signal evidence"
        : LinkedNarrativeCount > 0 ? "narrative pressure"
        : (SelectedItem.RiskScore >= 0.7 ? "incident spike" : "emerging operation");
    public string Next => SelectedItem is null
        ? "select item"
        : SelectedTriage?.Status is "escalated" ? "operations"
        : (SelectedItem.RiskScore >= 0.75 || LinkedAlertCount > 0) ? "decide now"
        : "hold watch";
    public string ActionIndicator => !string.IsNullOrWhiteSpace(RecommendedAction)
        ? RecommendedAction
        : "review and record decision";
    public IEnumerable<CourseOfActionDto> CoursesOfAction => SelectedItem?.CoursesOfAction ?? Enumerable.Empty<CourseOfActionDto>();
    public IEnumerable<DecisionDto> SimilarDecisions => SelectedFull?.Similar?.SimilarDecisions ?? Enumerable.Empty<DecisionDto>();
    public IEnumerable<PlaybookDefinitionDto> ContextPlaybooks => SelectedFull?.Context?.Playbooks ?? Enumerable.Empty<PlaybookDefinitionDto>();
    public IEnumerable<SignalDto> RelatedSignals => SelectedFull?.Intelligence?.Signals ?? Enumerable.Empty<SignalDto>();
    public AnalystIndicatorDto SelectedIndicator
        => BuildIndicator(SelectedItem, SelectedEvent, SelectedTriage, SelectedFull);

    public string WhyItMatters
    {
        get
        {
            if (SelectedItem is null)
                return "Pick an item from the operations queue to understand the current posture.";

            return SelectedIndicator.AssessmentSummary;
        }
    }

    public event Action? OnChanged;

    public OperationsService(ApiService api, ViStateService state, AuthService auth)
    {
        _api = api;
        _state = state;
        _auth = auth;
        _state.OnStateChanged += HandleStateChanged;
    }

    public void BuildQueue()
    {
        if (Overview.Items.Count > 0)
        {
            Queue = Overview.Items
                .OrderByDescending(x => x.RiskScore)
                .ThenByDescending(x => x.PriorityScore)
                .ToList();
            Notify();
            return;
        }

        Queue = _state.Events
            .Where(e => (e.RiskScore ?? 0) > 0.5 || (e.ConfidenceScore ?? 0) > 0.7)
            .OrderByDescending(e => e.RiskScore ?? 0)
            .Take(20)
            .Select(e => new OperationQueueItemDto
            {
                Id = e.EventId ?? "",
                Title = e.Title ?? e.EventId ?? "event",
                EventType = e.EventType,
                Timestamp = e.Timestamp,
                RiskScore = e.RiskScore ?? 0,
                ConfidenceScore = e.ConfidenceScore ?? 0,
                PriorityScore = Math.Max(e.RiskScore ?? 0, e.ConfidenceScore ?? 0),
                SignalCount = e.SignalCount ?? e.SupportingSignals?.Count ?? 0,
                SupportingSignals = e.SupportingSignals ?? new(),
                SourceFamilyCount = 1,
                Actors = e.Actors,
                Location = e.Location,
                Sentiment = e.Sentiment,
                RecommendedNextAction = "Escalate to analyst",
            })
            .ToList();

        Notify();
    }

    private DateTime _lastLoaded = DateTime.MinValue;
    public async Task EnsureLoadedAsync()
    {
        if (DateTime.UtcNow - _lastLoaded < TimeSpan.FromMinutes(2)) return;
        _lastLoaded = DateTime.UtcNow;
        await RefreshAllAsync();
    }

    public async Task RefreshAllAsync()
    {
        _refreshCts?.Cancel();
        _refreshCts = new CancellationTokenSource();
        var ct = _refreshCts.Token;
        Refreshing = true;
        Notify();

        try
        {
            var overviewTask = _api.GetOperationsOverviewAsync(12, ct);
            var decisionsTask = _api.GetDecisionsAsync(50, ct);
            var alertsTask = _api.GetAlertsAsync(30, null, ct);

            await Task.WhenAll(overviewTask, decisionsTask, alertsTask);

            Overview = await overviewTask ?? new();
            Queue = Overview.Items
                .OrderByDescending(x => x.RiskScore)
                .ThenByDescending(x => x.PriorityScore)
                .ToList();

            Decisions = (await decisionsTask)?.Decisions ?? new();
            Alerts = (await alertsTask)?.Alerts ?? new();

            if (SelectedSituation is not null && !string.IsNullOrWhiteSpace(SelectedSituation.SituationId))
            {
                await SelectBySituationIdAsync(SelectedSituation.SituationId);
            }
            else if (SelectedItem is not null)
            {
                var refreshed = Queue.FirstOrDefault(x => x.Id == SelectedItem.Id);
                if (refreshed is not null)
                    await SelectItemAsync(refreshed);
            }
        }
        finally
        {
            Refreshing = false;
            Notify();
        }
    }

    public void SelectByEventId(string? eventId)
    {
        if (string.IsNullOrWhiteSpace(eventId))
            return;

        var match = Queue.FirstOrDefault(x => string.Equals(x.Id, eventId, StringComparison.OrdinalIgnoreCase));
        if (match is not null)
        {
            _ = SelectItemAsync(match);
            return;
        }

        var ev = _state.Events.FirstOrDefault(e => string.Equals(e.EventId, eventId, StringComparison.OrdinalIgnoreCase));
        if (ev is null)
            return;

        _ = SelectItemAsync(new OperationQueueItemDto
        {
            Id = ev.EventId ?? "",
            Title = ev.Title ?? ev.EventId ?? "event",
            EventType = ev.EventType,
            Timestamp = ev.Timestamp,
            RiskScore = ev.RiskScore ?? 0,
            ConfidenceScore = ev.ConfidenceScore ?? 0,
            PriorityScore = Math.Max(ev.RiskScore ?? 0, ev.ConfidenceScore ?? 0),
            SignalCount = ev.SignalCount ?? ev.SupportingSignals?.Count ?? 0,
            SupportingSignals = ev.SupportingSignals ?? new(),
            SourceFamilyCount = 1,
            Actors = ev.Actors,
            Location = ev.Location,
            Sentiment = ev.Sentiment,
            RecommendedNextAction = "Escalate to analyst",
        });
    }

    public async Task SelectBySituationIdAsync(string? situationId)
    {
        if (string.IsNullOrWhiteSpace(situationId))
            return;

        var situation = await _api.GetSituationAsync(situationId);
        if (situation is null)
            return;

        SelectedSituation = situation;
        var eventIds = situation.EventIds
            .Where(id => !string.IsNullOrWhiteSpace(id))
            .ToHashSet(StringComparer.OrdinalIgnoreCase);
        var match = Queue
            .Where(item => !string.IsNullOrWhiteSpace(item.Id) && eventIds.Contains(item.Id!))
            .OrderByDescending(item => item.RiskScore)
            .ThenByDescending(item => item.PriorityScore)
            .FirstOrDefault();

        if (match is not null)
        {
            await SelectByEventIdAsync(match.Id ?? "", match, situation);
            return;
        }

        var fallbackEventId = situation.EventIds.FirstOrDefault(id => !string.IsNullOrWhiteSpace(id));
        if (!string.IsNullOrWhiteSpace(fallbackEventId))
            await SelectByEventIdAsync(fallbackEventId, null, situation);
    }

    public Task SelectAsync(EventDto ev)
        => SelectByEventIdAsync(ev.EventId ?? "");

    public Task SelectItemAsync(OperationQueueItemDto item)
        => SelectByEventIdAsync(item.Id ?? "", item);

    private async Task SelectByEventIdAsync(string eventId, OperationQueueItemDto? item = null, DetectedSituationDto? situation = null)
    {
        _selectCts?.Cancel();
        _selectCts = new CancellationTokenSource();
        var ct = _selectCts.Token;

        if (situation is not null)
            SelectedSituation = situation;
        else if (SelectedSituation is not null && !SelectedSituation.EventIds.Contains(eventId, StringComparer.OrdinalIgnoreCase))
            SelectedSituation = null;

        SelectedItem = item ?? Queue.FirstOrDefault(x => string.Equals(x.Id, eventId, StringComparison.OrdinalIgnoreCase));
        SelectedFull = null;
        SelectedEvent = null;
        SelectedTriage = null;
        Msg = "";
        CoaText = "";
        Rationale = "";
        TriageNote = "";
        LoadingReco = true;
        Notify();

        try
        {
            var full = await _api.GetEventFullAsync(eventId, ct: ct);
            if (ct.IsCancellationRequested)
                return;

            SelectedFull = full;
            SelectedEvent = full?.Context?.Event
                ?? full?.Intelligence?.Event
                ?? _state.Events.FirstOrDefault(e => string.Equals(e.EventId, eventId, StringComparison.OrdinalIgnoreCase));

            if (SelectedItem is null && SelectedEvent is not null)
            {
                SelectedItem = new OperationQueueItemDto
                {
                    Id = SelectedEvent.EventId ?? eventId,
                    Title = SelectedEvent.Title ?? eventId,
                    EventType = SelectedEvent.EventType,
                    Timestamp = SelectedEvent.Timestamp,
                    RiskScore = SelectedEvent.RiskScore ?? 0,
                    ConfidenceScore = SelectedEvent.ConfidenceScore ?? 0,
                    PriorityScore = Math.Max(SelectedEvent.RiskScore ?? 0, SelectedEvent.ConfidenceScore ?? 0),
                    SignalCount = SelectedEvent.SignalCount ?? SelectedEvent.SupportingSignals?.Count ?? 0,
                    SupportingSignals = SelectedEvent.SupportingSignals ?? new(),
                    SourceFamilyCount = full?.Context?.Amplification?.SourceFamilies ?? 0,
                    Actors = SelectedEvent.Actors,
                    Location = SelectedEvent.Location,
                    Sentiment = SelectedEvent.Sentiment,
                    Summary = SelectedSituation?.Description ?? full?.Context?.Situation?.Summary,
                    Alerts = full?.Context?.Alerts ?? new(),
                    Narratives = full?.Context?.Narratives ?? new(),
                    RecommendedNextAction = full?.Recommend?.PrimaryRecommendation ?? "Continue monitoring",
                };
            }

            SelectedTriage = await _api.GetTriageRecordAsync(eventId, ct);
            TriageStatus = SelectedTriage?.Status ?? "reviewing";
            TriagePriority = SelectedTriage?.Priority ?? ResolvePriority(SelectedItem?.RiskScore, SelectedItem?.ConfidenceScore);
            TriageNote = SelectedTriage?.Note ?? "";
            CoaText = full?.Recommend?.PrimaryRecommendation ?? SelectedItem?.RecommendedNextAction ?? "";
        }
        finally
        {
            LoadingReco = false;
            Notify();
        }
    }

    public async Task AckAlertAsync(string alertId)
    {
        await _api.AckAlertAsync(alertId);
        var idx = Alerts.FindIndex(a => a.AlertId == alertId);
        if (idx >= 0) Alerts[idx].Acknowledged = true;
        Notify();
    }

    public async Task SubmitDecisionAsync()
    {
        var eventId = SelectedEvent?.EventId ?? SelectedItem?.Id;
        if (string.IsNullOrWhiteSpace(eventId) || string.IsNullOrWhiteSpace(CoaText))
            return;

        Submitting = true;
        Msg = "";
        Notify();

        var dto = new CreateDecisionDto
        {
            EventId = eventId,
            CoaIndex = 0,
            CoaText = CoaText.Trim(),
            Analyst = _auth.CurrentUser?.DisplayName ?? "analyst",
            Status = "approved",
            Rationale = string.IsNullOrWhiteSpace(Rationale) ? null : Rationale.Trim(),
        };

        var res = await _api.CreateDecisionAsync(dto);
        Submitting = false;
        if (res is not null)
        {
            Decisions.Insert(0, res);
            Msg = "DECISION RECORDED";
            Rationale = "";
        }
        else
        {
            Msg = "DECISION FAILED";
        }

        Notify();
    }

    public async Task RecordOutcomeAsync(string id, string outcome)
    {
        var res = await _api.RecordOutcomeAsync(id, new RecordOutcomeDto { Outcome = outcome });
        if (res is not null)
        {
            var idx = Decisions.FindIndex(d => d.Id == id);
            if (idx >= 0) Decisions[idx] = res;
            Notify();
        }
    }

    public async Task SaveTriageAsync()
    {
        var eventId = SelectedEvent?.EventId ?? SelectedItem?.Id;
        if (string.IsNullOrWhiteSpace(eventId))
            return;

        SavingTriage = true;
        Notify();

        try
        {
            var dto = new UpsertTriageDto
            {
                EventId = eventId,
                Title = SelectedEvent?.Title ?? SelectedItem?.Title ?? eventId,
                Source = SelectedEvent?.Source,
                EventType = SelectedEvent?.EventType ?? SelectedItem?.EventType,
                RiskScore = SelectedEvent?.RiskScore ?? SelectedItem?.RiskScore,
                ConfidenceScore = SelectedEvent?.ConfidenceScore ?? SelectedItem?.ConfidenceScore,
                Status = TriageStatus,
                Priority = TriagePriority,
                Note = string.IsNullOrWhiteSpace(TriageNote) ? null : TriageNote.Trim(),
                SourceUrl = SelectedEvent?.Url,
                Region = SelectedEvent?.Location?.Name ?? SelectedEvent?.Location?.Country ?? SelectedItem?.Location?.Name ?? SelectedItem?.Location?.Country,
                SimilarEventCount = SelectedFull?.Similar?.Total ?? SelectedFull?.Context?.RelatedEvents?.Count ?? 0,
                RelatedActorCount = SelectedEvent?.Actors?.Count ?? SelectedItem?.Actors?.Count ?? 0,
            };

            var saved = await _api.UpsertTriageAsync(dto);
            if (saved is not null)
            {
                SelectedTriage = saved;
                TriageStatus = saved.Status;
                TriagePriority = saved.Priority;
                TriageNote = saved.Note ?? "";
                Msg = "TRIAGE SAVED";
            }
            else
            {
                Msg = "TRIAGE FAILED";
            }
        }
        finally
        {
            SavingTriage = false;
            Notify();
        }
    }

    public string AnalystDisplayName() => _auth.CurrentUser?.DisplayName ?? "Current analyst";

    public static string SevClass(double? score) => (score ?? 0) switch
    {
        >= 0.8 => "sev-crit",
        >= 0.6 => "sev-high",
        >= 0.4 => "sev-med",
        _ => "sev-low"
    };

    public static string SevLbl(double? score) => (score ?? 0) switch
    {
        >= 0.8 => "CRIT",
        >= 0.6 => "HIGH",
        >= 0.4 => "MED",
        _ => "LOW"
    };

    public static string ThreatPostureClass(string? posture) => (posture ?? "").ToLowerInvariant() switch
    {
        "critical" => "sev-crit",
        "elevated" => "sev-high",
        _ => "sev-low",
    };

    public static string SevAlertClass(string? sev) => sev?.ToLowerInvariant() switch
    {
        "critical" or "high" => "sev-crit",
        "medium" => "sev-high",
        _ => "sev-med"
    };

    public static string StatusClass(string s) => s.ToLowerInvariant() switch
    {
        "approved" => "sev-med",
        "executed" => "sev-low",
        "rejected" => "sev-high",
        _ => "sev-low"
    };

    public static string TriageStatusClass(string s) => s.ToLowerInvariant() switch
    {
        "escalated" => "sev-crit",
        "reviewing" => "sev-high",
        "actioned" => "sev-low",
        "dismissed" => "idle",
        _ => "sev-med"
    };

    public static string TrimId(string id) => string.IsNullOrEmpty(id) ? "-" : (id.Length > 8 ? id[..8] : id);
    public static string FmtTime(string? t) => DateTime.TryParse(t, out var dt) ? dt.ToString("MM-dd HH:mm") : (t ?? "-");

    private static string ResolvePriority(double? risk, double? confidence)
        => (risk ?? 0, confidence ?? 0) switch
        {
            (>= 0.85, _) or (_, >= 0.90) => "critical",
            (>= 0.65, _) or (_, >= 0.75) => "high",
            (>= 0.40, _) or (_, >= 0.55) => "medium",
            _ => "low",
        };

    private void HandleStateChanged()
    {
        if (Overview.Items.Count == 0)
            BuildQueue();
    }

    public AnalystIndicatorDto QueueIndicator(OperationQueueItemDto item)
        => BuildIndicator(item, null, null, null);

    private AnalystIndicatorDto BuildIndicator(
        OperationQueueItemDto? item,
        EventDto? selectedEvent,
        TriageRecordDto? triage,
        EventFullDto? full)
    {
        if (item is null)
        {
            return AnalystIndicatorFactory.CreateCustom(
                id: "operations",
                label: "Operations queue",
                category: "operation",
                indicatorKind: "operation",
                evidenceKind: "observed",
                assessmentKind: "operations_queue",
                severity: "low",
                driver: "awaiting selection",
                trajectory: "select item",
                recommendedAction: "select work item",
                score: 0,
                confidence: 0,
                corroboration: 0,
                linked: new IndicatorLinkCountsDto(),
                observationSummary: "The operations queue is waiting for a selected item.",
                assessmentSummary: "Pick an item from the queue to inspect its assessed risk, linked evidence, and recommended action.",
                correlationSummary: "No operation item is selected yet.");
        }

        var driver = item.Alerts.Count > 0 ? "corroborated alerts"
            : item.SignalCount > 0 ? "signal evidence"
            : item.Narratives.Count > 0 ? "narrative pressure"
            : item.RiskScore >= 0.7 ? "incident spike"
            : "emerging operation";
        var inSelectedSituation = SelectedSituation is not null &&
                                  !string.IsNullOrWhiteSpace(item.Id) &&
                                  SelectedSituation.EventIds.Contains(item.Id!, StringComparer.OrdinalIgnoreCase);
        var localAlertCount = full?.Context?.Alerts?.Count ?? item.Alerts.Count;
        var localNarrativeCount = full?.Context?.Narratives?.Count ?? item.Narratives.Count;
        var localSignalCount = full?.Intelligence?.Signals?.Count ?? item.SignalCount;
        var localSourceFamilyCount = full?.Context?.Amplification?.SourceFamilies ?? item.SourceFamilyCount;
        var localRelatedEventCount = full?.Context?.RelatedEvents?.Count ?? Math.Max(inSelectedSituation ? SelectedSituation?.EventIds.Count ?? 1 : 1, 1);
        if (inSelectedSituation && !string.IsNullOrWhiteSpace(SelectedSituation?.Indicator?.Driver))
            driver = SelectedSituation.Indicator.Driver;
        var trajectory = triage?.Status is "escalated" ? "operations"
            : (item.RiskScore >= 0.75 || localAlertCount > 0) ? "decide now"
            : localSignalCount > 0 ? "investigate"
            : "hold watch";
        if (inSelectedSituation && !string.IsNullOrWhiteSpace(SelectedSituation?.Indicator?.Trajectory))
            trajectory = SelectedSituation.Indicator.Trajectory;
        var action = !string.IsNullOrWhiteSpace(full?.Recommend?.PrimaryRecommendation)
            ? full!.Recommend!.PrimaryRecommendation
            : !string.IsNullOrWhiteSpace(item.RecommendedNextAction)
                ? item.RecommendedNextAction
                : "Continue monitoring";
        if (inSelectedSituation && !string.IsNullOrWhiteSpace(SelectedSituation?.Indicator?.RecommendedAction))
            action = SelectedSituation.Indicator.RecommendedAction;
        var summary = item.Summary ?? selectedEvent?.Description ?? item.Title ?? "Selected operation";
        var severity = item.RiskScore switch
        {
            >= 0.85 => "critical",
            >= 0.65 => "high",
            >= 0.4 => "medium",
            _ => "low",
        };
        var linked = new IndicatorLinkCountsDto
        {
            Actors = item.Actors.Count,
            Narratives = localNarrativeCount,
            Signals = localSignalCount,
            Alerts = localAlertCount,
            Sources = localSourceFamilyCount,
            Events = localRelatedEventCount,
            Regions = item.Location is null ? 0 : 1,
        };
        var observation = inSelectedSituation && !string.IsNullOrWhiteSpace(SelectedSituation?.Title)
            ? $"{summary} This item belongs to case {SelectedSituation.Title}."
            : $"{summary}";
        var assessment = BuildOperationsAssessment(item, localSignalCount, localAlertCount, localNarrativeCount, localRelatedEventCount);
        var correlation = BuildOperationsCorrelation(item, linked);

        return AnalystIndicatorFactory.CreateCustom(
            id: item.Id ?? "operation",
            label: item.Title ?? "Selected operation",
            category: "operation",
            indicatorKind: "operation",
            evidenceKind: "correlated",
            assessmentKind: "operational_priority",
            severity: severity,
            driver: driver,
            trajectory: trajectory,
            recommendedAction: action,
            score: item.RiskScore,
            confidence: item.ConfidenceScore,
            corroboration: Math.Min(1.0, (linked.Signals * 0.16) + (linked.Alerts * 0.2) + (linked.Sources * 0.08)),
            linked: linked,
            observationSummary: observation,
            assessmentSummary: assessment,
            correlationSummary: correlation,
            region: item.Location?.Name ?? item.Location?.Country,
            summary: summary);
    }

    private string BuildOperationsAssessment(
        OperationQueueItemDto item,
        int signalCount,
        int alertCount,
        int narrativeCount,
        int relatedEventCount)
    {
        var reasons = new List<string>();
        if (item.RiskScore >= 0.72) reasons.Add("shows elevated operational risk");
        if (item.ConfidenceScore >= 0.7) reasons.Add("has strong corroboration");
        if (!string.IsNullOrWhiteSpace(SituationCaseTitle)) reasons.Add($"belongs to the active case {SituationCaseTitle}");
        if (signalCount > 0) reasons.Add($"is backed by {signalCount} linked signal{(signalCount == 1 ? "" : "s")}");
        if (alertCount > 0) reasons.Add($"is linked to {alertCount} active alert{(alertCount == 1 ? "" : "s")}");
        if (narrativeCount > 0) reasons.Add($"carries {narrativeCount} narrative signal{(narrativeCount == 1 ? "" : "s")}");
        if (relatedEventCount > 0) reasons.Add($"connects to {relatedEventCount} related event{(relatedEventCount == 1 ? "" : "s")}");

        return reasons.Count == 0
            ? "This item is still emerging and should stay in analyst review until more evidence lands."
            : $"{item.Title} matters because it {string.Join(", ", reasons)}.";
    }

    private static string BuildOperationsCorrelation(OperationQueueItemDto item, IndicatorLinkCountsDto linked)
    {
        var parts = new List<string>();
        if (linked.Events > 0) parts.Add($"{linked.Events} event link{(linked.Events == 1 ? "" : "s")}");
        if (linked.Signals > 0) parts.Add($"{linked.Signals} signal{(linked.Signals == 1 ? "" : "s")}");
        if (linked.Alerts > 0) parts.Add($"{linked.Alerts} alert{(linked.Alerts == 1 ? "" : "s")}");
        if (linked.Narratives > 0) parts.Add($"{linked.Narratives} narrative{(linked.Narratives == 1 ? "" : "s")}");
        if (linked.Sources > 0) parts.Add($"{linked.Sources} source famil{(linked.Sources == 1 ? "y" : "ies")}");

        return parts.Count == 0
            ? "No strong operational correlation chain has formed yet."
            : $"The current operation is connected through {string.Join(", ", parts)}.";
    }

    public System.Text.Json.JsonElement? SimulateResult { get; private set; }
    public bool Simulating { get; private set; }

    public bool CanSimulate => SelectedItem?.Alerts?.Any(a => !string.IsNullOrWhiteSpace(a.AlertId)) == true;

    public async Task SimulateAsync()
    {
        var alertId = SelectedItem?.Alerts?.FirstOrDefault(a => !string.IsNullOrWhiteSpace(a.AlertId))?.AlertId;
        if (string.IsNullOrWhiteSpace(alertId)) return;
        Simulating = true;
        Msg = "";
        Notify();
        try
        {
            SimulateResult = await _api.SimulateCoaAsync(alertId);
            Msg = SimulateResult.HasValue ? "SIMULATION COMPLETE" : "SIMULATION RETURNED NO DATA";
        }
        catch (Exception ex) { Msg = $"SIMULATE FAILED: {ex.Message}"; }
        finally { Simulating = false; Notify(); }
    }

    private void Notify() => OnChanged?.Invoke();

    public void Dispose()
    {
        _state.OnStateChanged -= HandleStateChanged;
        _refreshCts?.Cancel();
        _refreshCts?.Dispose();
        _selectCts?.Cancel();
        _selectCts?.Dispose();
    }
}
