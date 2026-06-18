using VisionI.Web.Models;

namespace VisionI.Web.Services;

public sealed class TriageService
{
    private readonly ApiService _api;
    private readonly ToastService _toast;
    private readonly AuthService _auth;
    private CancellationTokenSource? _loadCts;
    private CancellationTokenSource? _detailCts;

    public bool Loading { get; private set; }
    public string SourceFilter { get; set; } = "";
    public string Search { get; set; } = "";
    public string QueueFilter { get; set; } = "";
    public bool MineOnly { get; set; }
    public TriageSummaryDto Summary { get; private set; } = new();
    public List<TriageCandidateDto> Candidates { get; private set; } = new();
    public List<TriageRecordDto> Queue { get; private set; } = new();
    public TriageCandidateDto? SelectedCandidate { get; private set; }
    public DetectedSituationDto? SelectedSituation { get; private set; }
    public EventFullDto? SelectedFull { get; private set; }
    public bool LoadingDetail { get; private set; }
    public string DraftStatus { get; set; } = "reviewing";
    public string DraftPriority { get; set; } = "medium";
    public string DraftNote { get; set; } = "";

    public int RelatedAlertCount => SelectedFull?.Context?.Alerts?.Count ?? 0;
    public int RelatedNarrativeCount => SelectedFull?.Context?.Narratives?.Count ?? 0;
    public int RelatedActorCount => SelectedFull?.Context?.Actors?.Count ?? SelectedCandidate?.RelatedActorCount ?? 0;
    public int RelatedEventCount => SelectedFull?.Context?.RelatedEvents?.Count ?? SelectedCandidate?.SimilarEventCount ?? 0;
    public int SimilarDecisionCount => SelectedFull?.Similar?.SimilarDecisions?.Count ?? 0;
    public IEnumerable<PlaybookDefinitionDto> RecommendedPlaybooks => SelectedFull?.Context?.Playbooks ?? Enumerable.Empty<PlaybookDefinitionDto>();
    public string? SituationCaseTitle => SelectedSituation?.Title;
    public AnalystIndicatorDto SelectedIndicator
        => BuildIndicator(SelectedCandidate, SelectedFull, DraftStatus, DraftPriority);
    public string Driver
        => SelectedIndicator.Driver;
    public string Next
        => SelectedIndicator.Trajectory;
    public string ActionLabel
        => SelectedIndicator.RecommendedAction;

    public string WhatChanged
    {
        get
        {
            return SelectedIndicator.ObservationSummary;
        }
    }

    public string WhyItMatters
    {
        get
        {
            return SelectedIndicator.AssessmentSummary;
        }
    }

    public string RecommendedAction
    {
        get
        {
            return SelectedIndicator.RecommendedAction;
        }
    }

    public event Action? OnChanged;

    public TriageService(ApiService api, ToastService toast, AuthService auth)
    {
        _api = api;
        _toast = toast;
        _auth = auth;
    }

    public async Task LoadAsync(string? focusEventId = null, string? focusSituationId = null)
    {
        _loadCts?.Cancel();
        _loadCts = new CancellationTokenSource();
        var ct = _loadCts.Token;
        Loading = true;
        Notify();

        try
        {
            var summaryTask = _api.GetTriageSummaryAsync(ct);
            var candidatesTask = _api.GetTriageCandidatesAsync(30, string.IsNullOrWhiteSpace(SourceFilter) ? null : SourceFilter, string.IsNullOrWhiteSpace(Search) ? null : Search, ct);
            var queueTask = _api.GetTriageQueueAsync(
                status: string.IsNullOrWhiteSpace(QueueFilter) ? null : QueueFilter,
                mine: MineOnly,
                limit: 80,
                offset: 0,
                ct: ct);
            await Task.WhenAll(summaryTask, candidatesTask, queueTask);

            Summary = await summaryTask ?? new();
            Candidates = (await candidatesTask)?.Items ?? new();
            Queue = (await queueTask)?.Items ?? new();

            if (!string.IsNullOrWhiteSpace(focusSituationId))
            {
                await FocusSituationAsync(focusSituationId, ct);
            }
            else if (!string.IsNullOrWhiteSpace(focusEventId))
            {
                await FocusEventAsync(focusEventId, ct);
            }
            else if (SelectedCandidate is not null)
            {
                SelectedCandidate = Candidates.FirstOrDefault(x => x.EventId == SelectedCandidate.EventId) ?? SelectedCandidate;
                if (SelectedCandidate is not null)
                    await LoadDetailAsync(SelectedCandidate.EventId, ct);
            }
        }
        finally
        {
            Loading = false;
            Notify();
        }
    }

    public async Task SelectCandidateAsync(TriageCandidateDto candidate)
    {
        if (SelectedSituation is not null && !SelectedSituation.EventIds.Contains(candidate.EventId, StringComparer.OrdinalIgnoreCase))
            SelectedSituation = null;
        SelectedCandidate = candidate;
        DraftStatus = candidate.Status is "actioned" or "dismissed" ? candidate.Status : "reviewing";
        DraftPriority = string.IsNullOrWhiteSpace(candidate.Priority) ? "medium" : candidate.Priority;
        DraftNote = Queue.FirstOrDefault(x => x.EventId == candidate.EventId)?.Note ?? "";
        await LoadDetailAsync(candidate.EventId, _loadCts?.Token ?? CancellationToken.None);
        Notify();
    }

    public async Task SaveAsync()
    {
        if (SelectedCandidate is null)
            return;

        var dto = new UpsertTriageDto
        {
            EventId = SelectedCandidate.EventId,
            Title = SelectedCandidate.Title,
            Source = SelectedCandidate.Source,
            EventType = SelectedCandidate.EventType,
            RiskScore = SelectedCandidate.RiskScore,
            ConfidenceScore = SelectedCandidate.ConfidenceScore,
            Status = DraftStatus,
            Priority = DraftPriority,
            Note = string.IsNullOrWhiteSpace(DraftNote) ? null : DraftNote.Trim(),
            SourceUrl = SelectedCandidate.SourceUrl,
            Region = SelectedCandidate.Region,
            SimilarEventCount = SelectedCandidate.SimilarEventCount,
            RelatedActorCount = SelectedCandidate.RelatedActorCount,
        };

        var saved = await _api.UpsertTriageAsync(dto);
        if (saved is null)
        {
            _toast.ShowError("Failed to update triage record.");
            return;
        }

        _toast.ShowSuccess("Triage record updated.");
        await LoadAsync();
        SelectedCandidate = Candidates.FirstOrDefault(x => x.EventId == saved.EventId) ?? SelectedCandidate;
        Notify();
    }

    public string CurrentAnalystName()
        => _auth.CurrentUser?.DisplayName ?? "Current analyst";

    public static string PriorityClass(string priority) => priority.ToLowerInvariant() switch
    {
        "critical" => "danger",
        "high" => "warn",
        "medium" => "primary",
        _ => "ok",
    };

    public static string StatusClass(string status) => status.ToLowerInvariant() switch
    {
        "escalated" => "danger",
        "reviewing" => "warn",
        "actioned" => "ok",
        "dismissed" => "muted",
        _ => "primary",
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

    private void Notify() => OnChanged?.Invoke();

    private async Task FocusEventAsync(string eventId, CancellationToken ct)
    {
        var candidate = Candidates.FirstOrDefault(x => string.Equals(x.EventId, eventId, StringComparison.OrdinalIgnoreCase));
        if (candidate is not null)
        {
            await SelectCandidateAsync(candidate);
            return;
        }

        var record = Queue.FirstOrDefault(x => string.Equals(x.EventId, eventId, StringComparison.OrdinalIgnoreCase))
                     ?? await _api.GetTriageRecordAsync(eventId, ct);
        if (record is null)
            return;

        candidate = new TriageCandidateDto
        {
            EventId = record.EventId,
            Title = record.Title,
            Source = record.Source,
            EventType = record.EventType,
            RiskScore = record.RiskScore,
            ConfidenceScore = record.ConfidenceScore,
            SourceUrl = record.SourceUrl,
            Region = record.Region,
            Status = record.Status,
            Priority = record.Priority,
            AnalystDisplayName = record.AnalystDisplayName,
            SimilarEventCount = record.SimilarEventCount,
            RelatedActorCount = record.RelatedActorCount,
            Timestamp = record.LastSeenAt ?? record.UpdatedAt ?? record.CreatedAt,
        };

        if (!Candidates.Any(x => string.Equals(x.EventId, candidate.EventId, StringComparison.OrdinalIgnoreCase)))
            Candidates.Insert(0, candidate);

        await SelectCandidateAsync(candidate);
    }

    private async Task FocusSituationAsync(string situationId, CancellationToken ct)
    {
        var situation = await _api.GetSituationAsync(situationId, ct);
        if (situation is null)
            return;

        SelectedSituation = situation;
        var eventIds = situation.EventIds
            .Where(id => !string.IsNullOrWhiteSpace(id))
            .ToHashSet(StringComparer.OrdinalIgnoreCase);

        var candidate = Candidates
            .Where(c => eventIds.Contains(c.EventId))
            .OrderByDescending(c => c.RiskScore ?? 0)
            .ThenByDescending(c => c.ConfidenceScore ?? 0)
            .FirstOrDefault();

        if (candidate is not null)
        {
            await SelectCandidateAsync(candidate);
            return;
        }

        var fallbackEventId = situation.EventIds.FirstOrDefault(id => !string.IsNullOrWhiteSpace(id));
        if (!string.IsNullOrWhiteSpace(fallbackEventId))
            await FocusEventAsync(fallbackEventId, ct);
    }

    private async Task LoadDetailAsync(string eventId, CancellationToken ct)
    {
        _detailCts?.Cancel();
        _detailCts = CancellationTokenSource.CreateLinkedTokenSource(ct);
        LoadingDetail = true;
        Notify();

        try
        {
            SelectedFull = await _api.GetEventFullAsync(eventId, socialLimit: 12, similarLimit: 6, ct: _detailCts.Token);
        }
        catch (OperationCanceledException)
        {
        }
        catch
        {
            SelectedFull = null;
        }
        finally
        {
            LoadingDetail = false;
            Notify();
        }
    }

    private static string FirstNonEmpty(params string?[] values)
    {
        foreach (var value in values)
        {
            if (!string.IsNullOrWhiteSpace(value))
                return value.Trim();
        }

        return "";
    }

    public AnalystIndicatorDto CandidateIndicator(TriageCandidateDto item)
        => BuildIndicator(item, null, item.Status, item.Priority);

    private AnalystIndicatorDto BuildIndicator(
        TriageCandidateDto? candidate,
        EventFullDto? full,
        string? draftStatus,
        string? draftPriority)
    {
        if (candidate is null)
        {
            return AnalystIndicatorFactory.CreateCustom(
                id: "triage",
                label: "Triage queue",
                category: "triage",
                indicatorKind: "triage",
                evidenceKind: "observed",
                assessmentKind: "triage_review",
                severity: "low",
                driver: "awaiting selection",
                trajectory: "watch",
                recommendedAction: "select candidate",
                score: 0,
                confidence: 0,
                corroboration: 0,
                linked: new IndicatorLinkCountsDto(),
                observationSummary: "Select a candidate to review the latest event context.",
                assessmentSummary: "No triage candidate is selected yet.",
                correlationSummary: "No linked evidence has been loaded yet.");
        }

        var driver = RelatedAlertCount > 0 ? "corroborated alerts"
            : RelatedNarrativeCount > 0 ? "narrative pressure"
            : RelatedActorCount >= 3 ? "actor convergence"
            : (candidate.RiskScore ?? 0) >= 0.7 ? "incident spike"
            : "emerging signal";
        var localAlertCount = full?.Context?.Alerts?.Count ?? 0;
        var localNarrativeCount = full?.Context?.Narratives?.Count ?? 0;
        var localActorCount = full?.Context?.Actors?.Count ?? candidate.RelatedActorCount;
        var localEventCount = full?.Context?.RelatedEvents?.Count ?? candidate.SimilarEventCount;
        driver = localAlertCount > 0 ? "corroborated alerts"
            : localNarrativeCount > 0 ? "narrative pressure"
            : localActorCount >= 3 ? "actor convergence"
            : (candidate.RiskScore ?? 0) >= 0.7 ? "incident spike"
            : "emerging signal";
        var inSelectedSituation = SelectedSituation is not null &&
                                  SelectedSituation.EventIds.Contains(candidate.EventId, StringComparer.OrdinalIgnoreCase);
        if (inSelectedSituation && !string.IsNullOrWhiteSpace(SelectedSituation?.Indicator?.Driver))
            driver = SelectedSituation.Indicator.Driver;
        var trajectory = draftStatus is "escalated" ? "operations"
            : (candidate.RiskScore ?? 0) >= 0.7 || localAlertCount > 0 ? "triage now"
            : localEventCount > 2 ? "investigate"
            : "watch";
        if (inSelectedSituation && !string.IsNullOrWhiteSpace(SelectedSituation?.Indicator?.Trajectory))
            trajectory = SelectedSituation.Indicator.Trajectory;
        var action = FirstNonEmpty(
            full?.Recommend?.PrimaryRecommendation,
            full?.Context?.Playbooks?.FirstOrDefault()?.Objective,
            inSelectedSituation ? SelectedSituation?.Indicator?.RecommendedAction : null,
            draftStatus is "escalated"
                ? "Escalate to operations and capture the decision rationale."
                : "Review corroborating signals, capture analyst notes, and persist triage status.");
        var severity = (candidate.RiskScore ?? 0) switch
        {
            >= 0.85 => "critical",
            >= 0.65 => "high",
            >= 0.4 => "medium",
            _ => "low",
        };
        var corroboration = Math.Min(1.0,
            (RelatedAlertCount * 0.2) +
            (RelatedNarrativeCount * 0.15) +
            (RelatedEventCount * 0.08) +
            (candidate.RelatedActorCount * 0.05));

        return AnalystIndicatorFactory.CreateCustom(
            id: candidate.EventId,
            label: candidate.Title,
            category: "triage",
            indicatorKind: "triage_candidate",
            evidenceKind: "correlated",
            assessmentKind: "triage_review",
            severity: severity,
            driver: driver,
            trajectory: trajectory,
            recommendedAction: action,
            score: candidate.RiskScore ?? 0,
            confidence: candidate.ConfidenceScore ?? 0,
            corroboration: corroboration,
            linked: new IndicatorLinkCountsDto
            {
                Actors = localActorCount,
                Narratives = localNarrativeCount,
                Alerts = localAlertCount,
                Events = Math.Max(localEventCount, inSelectedSituation ? SelectedSituation?.EventIds.Count ?? 0 : 0),
                Regions = string.IsNullOrWhiteSpace(candidate.Region) ? 0 : 1,
            },
            observationSummary: FirstNonEmpty(
                inSelectedSituation ? $"{candidate.Title} belongs to case {SelectedSituation?.Title}." : null,
                full?.Context?.Event?.Description,
                full?.Context?.Event?.Body,
                full?.Context?.Event?.Title,
                candidate.Title,
                "Selected triage candidate."),
            assessmentSummary: FirstNonEmpty(
                inSelectedSituation ? SelectedSituation?.Indicator?.AssessmentSummary : null,
                full?.Explain?.Briefing,
                full?.Recommend?.Reasoning,
                full?.Context?.Situation?.Summary,
                "This item is waiting for analyst review."),
            correlationSummary: BuildCorrelationSummary(candidate, localActorCount, localNarrativeCount, localAlertCount, localEventCount),
            region: candidate.Region,
            summary: candidate.Title);
    }

    private string BuildCorrelationSummary(
        TriageCandidateDto candidate,
        int actorCount,
        int narrativeCount,
        int alertCount,
        int eventCount)
    {
        var parts = new List<string>();
        if (eventCount > 0) parts.Add($"{eventCount} related event{(eventCount == 1 ? "" : "s")}");
        if (actorCount > 0) parts.Add($"{actorCount} linked actor{(actorCount == 1 ? "" : "s")}");
        if (alertCount > 0) parts.Add($"{alertCount} alert{(alertCount == 1 ? "" : "s")}");
        if (narrativeCount > 0) parts.Add($"{narrativeCount} narrative signal{(narrativeCount == 1 ? "" : "s")}");
        if (SelectedSituation is not null && SelectedSituation.EventIds.Contains(candidate.EventId, StringComparer.OrdinalIgnoreCase))
            parts.Add($"case {SelectedSituation.Title}");

        return parts.Count == 0
            ? "No strong corroboration chain has formed yet."
            : $"The current triage picture includes {string.Join(", ", parts)}.";
    }
}
