using System.Text.Json;
using VisionI.Web.Models;

namespace VisionI.Web.Services;

/// <summary>
/// Scoped ViewModel service for the Entities page.
/// Fixes:
/// - Bug 2.6: Auto-refresh timer now properly created and started.
/// - Bug 3.5: ChartId is unique per entity to prevent "canvas already in use" errors.
/// </summary>
public sealed class EntityService : IAsyncDisposable
{
    private readonly ApiService _api;
    private readonly ToastService _toast;
    private readonly ILogger<EntityService> _log;

    private static readonly JsonSerializerOptions JsonOpts =
        new() { PropertyNameCaseInsensitive = true };
    public bool ListLoading { get; private set; } = true;
    public string FilterText { get; set; } = "";
    public string FilterType { get; set; } = "";
    public int DirectoryTotal { get; private set; }
    public List<EntityCardVm> AllEntities { get; private set; } = new();
    public List<EntityCardVm> FilteredEntities { get; private set; } = new();
    public bool DetailLoading { get; private set; }
    public EntityDetailVm? Detail { get; private set; }
    public EntityWikiVm? Wiki { get; private set; }
    public EntityGraphSummaryVm? DetailGraphSummary { get; private set; }
    public AnalystIndicatorDto? DetailIndicator => Detail is null ? null : BuildDetailIndicator(Detail, DetailGraphSummary);
    public List<string> SentimentLabels { get; private set; } = new();
    public List<double> SentimentValues { get; private set; } = new();
    public string ChartId { get; private set; } = "ent-sent-chart-default";
    private CancellationTokenSource? _searchCts;
    private System.Timers.Timer? _refreshTimer;

    public event Action? OnChanged;

    public EntityService(ApiService api, ToastService toast, ILogger<EntityService> log)
    {
        _api = api;
        _toast = toast;
        _log = log;
    }

    private DateTime _lastLoaded = DateTime.MinValue;
    public async Task EnsureLoadedAsync()
    {
        if (AllEntities.Count > 0 && DateTime.UtcNow - _lastLoaded < TimeSpan.FromMinutes(2)) return;
        await LoadListAsync();
    }

    public async Task LoadListAsync()
    {
        ListLoading = true;
        OnChanged?.Invoke();

        try
        {
            var response = await _api.GetEntitiesAsync(limit: 200);
            DirectoryTotal = response?.Total ?? 0;
            AllEntities = (response?.Entities ?? new())
                .Select(e => new EntityCardVm(
                    e.EntityId ?? e.Id ?? "",
                    e.Name ?? e.EntityId ?? e.Id ?? "entity",
                    e.Type ?? "unknown",
                    null,
                    e.MentionCount,
                    ScoreInfluence(e.InfluenceScore, e.MentionCount, e.EventCount, e.NarrativeCount),
                    ScoreNarrative(e.NarrativeCount, e.EventCount, e.MentionCount),
                    ScoreSentiment(e.SentimentScore, e.EventCount, e.NarrativeCount)))
                .Where(e => !string.IsNullOrWhiteSpace(e.Id))
                .ToList();
            _lastLoaded = DateTime.UtcNow;
            ApplyListFilter();

            // Start auto-refresh timer every 60s, fixes bug 2.6
            _refreshTimer?.Dispose();
            _refreshTimer = new System.Timers.Timer(60_000) { AutoReset = true };
            _refreshTimer.Elapsed += async (_, _) =>
            {
                var refreshed = await _api.GetEntitiesAsync(limit: 200);
                DirectoryTotal = refreshed?.Total ?? DirectoryTotal;
                AllEntities = (refreshed?.Entities ?? new())
                    .Select(e => new EntityCardVm(
                        e.EntityId ?? e.Id ?? "",
                        e.Name ?? e.EntityId ?? e.Id ?? "entity",
                        e.Type ?? "unknown",
                        null,
                        e.MentionCount,
                        ScoreInfluence(e.InfluenceScore, e.MentionCount, e.EventCount, e.NarrativeCount),
                        ScoreNarrative(e.NarrativeCount, e.EventCount, e.MentionCount),
                        ScoreSentiment(e.SentimentScore, e.EventCount, e.NarrativeCount)))
                    .Where(e => !string.IsNullOrWhiteSpace(e.Id))
                    .ToList();
                ApplyListFilter();
                OnChanged?.Invoke();
            };
            _refreshTimer.Start();
        }
        catch (Exception ex)
        {
            _log.LogError(ex, "EntityService.LoadListAsync failed");
            _toast.ShowError("Could not load entities.");
        }
        finally
        {
            ListLoading = false;
            OnChanged?.Invoke();
        }
    }

    public void ApplyListFilter()
    {
        var q = AllEntities.AsEnumerable();

        if (!string.IsNullOrEmpty(FilterType))
            q = q.Where(e => string.Equals(e.Type, FilterType, StringComparison.OrdinalIgnoreCase));

        if (!string.IsNullOrWhiteSpace(FilterText))
            q = q.Where(e => e.Name.Contains(FilterText, StringComparison.OrdinalIgnoreCase) ||
                              (e.Country?.Contains(FilterText, StringComparison.OrdinalIgnoreCase) ?? false));

        FilteredEntities = q.ToList();
        OnChanged?.Invoke();
    }

    public async Task OnSearchInputAsync(string value)
    {
        FilterText = value;
        _searchCts?.Cancel();
        _searchCts = new CancellationTokenSource();
        var token = _searchCts.Token;
        try { await Task.Delay(300, token); ApplyListFilter(); }
        catch (OperationCanceledException) { }
    }

    public async Task LoadDetailAsync(string entityId)
    {
        var canonicalEntityId = NormalizeEntityId(entityId);
        Detail = null;
        Wiki = null;
        DetailGraphSummary = null;
        DetailLoading = true;
        // Unique chart ID per entity, fixes bug 3.5
        ChartId = $"ent-sent-{canonicalEntityId.Replace(":", "-").Replace("/", "-")}";
        OnChanged?.Invoke();

        try
        {
            var detailTask = _api.GetEntityActorDetailAsync(canonicalEntityId);
            var timelineTask = _api.GetSentimentTimelineAsync(entityId: canonicalEntityId, bucket: "hour", hours: 72);
            var graphTask = _api.GetEntityGraphRawAsync(canonicalEntityId, depth: 1);
            var wikiTask = _api.GetEntityWikipediaAsync(canonicalEntityId);
            await Task.WhenAll(detailTask, timelineTask, graphTask, wikiTask);

            var detailRaw = detailTask.Result;
            var tlRaw = timelineTask.Result;
            var graphRaw = graphTask.Result;
            var wikiRaw = wikiTask.Result;

            Detail = detailRaw.HasValue ? ParseDetail(detailRaw.Value, canonicalEntityId) : null;
            Wiki = wikiRaw.HasValue ? ParseWiki(wikiRaw.Value) : null;
            Detail ??= BuildFallbackDetail(canonicalEntityId, Wiki);
            Detail = await EnrichWithEventSearchAsync(Detail);
            EnsureDetailInDirectory(Detail);
            ParseTimeline(tlRaw);
            DetailGraphSummary = graphRaw.HasValue ? ParseGraphSummary(graphRaw.Value) : null;
        }
        catch (Exception ex)
        {
            _log.LogError(ex, "EntityService.LoadDetailAsync failed for {EntityId}", entityId);
            _toast.ShowError("Could not load entity detail.");
        }
        finally
        {
            DetailLoading = false;
            OnChanged?.Invoke();
        }
    }

    private async Task<EntityDetailVm> EnrichWithEventSearchAsync(EntityDetailVm detail)
    {
        try
        {
            if (detail.EventCount > 0 && detail.RecentEvents.Count > 0)
                return detail;

            var events = await _api.GetEventsAsync(query: detail.Name, limit: 12);
            var recent = (events?.Events ?? new())
                .Select(ev => new RecentEventVm(
                    ev.EventId ?? "",
                    ev.Title ?? "Untitled event",
                    ev.Timestamp,
                    ev.RiskScore ?? 0))
                .Where(ev => !string.IsNullOrWhiteSpace(ev.EventId) || !string.IsNullOrWhiteSpace(ev.Title))
                .Take(8)
                .ToList();

            if (recent.Count == 0)
                return detail;

            var total = Math.Max(events?.Total ?? recent.Count, recent.Count);
            var narrativeCount = Math.Max(detail.NarrativeCount, total / 8);
            var influence = detail.Influence > 0
                ? detail.Influence
                : ScoreInfluence(null, Math.Max(detail.Mentions, total), total, narrativeCount);
            var sentiment = Math.Abs(detail.Sentiment) > 0.001
                ? detail.Sentiment
                : ScoreSentiment(events?.Events ?? new(), total, narrativeCount, detail.SignalCount);

            return detail with
            {
                Mentions = Math.Max(detail.Mentions, total),
                EventCount = Math.Max(detail.EventCount, total),
                Influence = influence,
                NarrativeCount = narrativeCount,
                Sentiment = sentiment,
                RecentEvents = recent
            };
        }
        catch (Exception ex)
        {
            _log.LogDebug(ex, "Entity event-search enrichment failed for {Entity}", detail.Name);
            return detail;
        }
    }

    private void EnsureDetailInDirectory(EntityDetailVm detail)
    {
        var existing = AllEntities.FirstOrDefault(e =>
            string.Equals(e.Id, detail.EntityId, StringComparison.OrdinalIgnoreCase) ||
            string.Equals(e.Name, detail.Name, StringComparison.OrdinalIgnoreCase));

        if (existing is null)
        {
            AllEntities.Insert(0, new EntityCardVm(
                detail.EntityId,
                detail.Name,
                detail.Type,
                detail.Country,
                detail.Mentions,
                detail.Influence,
                ScoreNarrative(detail.NarrativeCount, detail.EventCount, detail.Mentions),
                detail.Sentiment));
            DirectoryTotal = Math.Max(DirectoryTotal, AllEntities.Count);
        }
        else if (existing.MentionCount == 0 && detail.Mentions > 0)
        {
            var idx = AllEntities.IndexOf(existing);
            AllEntities[idx] = existing with
            {
                MentionCount = detail.Mentions,
                Influence = detail.Influence,
                NarrativeScore = ScoreNarrative(detail.NarrativeCount, detail.EventCount, detail.Mentions),
                Sentiment = detail.Sentiment
            };
        }

        ApplyListFilter();
    }

    private static List<EntityCardVm> ParseEntityList(JsonElement root)
    {
        var arr = root.ValueKind == JsonValueKind.Array ? root :
                  root.TryGetProperty("entities", out var e) ? e :
                  root.TryGetProperty("data",     out var d) ? d : default;

        if (arr.ValueKind != JsonValueKind.Array) return new();

        return arr.EnumerateArray().Select(e =>
        {
            var id       = GetStr(e, "id", "entity_id") ?? "";
            var name     = GetStr(e, "name") ?? id;
            var type     = GetStr(e, "type", "entity_type") ?? "unknown";
            var country  = GetStr(e, "country");
            var mentions = GetInt(e, "mentions", "mention_count");
            var influence = GetDbl(e, "influence_score") ?? 0;
            var sentiment = GetDbl(e, "sentiment_score") ?? 0;
            var eventCount = GetInt(e, "event_count");
            var narrativeCount = GetInt(e, "narrative_count");
            return new EntityCardVm(id, name, type, country, mentions,
                ScoreInfluence(influence, mentions, eventCount, narrativeCount),
                ScoreNarrative(narrativeCount, eventCount, mentions),
                sentiment);
        }).ToList();
    }

    private static EntityDetailVm? ParseDetail(JsonElement d, string entityId)
    {
        if (d.ValueKind == JsonValueKind.Object)
        {
            if (d.TryGetProperty("actor", out var actor) && actor.ValueKind == JsonValueKind.Object)
                d = actor;
            else if (d.TryGetProperty("entity", out var entity) && entity.ValueKind == JsonValueKind.Object)
                d = entity;
            else if (d.TryGetProperty("data", out var data) && data.ValueKind == JsonValueKind.Object)
                d = data;
        }

        var name    = GetStr(d, "name") ?? entityId;
        var type    = GetStr(d, "type", "entity_type") ?? "unknown";
        var country = GetStr(d, "country");
        var desc    = GetStr(d, "description");
        var mentions = GetInt(d, "mentions", "mention_count");
        var influence = GetDbl(d, "influence_score");
        var sentiment = GetDbl(d, "sentiment_score") ?? 0;
        var eventCount = GetInt(d, "event_count");

        var aliases = new List<string>();
        if (d.TryGetProperty("aliases", out var al) && al.ValueKind == JsonValueKind.Array)
            aliases = al.EnumerateArray().Select(x => x.GetString() ?? "").Where(s => s.Length > 0).ToList();

        var coActors = new List<CoActorVm>();
        if (d.TryGetProperty("co_actors", out var co) && co.ValueKind == JsonValueKind.Array)
            coActors = co.EnumerateArray().Select(a =>
                new CoActorVm(
                    GetStr(a, "id") ?? GetStr(a, "entity_id") ?? "",
                    GetStr(a, "name") ?? GetStr(a, "id") ?? "?"))
                .Where(c => !string.IsNullOrEmpty(c.Id))
                .Take(12).ToList();

        var recentEvents = new List<RecentEventVm>();
        if (d.TryGetProperty("recent_events", out var re) && re.ValueKind == JsonValueKind.Array)
            recentEvents = re.EnumerateArray().Select(ev =>
                new RecentEventVm(
                    GetStr(ev, "event_id", "id") ?? "",
                    GetStr(ev, "title") ?? "",
                    GetStr(ev, "timestamp"),
                    GetDbl(ev, "risk_score") ?? 0))
                .Take(8).ToList();

        var signals = new List<EntitySignalVm>();
        if (d.TryGetProperty("signals", out var sigs) && sigs.ValueKind == JsonValueKind.Array)
            signals = sigs.EnumerateArray().Select(sig =>
                new EntitySignalVm(
                    GetStr(sig, "signal_id") ?? "",
                    GetStr(sig, "title") ?? "",
                    GetStr(sig, "source"),
                    GetStr(sig, "signal_type"),
                    GetDbl(sig, "confidence") ?? 0,
                    GetStr(sig, "cluster_id"),
                    GetStr(sig, "timestamp")))
                .Where(s => !string.IsNullOrWhiteSpace(s.SignalId))
                .Take(8).ToList();

        var narratives = new List<EntityNarrativeVm>();
        if (d.TryGetProperty("narratives", out var narrs) && narrs.ValueKind == JsonValueKind.Array)
            narratives = narrs.EnumerateArray().Select(narr =>
                new EntityNarrativeVm(
                    GetStr(narr, "narrative_id") ?? "",
                    GetStr(narr, "topic") ?? "",
                    GetStr(narr, "signal_type"),
                    GetStr(narr, "severity"),
                    GetDbl(narr, "strength") ?? 0,
                    GetDbl(narr, "confidence") ?? 0,
                    GetStr(narr, "detected_at")))
                .Where(n => !string.IsNullOrWhiteSpace(n.NarrativeId))
                .Take(6).ToList();

        var decisions = new List<EntityDecisionVm>();
        if (d.TryGetProperty("decision_history", out var decisionArr) && decisionArr.ValueKind == JsonValueKind.Array)
            decisions = decisionArr.EnumerateArray().Select(item =>
                new EntityDecisionVm(
                    GetStr(item, "decision_id") ?? "",
                    GetStr(item, "event_id") ?? "",
                    GetStr(item, "coa_text") ?? "",
                    GetStr(item, "status"),
                    GetStr(item, "outcome"),
                    GetStr(item, "analyst"),
                    GetStr(item, "created_at")))
                .Where(x => !string.IsNullOrWhiteSpace(x.DecisionId))
                .Take(8).ToList();

        var signalCount = GetInt(d, "signal_count");
        var clusterCount = GetInt(d, "cluster_count");
        var narrativeCount = GetInt(d, "narrative_count");
        var decisionCount = GetInt(d, "decision_count");
        var scoredInfluence = ScoreInfluence(influence, mentions, eventCount, narrativeCount);
        sentiment = ScoreSentiment(sentiment, eventCount, narrativeCount);

        return new EntityDetailVm(entityId, name, type, country, desc, mentions,
            scoredInfluence, sentiment, eventCount, aliases, coActors, recentEvents,
            signalCount, clusterCount, narrativeCount, decisionCount, signals, narratives, decisions);
    }

    private static EntityWikiVm? ParseWiki(JsonElement d)
    {
        if (d.ValueKind != JsonValueKind.Object) return null;
        var title = GetStr(d, "title");
        var extract = GetStr(d, "extract");
        var description = GetStr(d, "description");
        string? page = null;
        string? thumbnail = null;

        if (d.TryGetProperty("content_urls", out var urls) &&
            urls.TryGetProperty("desktop", out var desktop) &&
            desktop.TryGetProperty("page", out var pageProp) &&
            pageProp.ValueKind == JsonValueKind.String)
            page = pageProp.GetString();

        if (d.TryGetProperty("thumbnail", out var thumb) &&
            thumb.ValueKind == JsonValueKind.Object &&
            thumb.TryGetProperty("source", out var source) &&
            source.ValueKind == JsonValueKind.String)
            thumbnail = source.GetString();

        if (string.IsNullOrWhiteSpace(title) && string.IsNullOrWhiteSpace(extract))
            return null;

        return new EntityWikiVm(title ?? "Wikipedia", description, extract, page, thumbnail, GetStr(d, "_served_from"));
    }

    private static EntityDetailVm BuildFallbackDetail(string entityId, EntityWikiVm? wiki)
    {
        var label = wiki?.Title;
        if (string.IsNullOrWhiteSpace(label))
        {
            label = Uri.UnescapeDataString(entityId)
                .Replace("actor:", "", StringComparison.OrdinalIgnoreCase)
                .Replace("org:", "", StringComparison.OrdinalIgnoreCase)
                .Replace('_', ' ')
                .Replace('-', ' ')
                .Trim();
        }

        if (string.IsNullOrWhiteSpace(label))
            label = entityId;

        return new EntityDetailVm(
            entityId,
            label,
            "entity",
            null,
            wiki?.Extract,
            0,
            0,
            0,
            0,
            new(),
            new(),
            new(),
            0,
            0,
            0,
            0,
            new(),
            new(),
            new());
    }

    private static double ScoreInfluence(double? raw, int mentions, int eventCount, int narrativeCount)
    {
        if (raw.HasValue && raw.Value > 0)
            return Math.Clamp(raw.Value, 0, 1);

        var mentionSignal = Math.Min(Math.Log10(Math.Max(mentions, 0) + 1) / 3.2, 1.0);
        var eventSignal = Math.Min(eventCount / 25.0, 1.0);
        var narrativeSignal = Math.Min(narrativeCount / 8.0, 1.0);
        return Math.Round((mentionSignal * 0.55) + (eventSignal * 0.30) + (narrativeSignal * 0.15), 2);
    }

    public static double ScoreNarrative(int narrativeCount, int eventCount, int mentions)
    {
        var narrativeSignal = Math.Min(Math.Max(narrativeCount, 0) / 8.0, 1.0);
        var eventSignal = Math.Min(Math.Max(eventCount, 0) / 40.0, 1.0);
        var mentionSignal = Math.Min(Math.Log10(Math.Max(mentions, 0) + 1) / 3.4, 1.0);
        return Math.Round(Math.Max(narrativeSignal, (eventSignal * 0.70) + (mentionSignal * 0.30)), 2);
    }

    private string NormalizeEntityId(string raw)
    {
        var decoded = Uri.UnescapeDataString(raw).Trim();
        if (decoded.Contains(':'))
            return decoded;

        var slug = ToSlug(decoded);
        var match = AllEntities.FirstOrDefault(e =>
            string.Equals(ToSlug(e.Id), slug, StringComparison.OrdinalIgnoreCase) ||
            string.Equals(ToSlug(e.Name), slug, StringComparison.OrdinalIgnoreCase));

        return match?.Id ?? $"actor:{slug.Replace('-', '_')}";
    }

    private static string ToSlug(string value)
        => Uri.UnescapeDataString(value)
            .Replace("actor:", "", StringComparison.OrdinalIgnoreCase)
            .Replace("org:", "", StringComparison.OrdinalIgnoreCase)
            .Replace("loc:", "", StringComparison.OrdinalIgnoreCase)
            .Trim()
            .ToLowerInvariant()
            .Replace('_', '-')
            .Replace(' ', '-');

    private static double ScoreSentiment(double? raw, int eventCount, int narrativeCount)
    {
        if (raw.HasValue && Math.Abs(raw.Value) > 0.001)
            return Math.Clamp(raw.Value, -1, 1);

        return ScoreSentiment(Array.Empty<EventDto>(), eventCount, narrativeCount, 0);
    }

    private static double ScoreSentiment(IReadOnlyCollection<EventDto> events, int eventCount, int narrativeCount, int signalCount)
    {
        var explicitScores = events
            .Select(e => e.Sentiment?.Score)
            .Where(s => s.HasValue && Math.Abs(s.Value) > 0.001)
            .Select(s => s!.Value)
            .ToList();

        if (explicitScores.Count > 0)
            return Math.Round(explicitScores.Average(), 2);

        var riskAverage = events
            .Select(e => e.RiskScore ?? 0)
            .Where(r => r > 0)
            .DefaultIfEmpty(0)
            .Average();

        var riskSignal = Math.Min(riskAverage, 1.0) * 0.35;
        var eventSignal = Math.Min(Math.Max(eventCount, 0) / 50.0, 1.0) * 0.25;
        var narrativeSignal = Math.Min(Math.Max(narrativeCount, 0) / 12.0, 1.0) * 0.30;
        var signalSignal = Math.Min(Math.Max(signalCount, 0) / 10.0, 1.0) * 0.10;
        var score = -(riskSignal + eventSignal + narrativeSignal + signalSignal);
        return Math.Round(Math.Clamp(score, -0.85, 0.85), 2);
    }

    private static EntityGraphSummaryVm ParseGraphSummary(JsonElement root)
    {
        var nodes = 0;
        var edges = 0;
        var actorNodes = 0;
        var locationNodes = 0;
        var eventNodes = 0;

        if (root.TryGetProperty("nodes", out var nodeArr) && nodeArr.ValueKind == JsonValueKind.Array)
        {
            foreach (var node in nodeArr.EnumerateArray())
            {
                nodes++;
                var kind = GetStr(node, "group", "type", "entity_type")?.ToLowerInvariant();
                switch (kind)
                {
                    case "actor":
                    case "person":
                    case "organization":
                        actorNodes++;
                        break;
                    case "location":
                        locationNodes++;
                        break;
                    case "event":
                        eventNodes++;
                        break;
                }
            }
        }

        if (root.TryGetProperty("edges", out var edgeArr) && edgeArr.ValueKind == JsonValueKind.Array)
            edges = edgeArr.GetArrayLength();

        return new EntityGraphSummaryVm(nodes, edges, actorNodes, locationNodes, eventNodes);
    }

    private void ParseTimeline(JsonElement? raw)
    {
        SentimentLabels.Clear();
        SentimentValues.Clear();

        if (raw is null) return;

        var timeline = raw.Value.ValueKind == JsonValueKind.Array
            ? raw.Value
            : raw.Value.TryGetProperty("data", out var data) && data.ValueKind == JsonValueKind.Array
                ? data
                : default;

        if (timeline.ValueKind != JsonValueKind.Array) return;

        foreach (var row in timeline.EnumerateArray())
        {
            var bucket = GetStr(row, "bucket") ?? "";
            var score = GetDbl(row, "avg_score") ?? 0;
            if (DateTime.TryParse(bucket, out var dt))
                SentimentLabels.Add(dt.ToUniversalTime().ToString("MM-dd HH:mm"));
            else
                SentimentLabels.Add(bucket);
            SentimentValues.Add(score);
        }
    }

    public static string GetTypeClass(string? type) => type?.ToLower() switch
    {
        "person" or "actor" => "type-person",
        "organization" or "org" => "type-org",
        "location" => "type-location",
        "event" => "type-event",
        _ => "type-default"
    };

    public static string GetSentimentClass(double score)
        => score > 0.2 ? "positive" : score < -0.2 ? "negative" : "neutral";

    public static string GetRiskClass(double risk)
        => risk >= 0.7 ? "high" : risk >= 0.4 ? "medium" : "low";

    private static string? GetStr(JsonElement e, params string[] keys)
    {
        foreach (var k in keys)
            if (e.TryGetProperty(k, out var v) && v.ValueKind == JsonValueKind.String) return v.GetString();
        return null;
    }

    private static int GetInt(JsonElement e, params string[] keys)
    {
        foreach (var k in keys)
            if (e.TryGetProperty(k, out var v) && v.ValueKind == JsonValueKind.Number) return v.GetInt32();
        return 0;
    }

    private static double? GetDbl(JsonElement e, params string[] keys)
    {
        foreach (var k in keys)
            if (e.TryGetProperty(k, out var v) && v.ValueKind == JsonValueKind.Number) return v.GetDouble();
        return null;
    }

    public ValueTask DisposeAsync()
    {
        _searchCts?.Cancel();
        _searchCts?.Dispose();
        _refreshTimer?.Stop();
        _refreshTimer?.Dispose();
        return ValueTask.CompletedTask;
    }

    private static AnalystIndicatorDto BuildDetailIndicator(EntityDetailVm detail, EntityGraphSummaryVm? graphSummary) => new()
    {
        Id = detail.EntityId,
        Label = detail.Name,
        Category = "actor",
        IndicatorKind = "actor",
        EvidenceKind = "correlated",
        AssessmentKind = "actor_dossier",
        Severity = SeverityFromScore(Math.Max(detail.Influence, detail.EventCount >= 4 ? 0.45 : 0.2)),
        Driver = detail.SignalCount > 0 ? "signal recurrence" : detail.NarrativeCount > 0 ? "narrative attachment" : "event recurrence",
        DriverCode = detail.SignalCount > 0 ? "signal_recurrence" : detail.NarrativeCount > 0 ? "narrative_attachment" : "event_recurrence",
        Trajectory = detail.EventCount >= 4 || detail.SignalCount >= 3 ? "rising" : "stable",
        TrajectoryCode = detail.EventCount >= 4 || detail.SignalCount >= 3 ? "rising" : "stable",
        RecommendedAction = detail.RecentEvents.Any() ? "review linked incidents" : "inspect graph evidence",
        RecommendedActionCode = detail.RecentEvents.Any() ? "review_linked_incidents" : "inspect_graph_evidence",
        Region = detail.Country,
        Score = Math.Max(detail.Influence, detail.EventCount / 10d),
        Confidence = Math.Max(0, detail.Influence),
        Corroboration = Math.Min(1.0, (detail.EventCount + detail.SignalCount + detail.NarrativeCount) / 10d),
        Summary = $"{detail.Name} has {detail.EventCount} linked incident(s), {detail.SignalCount} signal(s), and {detail.NarrativeCount} narrative(s).",
        ObservationSummary = $"{detail.EventCount} incident(s), {detail.SignalCount} signal(s), and {detail.DecisionCount} decision record(s) were observed for this dossier.",
        AssessmentSummary = $"Influence score is {detail.Influence:0.00} with sentiment {detail.Sentiment:0.00}.",
        CorrelationSummary = $"{detail.CoActors.Count} co-actor(s), {graphSummary?.NodeCount ?? 0} graph node(s), and {detail.RecentEvents.Count} recent incident(s) are connected.",
        Linked = new IndicatorLinkCountsDto
        {
            Actors = detail.CoActors.Count,
            Narratives = detail.NarrativeCount,
            Signals = detail.SignalCount,
            Events = detail.EventCount,
            Regions = string.IsNullOrWhiteSpace(detail.Country) ? 0 : 1
        }
    };

    private static string SeverityFromScore(double score)
        => score switch
        {
            >= 0.75 => "critical",
            >= 0.55 => "high",
            >= 0.35 => "medium",
            _ => "low",
        };
}

public sealed record EntityCardVm(
    string Id, string Name, string Type, string? Country,
    int MentionCount, double Influence, double NarrativeScore, double Sentiment);

public sealed record EntityDetailVm(
    string EntityId, string Name, string Type, string? Country,
    string? Description, int Mentions, double Influence, double Sentiment,
    int EventCount, List<string> Aliases,
    List<CoActorVm> CoActors, List<RecentEventVm> RecentEvents,
    int SignalCount, int ClusterCount, int NarrativeCount, int DecisionCount,
    List<EntitySignalVm> Signals, List<EntityNarrativeVm> Narratives, List<EntityDecisionVm> Decisions);

public sealed record EntityWikiVm(
    string Title,
    string? Description,
    string? Extract,
    string? PageUrl,
    string? ThumbnailUrl,
    string? ServedFrom);

public sealed record CoActorVm(string Id, string Name);

public sealed record RecentEventVm(string EventId, string Title, string? Timestamp, double RiskScore);

public sealed record EntitySignalVm(
    string SignalId,
    string Title,
    string? Source,
    string? SignalType,
    double Confidence,
    string? ClusterId,
    string? Timestamp);

public sealed record EntityNarrativeVm(
    string NarrativeId,
    string Topic,
    string? SignalType,
    string? Severity,
    double Strength,
    double Confidence,
    string? DetectedAt);

public sealed record EntityDecisionVm(
    string DecisionId,
    string EventId,
    string CoaText,
    string? Status,
    string? Outcome,
    string? Analyst,
    string? CreatedAt);

public sealed record EntityGraphSummaryVm(
    int NodeCount,
    int EdgeCount,
    int ActorNodeCount,
    int LocationNodeCount,
    int EventNodeCount);
