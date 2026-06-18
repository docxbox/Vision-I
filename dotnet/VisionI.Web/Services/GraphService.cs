using System.Text.Json;
using VisionI.Web.Models;

namespace VisionI.Web.Services;

/// <summary>
/// Scoped ViewModel service for the Graph Explorer page.
/// Keeps graph parsing, filtering, search, and related-node tracking out of the Razor page.
/// </summary>
public sealed class GraphService : IDisposable
{
    private readonly ApiService _api;
    private readonly ToastService _toast;
    private readonly ILogger<GraphService> _log;
    private readonly Dictionary<string, GraphNodeVm> _nodeIndex = new(StringComparer.OrdinalIgnoreCase);
    private CancellationTokenSource? _nodeEvidenceCts;

    private List<GraphNodeVm> _allNodes = new();
    private List<GraphEdgeVm> _allEdges = new();
    private List<EntityDto> _entityDirectory = new();
    private List<DetectedSituationDto> _activeSituations = new();
    private bool _entityDirectoryLoaded;
    private bool _activeSituationsLoaded;

    public double SentimentMin { get; set; } = -1;
    public double SentimentMax { get; set; } = 1;
    public int MinMentions { get; set; }
    public int MaxAgeDays { get; set; } = 30;
    public string NodeType { get; set; } = "";
    public string SearchTerm { get; set; } = "";

    public bool Loading { get; private set; } = true;
    public bool Expanding { get; private set; }
    public string? Mode { get; private set; }
    public string? RootEntityId { get; private set; }
    public string DataSourceLabel { get; private set; } = "Postgres snapshot";
    public string SurfaceNote { get; private set; } = "Overview mode shows a recent event-linked snapshot built from operational data.";
    public GraphSurfaceEvidenceVm? GraphEvidence { get; private set; }
    public GraphNodeVm? SelectedNode { get; private set; }
    public List<GraphNodeVm> RelatedNodes { get; private set; } = new();
    public List<GraphEdgeVm> RelatedEdges { get; private set; } = new();
    public List<GraphSearchHit> SearchHits { get; private set; } = new();
    public string? FocusNodeId { get; private set; }
    public bool LoadingNodeEvidence { get; private set; }
    public GraphNodeEvidenceVm? SelectedNodeEvidence { get; private set; }

    public List<GraphNodeVm> VisibleNodes { get; private set; } = new();
    public List<GraphEdgeVm> VisibleEdges { get; private set; } = new();

    public int NodeCount => _allNodes.Count;
    public int EdgeCount => _allEdges.Count;
    public bool IsEntityScoped => !string.IsNullOrWhiteSpace(RootEntityId);
    public bool IsActorScoped => !string.IsNullOrWhiteSpace(RootEntityId) &&
                                 (RootEntityId.StartsWith("actor:", StringComparison.OrdinalIgnoreCase) ||
                                  RootEntityId.StartsWith("org:", StringComparison.OrdinalIgnoreCase));
    public IEnumerable<string> AvailableNodeTypes =>
        _allNodes.Select(n => n.Group)
            .Where(static g => !string.IsNullOrWhiteSpace(g))
            .Cast<string>()
            .Distinct(StringComparer.OrdinalIgnoreCase)
            .OrderBy(static g => g);

    public event Action? OnChanged;
    public event Action? OnGraphReady;
    public event Action<List<GraphNodeVm>, List<GraphEdgeVm>>? OnNodesExpanded;

    public GraphService(ApiService api, ToastService toast, ILogger<GraphService> log)
    {
        _api = api;
        _toast = toast;
        _log = log;
    }

    public async Task LoadAsync(string? mode, string? entityId)
    {
        Loading = true;
        Mode = mode;
        RootEntityId = entityId;
        SelectedNode = null;
        GraphEvidence = null;
        RelatedNodes = new();
        RelatedEdges = new();
        SearchHits = new();
        FocusNodeId = null;
        SelectedNodeEvidence = null;
        NotifyChanged();

        try
        {
            JsonElement? raw;
            if (!string.IsNullOrWhiteSpace(entityId))
            {
                raw = await _api.GetAsync<JsonElement?>($"api/entities/{Uri.EscapeDataString(entityId)}/graph?depth=2");
                DataSourceLabel = "Neo4j ego graph";
                SurfaceNote = IsActorScoped
                    ? "This view is a live actor-centric ego graph from Neo4j."
                    : "This focus view is only reliable for actor-style graph ids. Other node types may fall back to overview data.";

                if (raw is null)
                {
                    raw = await _api.GetOntologyGraphAsync(36);
                    DataSourceLabel = "Postgres snapshot";
                    SurfaceNote = "The requested entity graph was unavailable, so this page fell back to the latest event-linked overview snapshot.";
                }
            }
            else
            {
                raw = await _api.GetOntologyGraphAsync(36);
                DataSourceLabel = "Postgres snapshot";
                SurfaceNote = "Overview mode shows a recent event-linked snapshot built from operational data. Use an actor focus for live Neo4j ego exploration.";
            }

            if (raw is not null)
            {
                GraphEvidence = ParseGraphEvidence(raw.Value);
                Parse(raw.Value);
            }

            _ = EnsureEntityDirectoryInBackgroundAsync();
        }
        catch (Exception ex)
        {
            _log.LogError(ex, "GraphService.LoadAsync failed");
            _toast.ShowError("Could not load graph data.");
        }
        finally
        {
            Loading = false;
            ApplyFilters();
            OnGraphReady?.Invoke();
            NotifyChanged();
        }
    }

    public async Task ExpandNodeAsync(string nodeId)
    {
        if (!_nodeIndex.ContainsKey(nodeId))
            return;

        Expanding = true;
        NotifyChanged();

        try
        {
            if (!_nodeIndex.TryGetValue(nodeId, out var seed) || !SupportsActorExpansion(seed))
            {
                _toast.Show("Expansion is currently supported for actor and organization nodes.");
                return;
            }

            var raw = await _api.GetAsync<JsonElement?>($"api/entities/{Uri.EscapeDataString(nodeId)}/graph?depth=1");
            if (raw is null) return;

            GraphEvidence = ParseGraphEvidence(raw.Value) ?? GraphEvidence;

            var (newNodes, newEdges) = ParseIncremental(raw.Value);

            var addedNodes = newNodes.Where(n => !_nodeIndex.ContainsKey(n.Id)).ToList();
            foreach (var node in addedNodes)
            {
                _nodeIndex[node.Id] = node;
                _allNodes.Add(node);
            }

            var edgeKeys = _allEdges
                .Select(static e => $"{e.From}|{e.To}|{e.Relation}")
                .ToHashSet(StringComparer.OrdinalIgnoreCase);
            var addedEdges = newEdges
                .Where(e => edgeKeys.Add($"{e.From}|{e.To}|{e.Relation}"))
                .ToList();
            _allEdges.AddRange(addedEdges);

            if (addedNodes.Count > 0 || addedEdges.Count > 0)
            {
                ApplyFilters();
                OnNodesExpanded?.Invoke(addedNodes, addedEdges);
            }
        }
        catch (Exception ex)
        {
            _log.LogError(ex, "GraphService.ExpandNodeAsync failed for {NodeId}", nodeId);
            _toast.ShowError("Could not expand node.");
        }
        finally
        {
            Expanding = false;
            NotifyChanged();
        }
    }

    public void ApplyFilters()
    {
        var cutoff = DateTimeOffset.UtcNow.AddDays(-MaxAgeDays);

        VisibleNodes = _allNodes
            .Where(n => n.Mentions >= MinMentions)
            .Where(n => string.IsNullOrWhiteSpace(NodeType) || string.Equals(n.Group, NodeType, StringComparison.OrdinalIgnoreCase))
            .Where(n => n.SentimentScore is null || (n.SentimentScore >= SentimentMin && n.SentimentScore <= SentimentMax))
            .Where(n => n.Timestamp is null || n.Timestamp >= cutoff)
            .OrderByDescending(static n => n.Mentions)
            .ToList();

        var visibleIds = VisibleNodes.Select(static n => n.Id).ToHashSet(StringComparer.OrdinalIgnoreCase);
        VisibleEdges = _allEdges
            .Where(e => visibleIds.Contains(e.From) && visibleIds.Contains(e.To))
            .OrderByDescending(static e => e.Weight)
            .ToList();

        if (SelectedNode is not null && !visibleIds.Contains(SelectedNode.Id))
            ClearSelection();
        else if (SelectedNode is not null)
            UpdateSelectionArtifacts(SelectedNode.Id);

        if (!string.IsNullOrWhiteSpace(SearchTerm))
            UpdateSearchHits();

        NotifyChanged();
    }

    public async Task SearchAsync()
    {
        await EnsureEntityDirectoryAsync();
        UpdateSearchHits();
        NotifyChanged();
    }

    public async Task SelectSearchHitAsync(GraphSearchHit hit)
    {
        SearchHits = new(); // close dropdown immediately
        NotifyChanged();

        if (!hit.InCurrentGraph)
        {
            // LoadAsync fires OnGraphReady → RenderGraphAsync → FocusPendingNodeAsync
            // Set FocusNodeId BEFORE LoadAsync so RenderGraphAsync picks it up
            FocusNodeId = hit.Id;
            await LoadAsync("ego", hit.Id);
            // After load, SelectNode to populate panel (FocusNodeId already set above)
            if (_nodeIndex.ContainsKey(hit.Id))
                SelectNode(hit.Id);
            return; // focus handled inside RenderGraphAsync
        }

        if (_nodeIndex.ContainsKey(hit.Id))
        {
            SelectNode(hit.Id);
            FocusNodeId = hit.Id;
        }
        else
        {
            _toast.Show("That entity is not available in the current graph snapshot yet.");
        }

        NotifyChanged();
    }

    public void ClearFocusRequest() => FocusNodeId = null;

    public void SelectNode(string nodeId)
    {
        if (!_nodeIndex.TryGetValue(nodeId, out var node))
            return;

        SelectedNode = node;
        FocusNodeId = nodeId;
        UpdateSelectionArtifacts(nodeId);
        _ = LoadNodeEvidenceAsync(node);
        NotifyChanged();
    }

    public void ClearSelection()
    {
        SelectedNode = null;
        RelatedNodes = new();
        RelatedEdges = new();
        FocusNodeId = null;
        SelectedNodeEvidence = null;
        NotifyChanged();
    }

    public bool SupportsActorExpansion(GraphNodeVm? node)
        => node is not null &&
           (string.Equals(node.Group, "actor", StringComparison.OrdinalIgnoreCase) ||
            string.Equals(node.Group, "organization", StringComparison.OrdinalIgnoreCase) ||
            node.Id.StartsWith("actor:", StringComparison.OrdinalIgnoreCase) ||
            node.Id.StartsWith("org:", StringComparison.OrdinalIgnoreCase));

    public bool SupportsEntityProfile(GraphNodeVm? node)
        => node is not null &&
           !string.Equals(node.Group, "event", StringComparison.OrdinalIgnoreCase) &&
           !node.Id.StartsWith("event:", StringComparison.OrdinalIgnoreCase) &&
           !string.IsNullOrWhiteSpace(node.Label);

    public bool SupportsEntityProfile(string nodeId)
        => _nodeIndex.TryGetValue(nodeId, out var node) && SupportsEntityProfile(node);

    public async Task<string?> EnsureEntityMappedAsync(string nodeId)
    {
        if (!_nodeIndex.TryGetValue(nodeId, out var node))
            return null;

        if (!SupportsEntityProfile(node))
            return null;

        try
        {
            var raw = await _api.MapGraphEntityAsync(node.Id, node.Label, node.Group);
            if (raw is { ValueKind: JsonValueKind.Object } json &&
                json.TryGetProperty("id", out var idProp) &&
                idProp.ValueKind == JsonValueKind.String)
            {
                var mappedId = idProp.GetString();
                if (!string.IsNullOrWhiteSpace(mappedId))
                {
                    _toast.Show("Entity mapped and recorded.");
                    return mappedId;
                }
            }
        }
        catch (Exception ex)
        {
            _log.LogWarning(ex, "Entity mapping failed for graph node {NodeId}", nodeId);
        }

        _toast.ShowError("Could not map that graph node to an entity profile.");
        return null;
    }

    private async Task EnsureEntityDirectoryAsync()
    {
        if (_entityDirectoryLoaded)
            return;

        try
        {
            var response = await _api.GetEntitiesAsync(limit: 500);
            _entityDirectory = response?.Entities ?? new();
            _entityDirectoryLoaded = true;
        }
        catch (Exception ex)
        {
            _log.LogWarning(ex, "Graph entity directory preload failed");
        }
    }

    private async Task EnsureEntityDirectoryInBackgroundAsync()
    {
        try
        {
            await EnsureEntityDirectoryAsync();
            UpdateSearchHits();
            NotifyChanged();
        }
        catch { }
    }

    private async Task EnsureActiveSituationsAsync()
    {
        if (_activeSituationsLoaded)
            return;

        try
        {
            var response = await _api.GetSituationsAsync(limit: 40, status: "active");
            _activeSituations = response?.Situations ?? new();
            _activeSituationsLoaded = true;
        }
        catch (Exception ex)
        {
            _log.LogWarning(ex, "Graph active situations preload failed");
        }
    }

    private void UpdateSelectionArtifacts(string nodeId)
    {
        RelatedEdges = VisibleEdges
            .Where(e => string.Equals(e.From, nodeId, StringComparison.OrdinalIgnoreCase) ||
                        string.Equals(e.To, nodeId, StringComparison.OrdinalIgnoreCase))
            .OrderByDescending(static e => e.Weight)
            .ToList();

        var relatedIds = RelatedEdges
            .SelectMany(e => new[] { e.From, e.To })
            .Where(id => !string.Equals(id, nodeId, StringComparison.OrdinalIgnoreCase))
            .Distinct(StringComparer.OrdinalIgnoreCase)
            .ToHashSet(StringComparer.OrdinalIgnoreCase);

        RelatedNodes = VisibleNodes
            .Where(n => relatedIds.Contains(n.Id))
            .OrderByDescending(static n => n.Mentions)
            .Take(12)
            .ToList();
    }

    private void UpdateSearchHits()
    {
        var term = SearchTerm.Trim();
        if (term.Length < 2)
        {
            SearchHits = new();
            return;
        }

        var visibleHits = _allNodes
            .Where(n => n.Label.Contains(term, StringComparison.OrdinalIgnoreCase) ||
                        n.Id.Contains(term, StringComparison.OrdinalIgnoreCase))
            .OrderByDescending(static n => n.Mentions)
            .Take(8)
            .Select(n => new GraphSearchHit(
                n.Id,
                n.Label,
                n.Group ?? "node",
                true,
                $"mentions {n.Mentions}"))
            .ToList();

        var existingIds = visibleHits.Select(static h => h.Id).ToHashSet(StringComparer.OrdinalIgnoreCase);
        var directoryHits = _entityDirectory
            .Where(e =>
                !string.IsNullOrWhiteSpace(e.EntityId ?? e.Id) &&
                ((e.Name?.Contains(term, StringComparison.OrdinalIgnoreCase) ?? false) ||
                 (e.EntityId?.Contains(term, StringComparison.OrdinalIgnoreCase) ?? false) ||
                 (e.Id?.Contains(term, StringComparison.OrdinalIgnoreCase) ?? false)))
            .Where(e => !existingIds.Contains(e.EntityId ?? e.Id ?? ""))
            .OrderByDescending(static e => e.MentionCount)
            .Take(8)
            .Select(e => new GraphSearchHit(
                e.EntityId ?? e.Id ?? "",
                e.Name ?? e.EntityId ?? e.Id ?? "entity",
                e.Type ?? "entity",
                _nodeIndex.ContainsKey(e.EntityId ?? e.Id ?? ""),
                $"mentions {e.MentionCount}"))
            .Where(static h => !string.IsNullOrWhiteSpace(h.Id))
            .ToList();

        SearchHits = visibleHits.Concat(directoryHits).Take(10).ToList();
    }

    private void Parse(JsonElement root)
    {
        _allNodes.Clear();
        _allEdges.Clear();
        _nodeIndex.Clear();

        var (nodes, edges) = ParseIncremental(root);
        _allNodes = nodes;
        _allEdges = edges;

        foreach (var node in nodes)
            _nodeIndex[node.Id] = node;
    }

    private static (List<GraphNodeVm> nodes, List<GraphEdgeVm> edges) ParseIncremental(JsonElement root)
    {
        var nodes = new List<GraphNodeVm>();
        var edges = new List<GraphEdgeVm>();

        if (TryGetArray(root, out var nodeArr, "nodes", "entities"))
        {
            foreach (var n in nodeArr)
            {
                var id = GetStr(n, "id", "entity_id") ?? Guid.NewGuid().ToString("N")[..8];
                var name = GetStr(n, "name", "label", "title") ?? id;
                var group = GetStr(n, "group", "type", "entity_type") ?? "unknown";
                var eventType = GetStr(n, "event_type");
                var mentions = GetInt(n, "mention_count", "mentions");
                var sentiment = GetDbl(n, "sentiment_score", "sentiment");
                var ts = ParseTs(GetStr(n, "last_seen", "timestamp"));

                nodes.Add(new GraphNodeVm(id, name, group, eventType, sentiment, ts, Math.Max(mentions, 1)));
            }
        }

        if (TryGetArray(root, out var edgeArr, "edges", "relationships", "links"))
        {
            foreach (var e in edgeArr)
            {
                var from = GetStr(e, "source", "from", "src") ?? "";
                var to = GetStr(e, "target", "to", "dst") ?? "";
                var rel = GetStr(e, "relation_type", "relation", "type", "label") ?? "";
                var weight = GetDbl(e, "weight") ?? 1;
                var evidenceMode = GetStr(e, "evidence_mode") ?? "observed";
                if (!string.IsNullOrEmpty(from) && !string.IsNullOrEmpty(to))
                    edges.Add(new GraphEdgeVm(from, to, rel, weight, evidenceMode));
            }
        }

        return (nodes, edges);
    }

    private static bool TryGetArray(JsonElement root, out IEnumerable<JsonElement> result, params string[] keys)
    {
        foreach (var key in keys)
        {
            if (root.TryGetProperty(key, out var arr) && arr.ValueKind == JsonValueKind.Array)
            {
                result = arr.EnumerateArray();
                return true;
            }
        }

        result = Enumerable.Empty<JsonElement>();
        return false;
    }

    private static string? GetStr(JsonElement e, params string[] keys)
    {
        foreach (var key in keys)
        {
            if (e.TryGetProperty(key, out var value) && value.ValueKind == JsonValueKind.String)
                return value.GetString();
        }

        return null;
    }

    private static int GetInt(JsonElement e, params string[] keys)
    {
        foreach (var key in keys)
        {
            if (e.TryGetProperty(key, out var value) && value.ValueKind == JsonValueKind.Number)
                return value.GetInt32();
        }

        return 0;
    }

    private static double? GetDbl(JsonElement e, params string[] keys)
    {
        foreach (var key in keys)
        {
            if (e.TryGetProperty(key, out var value) && value.ValueKind == JsonValueKind.Number)
                return value.GetDouble();
        }

        return null;
    }

    private static DateTimeOffset? ParseTs(string? value)
        => DateTime.TryParse(value, out var parsed)
            ? new DateTimeOffset(parsed.ToUniversalTime())
            : null;

    private void NotifyChanged() => OnChanged?.Invoke();

    public void Dispose()
    {
        _nodeEvidenceCts?.Cancel();
        _nodeEvidenceCts?.Dispose();
    }

    private async Task LoadNodeEvidenceAsync(GraphNodeVm node)
    {
        _nodeEvidenceCts?.Cancel();
        _nodeEvidenceCts?.Dispose();
        _nodeEvidenceCts = new CancellationTokenSource();
        var ct = _nodeEvidenceCts.Token;

        LoadingNodeEvidence = true;
        SelectedNodeEvidence = null;
        NotifyChanged();

        try
        {
            EntityDetailVm? detail = null;
            EventFullDto? similarAnchor = null;
            var summary = new GraphNeighborhoodSummaryVm(
                RelatedNodes.Count,
                RelatedEdges.Count,
                RelatedNodes.Count(n => string.Equals(n.Group, "actor", StringComparison.OrdinalIgnoreCase) ||
                                        string.Equals(n.Group, "person", StringComparison.OrdinalIgnoreCase) ||
                                        string.Equals(n.Group, "organization", StringComparison.OrdinalIgnoreCase)),
                RelatedNodes.Count(n => string.Equals(n.Group, "event", StringComparison.OrdinalIgnoreCase)),
                RelatedNodes.Count(n => string.Equals(n.Group, "location", StringComparison.OrdinalIgnoreCase)));

            if (SupportsActorExpansion(node))
            {
                var detailRaw = await _api.GetEntityActorDetailAsync(node.Id, ct);
                if (detailRaw.HasValue)
                    detail = ParseEvidenceDetail(detailRaw.Value, node.Id);

                var anchorEventId = detail?.RecentEvents.FirstOrDefault()?.EventId;
                if (!string.IsNullOrWhiteSpace(anchorEventId))
                    similarAnchor = await _api.GetEventFullAsync(anchorEventId, socialLimit: 6, similarLimit: 4, ct: ct);
            }

            await EnsureActiveSituationsAsync();

            var recentEventIds = detail?.RecentEvents
                .Select(static e => e.EventId)
                .Where(static id => !string.IsNullOrWhiteSpace(id))
                .ToHashSet(StringComparer.OrdinalIgnoreCase)
                ?? new HashSet<string>(StringComparer.OrdinalIgnoreCase);

            var linkedSituations = recentEventIds.Count == 0
                ? new List<DetectedSituationDto>()
                : _activeSituations
                    .Where(s => s.EventIds.Any(recentEventIds.Contains))
                    .OrderByDescending(static s => s.RiskScore)
                    .ThenBy(static s => s.Title)
                    .Take(4)
                    .ToList();

            SelectedNodeEvidence = new GraphNodeEvidenceVm(detail, similarAnchor, summary, linkedSituations);
        }
        catch (OperationCanceledException)
        {
        }
        catch (Exception ex)
        {
            _log.LogDebug(ex, "Graph node evidence load failed for {NodeId}", node.Id);
        }
        finally
        {
            LoadingNodeEvidence = false;
            NotifyChanged();
        }
    }

    private static EntityDetailVm? ParseEvidenceDetail(JsonElement d, string entityId)
    {
        var name = GetStr(d, "name") ?? entityId;
        var type = GetStr(d, "type", "entity_type") ?? "unknown";
        var country = GetStr(d, "country");
        var desc = GetStr(d, "description");
        var mentions = GetInt(d, "mentions", "mention_count");
        var influence = GetDbl(d, "influence_score") ?? 0;
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
                .Take(10)
                .ToList();

        var recentEvents = new List<RecentEventVm>();
        if (d.TryGetProperty("recent_events", out var re) && re.ValueKind == JsonValueKind.Array)
            recentEvents = re.EnumerateArray().Select(ev =>
                    new RecentEventVm(
                        GetStr(ev, "event_id", "id") ?? "",
                        GetStr(ev, "title") ?? "",
                        GetStr(ev, "timestamp"),
                        GetDbl(ev, "risk_score") ?? 0))
                .Where(r => !string.IsNullOrWhiteSpace(r.EventId))
                .Take(6)
                .ToList();

        return new EntityDetailVm(
            entityId, name, type, country, desc, mentions, influence, sentiment, eventCount,
            aliases, coActors, recentEvents,
            0, 0, 0, 0,
            new(), new(), new());
    }

    private static GraphSurfaceEvidenceVm? ParseGraphEvidence(JsonElement root)
    {
        if (!root.TryGetProperty("evidence", out var evidence) || evidence.ValueKind != JsonValueKind.Object)
            return null;

        return new GraphSurfaceEvidenceVm(
            GetStr(evidence, "graph_source") ?? "unknown",
            GetInt(evidence, "actor_count"),
            GetInt(evidence, "event_count"),
            GetInt(evidence, "location_count"),
            GetInt(evidence, "signal_count"),
            GetInt(evidence, "narrative_count"),
            GetInt(evidence, "theme_count"));
    }
}

public sealed record GraphNodeVm(
    string Id,
    string Label,
    string? Group,
    string? EventType,
    double? SentimentScore = null,
    DateTimeOffset? Timestamp = null,
    int Mentions = 1);

public sealed record GraphEdgeVm(
    string From,
    string To,
    string Relation,
    double Weight = 1,
    string EvidenceMode = "observed");

public sealed record GraphSearchHit(
    string Id,
    string Label,
    string Kind,
    bool InCurrentGraph,
    string Subtitle);

public sealed record GraphNeighborhoodSummaryVm(
    int VisibleNeighborCount,
    int VisibleRelationshipCount,
    int ActorNeighborCount,
    int EventNeighborCount,
    int LocationNeighborCount);

public sealed record GraphNodeEvidenceVm(
    EntityDetailVm? EntityDetail,
    EventFullDto? SimilarAnchor,
    GraphNeighborhoodSummaryVm NeighborhoodSummary,
    IReadOnlyList<DetectedSituationDto> LinkedSituations);

public sealed record GraphSurfaceEvidenceVm(
    string GraphSource,
    int ActorCount,
    int EventCount,
    int LocationCount,
    int SignalCount,
    int NarrativeCount,
    int ThemeCount);
