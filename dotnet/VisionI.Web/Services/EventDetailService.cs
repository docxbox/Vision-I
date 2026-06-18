using VisionI.Web.Models;

namespace VisionI.Web.Services;

public sealed class EventDetailService
{
    private readonly ApiService _api;
    private readonly ViStateService _state;
    private readonly AuthService _auth;

    public bool Loading { get; private set; } = true;
    public string? Error { get; private set; }
    public bool Degraded { get; private set; }
    public string? LoadedEventId { get; private set; }
    public string? LoadedSituationId { get; private set; }
    public EventFullDto? Full { get; private set; }
    public EventDto? Event { get; private set; }
    public DetectedSituationDto? SelectedSituation { get; private set; }
    public TriageRecordDto? Triage { get; private set; }
    public string TriageStatus { get; set; } = "reviewing";
    public string TriagePriority { get; set; } = "medium";
    public string TriageNote { get; set; } = "";
    public bool SavingTriage { get; private set; }

    public string? LocationLabel => Event?.Location?.Name ?? Event?.Location?.Country;
    public string? SituationTitle => SelectedSituation?.Title ?? Full?.Context?.Situation?.Title;
    public string? SituationId => SelectedSituation?.SituationId;
    public bool HasSelectedSituation => SelectedSituation is not null;
    public int RelatedEventCount => Full?.Context?.RelatedEvents?.Count ?? 0;
    public int RelatedSignalCount => Full?.Intelligence?.Signals?.Count ?? Event?.SignalCount ?? 0;
    public int ClusteredSignalCount =>
        Full?.Intelligence?.Signals?
            .Where(s => !string.IsNullOrWhiteSpace(s.ClusterId))
            .Select(s => s.ClusterId!)
            .Distinct(StringComparer.OrdinalIgnoreCase)
            .Count()
        ?? 0;
    public int SocialCorrelationCount =>
        Full?.Intelligence?.SocialReactions?.Count
        ?? Full?.Social?.Posts?.Count
        ?? 0;
    public int LinkedAlertCount => Full?.Context?.Alerts?.Count ?? 0;
    public int NarrativeCount =>
        Math.Max(Full?.Intelligence?.Narratives?.Count ?? 0, Full?.Context?.Narratives?.Count ?? 0);
    public int PlaybookCount => Full?.Context?.Playbooks?.Count ?? 0;
    public int SimilarDecisionCount => Full?.Similar?.SimilarDecisions?.Count ?? 0;
    public int ActorCount => Event?.Actors?.Count ?? 0;
    public int PhysicalAssetCount => Full?.Intelligence?.PhysicalSignals?.Assets?.Count ?? 0;
    public int SourceFamilyCount => Full?.Context?.Amplification?.SourceFamilies ?? 0;
    public IEnumerable<SignalDto> RelatedSignals => Full?.Intelligence?.Signals ?? Enumerable.Empty<SignalDto>();
    public AnalystIndicatorDto DetailIndicator
    {
        get
        {
            var ev = Event;
            var eventId = ev?.EventId ?? LoadedEventId ?? "event";
            var label = ev?.Title ?? "Selected event";
            var severity = SeverityFromScore(ev?.RiskScore ?? 0);
            return AnalystIndicatorFactory.CreateCustom(
                id: eventId,
                label: label,
                category: "event",
                indicatorKind: "event",
                evidenceKind: "correlated",
                assessmentKind: "event_assessment",
                severity: severity,
                driver: PrimaryDriver,
                trajectory: Trajectory,
                recommendedAction: RecommendedActionLabel,
                score: ev?.RiskScore ?? 0,
                confidence: ev?.ConfidenceScore ?? 0,
                corroboration: CorroborationScore,
                linked: new IndicatorLinkCountsDto
                {
                    Actors = ActorCount,
                    Narratives = NarrativeCount,
                    Signals = RelatedSignalCount,
                    Alerts = LinkedAlertCount,
                    Sources = SourceFamilyCount,
                    Events = RelatedEventCount,
                    Regions = string.IsNullOrWhiteSpace(LocationLabel) ? 0 : 1,
                },
                observationSummary: BuildObservationSummary(label),
                assessmentSummary: BuildAssessmentSummary(label),
                correlationSummary: BuildCorrelationSummary(),
                region: LocationLabel,
                summary: BuildAssessmentSummary(label));
        }
    }
    public string PrimaryDriver =>
        LinkedAlertCount > 0 ? "corroborated alerts" :
        RelatedSignalCount > 0 ? "signal evidence" :
        NarrativeCount > 0 ? "narrative pressure" :
        (Event?.IsAnomaly ?? false) ? "anomaly detection" :
        "emerging event";
    public string Trajectory =>
        (Event?.RiskScore ?? 0) >= 0.75 || LinkedAlertCount > 0 ? "rising" :
        RelatedEventCount > 2 || RelatedSignalCount > 1 ? "watch" :
        "stable";
    public string RecommendedActionLabel =>
        !string.IsNullOrWhiteSpace(Full?.Recommend?.PrimaryRecommendation) ? "escalate" :
        LinkedAlertCount > 0 || (Event?.RiskScore ?? 0) >= 0.7 ? "triage now" :
        RelatedSignalCount > 0 ? "investigate" :
        "monitor";
    public double CorroborationScore =>
        Math.Min(1.0,
            (RelatedEventCount * 0.12) +
            (Math.Min(RelatedSignalCount, 4) * 0.16) +
            (Math.Min(LinkedAlertCount, 3) * 0.18) +
            (Math.Min(SourceFamilyCount, 5) * 0.08));
    public bool HasEvidenceSummary =>
        RelatedEventCount > 0 ||
        RelatedSignalCount > 0 ||
        SocialCorrelationCount > 0 ||
        LinkedAlertCount > 0 ||
        NarrativeCount > 0 ||
        SimilarDecisionCount > 0 ||
        PlaybookCount > 0;
    public string AnalystAssessment
    {
        get
        {
            var label = Event?.Title ?? "Selected event";
            var assessment = BuildAssessmentSummary(label);
            return string.IsNullOrWhiteSpace(assessment)
                ? $"{label} is still lightly evidenced and should be treated as an early signal until more corroboration arrives."
                : assessment;
        }
    }

    public bool HasSituation =>
        Full?.Context?.Situation is { } situation &&
        (!string.IsNullOrWhiteSpace(situation.Id) ||
         !string.IsNullOrWhiteSpace(situation.Title) ||
         !string.IsNullOrWhiteSpace(situation.Summary));

    public string DecisionPrecedent
    {
        get
        {
            var precedent = Full?.Recommend?.HistoricalPrecedent;
            if (!string.IsNullOrWhiteSpace(precedent))
                return precedent;

            if (SimilarDecisionCount > 0)
                return $"{SimilarDecisionCount} similar analyst decision{(SimilarDecisionCount == 1 ? "" : "s")} are available for comparison.";

            return "No strong historical decision precedent is attached to this event yet.";
        }
    }

    public event Action? OnChanged;

    public EventDetailService(ApiService api, ViStateService state, AuthService auth)
    {
        _api = api;
        _state = state;
        _auth = auth;
    }

    public async Task LoadAsync(string? eventId, string? situationId = null, bool force = false)
    {
        if (!force &&
            string.Equals(LoadedEventId, eventId, StringComparison.OrdinalIgnoreCase) &&
            string.Equals(LoadedSituationId, situationId, StringComparison.OrdinalIgnoreCase))
            return;

        LoadedEventId = eventId;
        LoadedSituationId = situationId;
        await LoadCoreAsync(eventId);
    }

    public Task ReloadAsync() => LoadCoreAsync(LoadedEventId);

    private async Task LoadCoreAsync(string? eventId)
    {
        Loading = true;
        Error = null;
        Degraded = false;
        Full = null;
        Event = FindCachedEvent(eventId);
        SelectedSituation = null;
        Triage = null;
        OnChanged?.Invoke();

        var id = eventId?.Trim();
        if (string.IsNullOrWhiteSpace(id))
        {
            Error = "No event id was provided.";
            Loading = false;
            OnChanged?.Invoke();
            return;
        }

        try
        {
            if (!string.IsNullOrWhiteSpace(LoadedSituationId))
                SelectedSituation = await _api.GetSituationAsync(LoadedSituationId);

            Full = await _api.GetEventFullAsync(id);
            Event = ResolvePrimaryEvent(Full) ?? Event;

            if (Event is null || string.IsNullOrWhiteSpace(Event.EventId))
                await LoadFallbackAsync(id);

            await LoadTriageAsync(id);
        }
        catch
        {
            await LoadFallbackAsync(id);
            await LoadTriageAsync(id);
        }
        finally
        {
            Loading = false;
            OnChanged?.Invoke();
        }
    }

    private async Task LoadFallbackAsync(string id)
    {
        var encodedId = Uri.EscapeDataString(id);

        var eventTask = _api.GetAsync<EventDto>($"api/events/{encodedId}");
        var contextTask = _api.GetEventContextAsync(id);
        var intelTask = _api.GetEventIntelligenceAsync(id);
        var socialTask = _api.GetEventSocialAsync(id);
        var similarTask = _api.CopilotSimilarAsync(id);
        var explainTask = _api.CopilotExplainAsync(id);
        var recommendTask = _api.CopilotRecommendAsync(id);

        await Task.WhenAll(eventTask, contextTask, intelTask, socialTask, similarTask, explainTask, recommendTask);

        var fallbackEvent = await eventTask;
        var context = await contextTask;
        var intelligence = await intelTask;
        var social = await socialTask;
        var similar = await similarTask;
        var explain = await explainTask;
        var recommend = await recommendTask;

        Full = new EventFullDto
        {
            EventId = id,
            Context = context,
            Intelligence = intelligence,
            Social = social,
            Similar = similar,
            Explain = explain,
            Recommend = recommend,
            FetchedAt = DateTime.UtcNow.ToString("O"),
        };

        Event = context?.Event;
        if (Event is null || string.IsNullOrWhiteSpace(Event.EventId))
            Event = intelligence?.Event;
        if (Event is null || string.IsNullOrWhiteSpace(Event.EventId))
            Event = fallbackEvent;

        var enrichmentAvailable = Event is not null && !string.IsNullOrWhiteSpace(Event.EventId);

        if (!enrichmentAvailable)
            Event = FindCachedEvent(id);

        if (Event is null || string.IsNullOrWhiteSpace(Event.EventId))
        {
            Error = "Event data is unavailable. The event may have aged out of the active feed window or the data source is temporarily offline.";
        }
        else if (!enrichmentAvailable)
        {
            Degraded = true;
        }
    }

    private EventDto? ResolvePrimaryEvent(EventFullDto? full)
    {
        if (full?.Context?.Event is { EventId: { Length: > 0 } })
            return full.Context.Event;

        if (full?.Intelligence?.Event is { EventId: { Length: > 0 } })
            return full.Intelligence.Event;

        return null;
    }

    private EventDto? FindCachedEvent(string? eventId)
        => string.IsNullOrWhiteSpace(eventId)
            ? null
            : _state.Events.FirstOrDefault(e => string.Equals(e.EventId, eventId, StringComparison.OrdinalIgnoreCase));

    public async Task SaveTriageAsync()
    {
        var current = Event;
        if (current is null || string.IsNullOrWhiteSpace(current.EventId))
            return;

        SavingTriage = true;
        OnChanged?.Invoke();

        try
        {
            var dto = new UpsertTriageDto
            {
                EventId = current.EventId,
                Title = current.Title ?? "Untitled event",
                Source = current.Source,
                EventType = current.EventType,
                RiskScore = current.RiskScore,
                ConfidenceScore = current.ConfidenceScore,
                Status = TriageStatus,
                Priority = TriagePriority,
                Note = string.IsNullOrWhiteSpace(TriageNote) ? null : TriageNote.Trim(),
                SourceUrl = current.Url,
                Region = LocationLabel,
                SimilarEventCount = Full?.Similar?.Total ?? 0,
                RelatedActorCount = current.Actors.Count,
            };

            Triage = await _api.UpsertTriageAsync(dto);
            if (Triage is not null)
            {
                TriageStatus = Triage.Status;
                TriagePriority = Triage.Priority;
                TriageNote = Triage.Note ?? "";
            }
        }
        finally
        {
            SavingTriage = false;
            OnChanged?.Invoke();
        }
    }

    public string AnalystDisplayName() => _auth.CurrentUser?.DisplayName ?? "Current analyst";

    private async Task LoadTriageAsync(string eventId)
    {
        Triage = await _api.GetTriageRecordAsync(eventId);
        TriageStatus = Triage?.Status ?? "reviewing";
        TriagePriority = Triage?.Priority ?? ResolvePriority(Event?.RiskScore, Event?.ConfidenceScore);
        TriageNote = Triage?.Note ?? "";
    }

    private static string ResolvePriority(double? risk, double? confidence)
        => (risk ?? 0, confidence ?? 0) switch
        {
            (>= 0.85, _) or (_, >= 0.90) => "critical",
            (>= 0.65, _) or (_, >= 0.75) => "high",
            (>= 0.40, _) or (_, >= 0.55) => "medium",
            _ => "low",
        };

    private static string SeverityFromScore(double score)
        => score switch
        {
            >= 0.85 => "critical",
            >= 0.65 => "high",
            >= 0.4 => "medium",
            _ => "low",
        };

    private string BuildObservationSummary(string label)
    {
        var clauses = new List<string>();
        if (!string.IsNullOrWhiteSpace(SituationTitle))
            clauses.Add($"belongs to case {SituationTitle}");
        if (!string.IsNullOrWhiteSpace(LocationLabel))
            clauses.Add($"was observed in {LocationLabel}");
        if (RelatedSignalCount > 0)
            clauses.Add($"has {RelatedSignalCount} linked signal{(RelatedSignalCount == 1 ? "" : "s")}");
        if (LinkedAlertCount > 0)
            clauses.Add($"is already tied to {LinkedAlertCount} alert{(LinkedAlertCount == 1 ? "" : "s")}");

        return clauses.Count == 0
            ? $"{label} has entered the event workspace with limited corroborating evidence so far."
            : $"{label} {string.Join(", ", clauses)}.";
    }

    private string BuildAssessmentSummary(string label)
    {
        var reasons = new List<string>();

        if (!string.IsNullOrWhiteSpace(SituationTitle))
            reasons.Add($"is part of the active case {SituationTitle}");

        if ((Event?.RiskScore ?? 0) >= 0.7)
            reasons.Add("carries elevated operational risk");
        if ((Event?.ConfidenceScore ?? 0) >= 0.7)
            reasons.Add("has strong confidence scoring");
        if (NarrativeCount > 0)
            reasons.Add($"shows {NarrativeCount} narrative signal{(NarrativeCount == 1 ? "" : "s")}");
        if (SocialCorrelationCount >= 5)
            reasons.Add($"has broad social pickup across {SocialCorrelationCount} correlated posts");

        return reasons.Count == 0
            ? $"{label} is still lightly evidenced and should stay in analyst review."
            : $"{label} matters because it {string.Join(", ", reasons)}.";
    }

    private string BuildCorrelationSummary()
    {
        var parts = new List<string>();
        if (SelectedSituation is not null)
            parts.Add($"{SelectedSituation.EventIds.Count} case event{(SelectedSituation.EventIds.Count == 1 ? "" : "s")}");
        if (RelatedEventCount > 0)
            parts.Add($"{RelatedEventCount} related event{(RelatedEventCount == 1 ? "" : "s")}");
        if (RelatedSignalCount > 0)
            parts.Add($"{RelatedSignalCount} signal{(RelatedSignalCount == 1 ? "" : "s")}");
        if (LinkedAlertCount > 0)
            parts.Add($"{LinkedAlertCount} alert{(LinkedAlertCount == 1 ? "" : "s")}");
        if (PlaybookCount > 0)
            parts.Add($"{PlaybookCount} playbook match{(PlaybookCount == 1 ? "" : "es")}");

        return parts.Count == 0
            ? "No strong correlation chain has been established yet."
            : $"The current correlation chain includes {string.Join(", ", parts)}.";
    }
}
