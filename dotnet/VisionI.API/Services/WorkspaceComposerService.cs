using System.Text.Json;
using System.Data;
using Microsoft.EntityFrameworkCore;
using VisionI.API.Infrastructure;
using VisionI.API.Models;
using VisionI.API.Models.Entities;
using VisionI.API.Repositories;

namespace VisionI.API.Services;

public class WorkspaceComposerService : IWorkspaceComposerService
{
    private readonly IWorkspaceRepository _repo;
    private readonly AppDbContext _db;
    private readonly PythonApiClient _python;
    private readonly ILogger<WorkspaceComposerService> _log;

    private static readonly Dictionary<string, int> _ttlMinutes = new()
    {
        ["overview"]    = 5,
        ["map"]         = 5,
        ["assets"]      = 5,
        ["developments"]= 5,
        ["sentiment"]   = 10,
        ["entities"]    = 10,
        ["correlation"] = 10,
        ["actions"]     = 10,
    };

    private static readonly string[] _workspaceIngestSources =
    [
        "gdelt",
        "rss",
        "news",
        "socials",
        "youtube",
        "usgs",
        "opensky",
        "ais",
        "stocks",
    ];

    private static readonly TimeSpan PythonResolverBudget = TimeSpan.FromSeconds(8);
    // The Correlation tab traverses the Neo4j graph (correlated actors + connected events).
    // On a single-worker Python tier it can queue behind a heavy assets-snapshot scan, so its
    // dedicated (non-bundled) fetch gets a larger budget; the result caches for the snapshot TTL.
    private static readonly TimeSpan CorrelationResolverBudget = TimeSpan.FromSeconds(25);

    public WorkspaceComposerService(
        IWorkspaceRepository repo,
        AppDbContext db,
        PythonApiClient python,
        ILogger<WorkspaceComposerService> log)
    {
        _repo = repo;
        _db = db;
        _python = python;
        _log = log;
    }

    public async Task<WorkspaceOverviewDto?> GetOverviewAsync(string slug, CancellationToken ct = default)
    {
        var ws = await _repo.GetBySlugAsync(slug, ct);
        if (ws is null) return null;

        var snapshot = await _repo.GetSnapshotAsync(ws.Id, "overview", ws.DefaultWindowHours, ct);
        if (snapshot is not null)
        {
            var cached = JsonSerializer.Deserialize<WorkspaceOverviewDto>(snapshot.PayloadJson);
            if (cached is not null)
                return cached with { FromCache = true };
        }

        var body = BuildResolveBody(ws);
        var (events, assets, entities, sentiment, correlation) = await FetchAllParallelAsync(body, ct);

        var topEvents    = ParseEvents(events, 20);
        var topEntities  = ParseEntities(entities);
        var narratives   = ParseNarratives(correlation);
        var anomalyCount = ParseAssets(assets, 200).Count(a => a.IsAnomaly);
        var dto = new WorkspaceOverviewDto(
            ws.Slug, ws.Title,
            EventCount:     IntProp(events,      "total"),
            MaxRiskScore:   MaxDouble(events,     "events", "risk_score"),
            AssetCount:     IntProp(assets,       "total"),
            VesselCount:    IntNested(assets,     "counts", "vessel"),
            FlightCount:    IntNested(assets,     "counts", "aircraft"),
            SentimentScore: AvgSentiment(sentiment),
            NarrativeCount: ArrayLen(correlation, "narratives"),
            TopEvents:      topEvents,
            SummaryBullets: BuildSummaryBullets(topEvents, topEntities, narratives, anomalyCount, AvgSentiment(sentiment)),
            TopActor:       topEntities.FirstOrDefault()?.Name,
            TopNarrative:   narratives.FirstOrDefault() is { } n ? $"{n.Source} → {n.Target}" : null,
            AnomalyDelta:   anomalyCount,
            GeneratedAt:    DateTime.UtcNow,
            FromCache:      false
        );

        await SaveSnapshotAsync(ws.Id, "overview", ws.DefaultWindowHours, dto, ct);
        return dto;
    }

    public async Task<WorkspaceMapDto?> GetMapAsync(string slug, CancellationToken ct = default)
    {
        var ws = await _repo.GetBySlugAsync(slug, ct);
        if (ws is null) return null;

        var snapshot = await _repo.GetSnapshotAsync(ws.Id, "map", ws.DefaultWindowHours, ct);
        if (snapshot is not null)
        {
            var cached = JsonSerializer.Deserialize<WorkspaceMapDto>(snapshot.PayloadJson);
            if (cached is not null)
                return cached with { FromCache = true };
        }

        var body = BuildResolveBody(ws);
        var assetsTask = ResolvePythonAsync("/workspace/resolve-assets", body, ct);
        var eventsTask = ResolvePythonAsync("/workspace/resolve-events", body, ct);
        await Task.WhenAll(assetsTask, eventsTask);
        var assets = assetsTask.Result;
        var events = eventsTask.Result;

        var geoFilter = ws.GeoFilters.FirstOrDefault(f => f.Name == "primary") ?? ws.GeoFilters.FirstOrDefault();

        var dto = new WorkspaceMapDto(
            ws.Slug,
            AssetCount:      IntProp(assets, "total"),
            EventCount:      IntProp(events, "total"),
            PrimaryGeoFilter: geoFilter is null ? null : new WorkspaceGeoFilterDto(
                geoFilter.Id, geoFilter.FilterType, geoFilter.Name,
                geoFilter.MinLat, geoFilter.MaxLat, geoFilter.MinLon, geoFilter.MaxLon),
            AssetItems:      ParseAssets(assets, 750),
            EventItems:      ParseGeoEvents(events, 200),
            GeneratedAt:     DateTime.UtcNow,
            FromCache:       false
        );

        await SaveSnapshotAsync(ws.Id, "map", ws.DefaultWindowHours, dto, ct);
        return dto;
    }

    public async Task<WorkspaceDevelopmentsDto?> GetDevelopmentsAsync(string slug, CancellationToken ct = default)
    {
        var ws = await _repo.GetBySlugAsync(slug, ct);
        if (ws is null) return null;

        var snapshot = await _repo.GetSnapshotAsync(ws.Id, "developments", ws.DefaultWindowHours, ct);
        if (snapshot is not null)
        {
            var cached = JsonSerializer.Deserialize<WorkspaceDevelopmentsDto>(snapshot.PayloadJson);
            if (cached is not null && cached.Events.Count > 0)
                return cached with { FromCache = true };
        }

        var body = BuildResolveBody(ws);
        var events = await ResolvePythonAsync("/workspace/resolve-events", body, ct);
        var parsed = ParseEvents(events, 50);
        var dto = parsed.Count > 0
            ? new WorkspaceDevelopmentsDto(
            ws.Slug,
            EventCount:   IntProp(events,   "total"),
            MaxRiskScore: MaxDouble(events,  "events", "risk_score"),
            Events:       parsed,
            GeneratedAt:  DateTime.UtcNow,
            FromCache:    false)
            : await BuildDevelopmentsFromDbAsync(ws, ct);

        if (dto.Events.Count > 0)
            await SaveSnapshotAsync(ws.Id, "developments", ws.DefaultWindowHours, dto, ct);

        return dto;
    }

    public async Task<WorkspaceEntitiesDto?> GetEntitiesAsync(string slug, CancellationToken ct = default)
    {
        var ws = await _repo.GetBySlugAsync(slug, ct);
        if (ws is null) return null;

        var snapshot = await _repo.GetSnapshotAsync(ws.Id, "entities", ws.DefaultWindowHours, ct);
        if (snapshot is not null)
        {
            var cached = JsonSerializer.Deserialize<WorkspaceEntitiesDto>(snapshot.PayloadJson);
            if (cached is not null)
                return cached with { FromCache = true };
        }

        var body = BuildResolveBody(ws);
        var entities = await ResolvePythonAsync("/workspace/resolve-entities", body, ct);
        var dto = new WorkspaceEntitiesDto(
            ws.Slug,
            EntityCount: IntProp(entities, "total"),
            EntityItems: ParseEntities(entities),
            GeneratedAt: DateTime.UtcNow,
            FromCache:   false);
        await SaveSnapshotAsync(ws.Id, "entities", ws.DefaultWindowHours, dto, ct);
        return dto;
    }

    public async Task<WorkspaceAssetsDto?> GetAssetsAsync(string slug, CancellationToken ct = default)
    {
        var ws = await _repo.GetBySlugAsync(slug, ct);
        if (ws is null) return null;

        var snapshot = await _repo.GetSnapshotAsync(ws.Id, "assets", ws.DefaultWindowHours, ct);
        if (snapshot is not null)
        {
            var cached = JsonSerializer.Deserialize<WorkspaceAssetsDto>(snapshot.PayloadJson);
            return cached is null ? null : cached with { FromCache = true };
        }

        var body = BuildResolveBody(ws);
        var assets = await ResolvePythonAsync("/workspace/resolve-assets", body, ct);
        var dto = new WorkspaceAssetsDto(
            ws.Slug,
            TotalAssets:  IntProp(assets,   "total"),
            VesselCount:  IntNested(assets,  "counts", "vessel"),
            FlightCount:  IntNested(assets,  "counts", "aircraft"),
            AnomalyCount: ArrayLen(assets,   "anomalies"),
            AssetItems:   ParseAssets(assets, 750),
            GeneratedAt:  DateTime.UtcNow,
            FromCache:    false);
        await SaveSnapshotAsync(ws.Id, "assets", ws.DefaultWindowHours, dto, ct);
        return dto;
    }

    public async Task<WorkspaceSentimentDto?> GetSentimentAsync(string slug, CancellationToken ct = default)
    {
        var ws = await _repo.GetBySlugAsync(slug, ct);
        if (ws is null) return null;

        var snapshot = await _repo.GetSnapshotAsync(ws.Id, "sentiment", ws.DefaultWindowHours, ct);
        if (snapshot is not null)
        {
            var cached = JsonSerializer.Deserialize<WorkspaceSentimentDto>(snapshot.PayloadJson);
            return cached is null ? null : cached with { FromCache = true };
        }

        var body = BuildResolveBody(ws);
        var sentiment = await ResolvePythonAsync("/workspace/resolve-sentiment", body, ct);
        var dto = new WorkspaceSentimentDto(
            ws.Slug,
            CombinedSentimentScore: AvgSentiment(sentiment),
            SocialEventCount:       ArrayLen(sentiment, "combined"),
            Reddit:                 ParseSentimentTimeline(sentiment, "reddit"),
            Youtube:                ParseSentimentTimeline(sentiment, "youtube"),
            Combined:               ParseSentimentTimeline(sentiment, "combined"),
            RedditItems:            ParseSocialItems(sentiment, "reddit_items"),
            YoutubeItems:           ParseSocialItems(sentiment, "youtube_items"),
            SocialItems:            ParseSocialItems(sentiment, "social_items"),
            GeneratedAt:            DateTime.UtcNow,
            FromCache:              false);
        await SaveSnapshotAsync(ws.Id, "sentiment", ws.DefaultWindowHours, dto, ct);
        return dto;
    }

    public async Task<WorkspaceCorrelationDto?> GetCorrelationAsync(string slug, CancellationToken ct = default)
    {
        var ws = await _repo.GetBySlugAsync(slug, ct);
        if (ws is null) return null;

        var snapshot = await _repo.GetSnapshotAsync(ws.Id, "correlation", ws.DefaultWindowHours, ct);
        if (snapshot is not null)
        {
            var cached = JsonSerializer.Deserialize<WorkspaceCorrelationDto>(snapshot.PayloadJson);
            if (cached is not null)
                return cached with { FromCache = true };
        }

        var body = BuildResolveBody(ws);
        var correlation = await ResolvePythonAsync("/workspace/resolve-correlation", body, ct, CorrelationResolverBudget);
        var dto = new WorkspaceCorrelationDto(
            ws.Slug,
            NarrativeCount: ArrayLen(correlation, "narratives"),
            ClusterCount:   ArrayLen(correlation, "signal_clusters"),
            Narratives:     ParseNarratives(correlation),
            SignalClusters: ParseSignalClusters(correlation),
            Events:         ParseCorrelationEvents(correlation),
            GeneratedAt:    DateTime.UtcNow,
            FromCache:      false);
        await SaveSnapshotAsync(ws.Id, "correlation", ws.DefaultWindowHours, dto, ct);
        return dto;
    }

    public async Task<WorkspaceActionsDto?> GetActionsAsync(string slug, CancellationToken ct = default)
    {
        var ws = await _repo.GetBySlugAsync(slug, ct);
        if (ws is null) return null;

        var snapshot = await _repo.GetSnapshotAsync(ws.Id, "actions", ws.DefaultWindowHours, ct);
        if (snapshot is not null)
        {
            var cached = JsonSerializer.Deserialize<WorkspaceActionsDto>(snapshot.PayloadJson);
            if (cached is not null)
                return cached with { FromCache = true };
        }

        var body = BuildResolveBody(ws);
        var eventsTask      = ResolvePythonAsync("/workspace/resolve-events",      body, ct);
        var correlationTask = ResolvePythonAsync("/workspace/resolve-correlation",  body, ct);
        var assetsTask      = ResolvePythonAsync("/workspace/resolve-assets",       body, ct);
        var sentimentTask   = ResolvePythonAsync("/workspace/resolve-sentiment",    body, ct);
        await Task.WhenAll(eventsTask, correlationTask, assetsTask, sentimentTask);
        var events      = eventsTask.Result;
        var correlation = correlationTask.Result;
        var assets      = assetsTask.Result;
        var sentiment   = sentimentTask.Result;

        var actionItems = BuildActions(events, correlation, assets, sentiment);
        var dto = new WorkspaceActionsDto(
            ws.Slug,
            ActionCount: actionItems.Count,
            ActionItems: actionItems,
            GeneratedAt: DateTime.UtcNow,
            FromCache:   false);
        await SaveSnapshotAsync(ws.Id, "actions", ws.DefaultWindowHours, dto, ct);
        return dto;
    }

    public async Task RefreshAsync(string slug, CancellationToken ct = default)
    {
        var ws = await _repo.GetBySlugAsync(slug, ct);
        if (ws is null) return;

        // Expire all cached snapshots so next request re-fetches from Python
        foreach (var type in _ttlMinutes.Keys)
        {
            var s = await _repo.GetSnapshotAsync(ws.Id, type, ws.DefaultWindowHours, ct);
            if (s is not null)
            {
                s.ExpiresAt = DateTime.UtcNow.AddSeconds(-1);
                await _repo.UpsertSnapshotAsync(s, ct);
            }
        }

        try
        {
            // Refresh should not just invalidate UI cache. Queue the same live +
            // text ingestion pipeline the scheduler uses so workspace evidence
            // can pick up fresh GDELT/RSS/social/transport rows on the next read.
            await _python.TriggerLiveAsync(ct);
        }
        catch (Exception ex)
        {
            _log.LogWarning(ex, "Workspace refresh cache expired, but ingest trigger failed for {Slug}", slug);
        }

        var activeQueries = ws.Queries
            .Where(q => q.IsActive && !string.IsNullOrWhiteSpace(q.Query))
            .OrderByDescending(q => q.Priority)
            .Select(q => q.Query.Trim())
            .Distinct(StringComparer.OrdinalIgnoreCase)
            .Take(8)
            .ToList();

        foreach (var query in activeQueries)
        {
            try
            {
                await _python.TriggerIngestAsync(
                    query,
                    limit: 40,
                    enrich: true,
                    sources: _workspaceIngestSources,
                    ct: ct);
            }
            catch (Exception ex)
            {
                _log.LogWarning(ex, "Workspace refresh could not queue targeted ingest for {Slug}: {Query}", slug, query);
            }
        }
    }

    private async Task<(JsonDocument?, JsonDocument?, JsonDocument?, JsonDocument?, JsonDocument?)>
        FetchAllParallelAsync(object body, CancellationToken ct)
    {
        var eventsTask     = ResolvePythonAsync("/workspace/resolve-events",      body, ct);
        var assetsTask     = ResolvePythonAsync("/workspace/resolve-assets",      body, ct);
        var entitiesTask   = ResolvePythonAsync("/workspace/resolve-entities",    body, ct);
        var sentimentTask  = ResolvePythonAsync("/workspace/resolve-sentiment",   body, ct);
        var correlationTask= ResolvePythonAsync("/workspace/resolve-correlation", body, ct);
        await Task.WhenAll(eventsTask, assetsTask, entitiesTask, sentimentTask, correlationTask);
        return (eventsTask.Result, assetsTask.Result, entitiesTask.Result,
                sentimentTask.Result, correlationTask.Result);
    }

    // Cap concurrent Python resolver calls. Python runs ONE uvicorn worker, so firing the
    // overview's 5 resolvers in parallel saturated it → each query slowed past the budget →
    // cancelled → empty tabs. Gate to 2 so each call runs near its isolated speed.
    private static readonly SemaphoreSlim _resolverGate = new(2, 2);

    private async Task<JsonDocument?> ResolvePythonAsync(string path, object body, CancellationToken ct, TimeSpan? budget = null)
    {
        var effectiveBudget = budget ?? PythonResolverBudget;
        await _resolverGate.WaitAsync(ct);
        try
        {
            // Budget starts AFTER acquiring the gate, so queue wait doesn't count against it.
            using var timeout = CancellationTokenSource.CreateLinkedTokenSource(ct);
            timeout.CancelAfter(effectiveBudget);
            return await _python.PostAsync(path, body, timeout.Token);
        }
        catch (OperationCanceledException) when (!ct.IsCancellationRequested)
        {
            _log.LogWarning("Workspace resolver {Path} exceeded the {TimeoutSeconds}s UI budget", path, effectiveBudget.TotalSeconds);
            return null;
        }
        catch (Exception ex) when (!ct.IsCancellationRequested)
        {
            _log.LogWarning(ex, "Workspace resolver {Path} failed; returning partial workspace snapshot", path);
            return null;
        }
        finally
        {
            _resolverGate.Release();
        }
    }

    private async Task<WorkspaceDevelopmentsDto> BuildDevelopmentsFromDbAsync(Workspace ws, CancellationToken ct)
    {
        var now = DateTime.UtcNow;
        var from = now.AddHours(-Math.Max(1, ws.DefaultWindowHours));
        var enabledSources = ws.SourceProfiles
            .Where(s => s.IsEnabled && !string.IsNullOrWhiteSpace(s.SourceName))
            .Select(s => s.SourceName.Trim().ToLowerInvariant())
            .ToList();
        var queryTerms = ws.Queries
            .Where(q => q.IsActive && !string.IsNullOrWhiteSpace(q.Query))
            .Select(q => q.Query.Trim().ToLowerInvariant())
            .ToList();
        var entityTerms = ws.Entities
            .Where(e => !string.IsNullOrWhiteSpace(e.DisplayName))
            .Select(e => e.DisplayName.Trim().ToLowerInvariant())
            .ToList();

        var records = await LoadRecentEventRowsAsync(from, ct);

        var matched = records
            .Where(e => SourceMatchesWorkspace(e.Source, enabledSources))
            .Where(e => MatchesWorkspaceTerms(e, queryTerms, entityTerms))
            .ToList();

        if (matched.Count == 0)
        {
            matched = records
                .Where(e => SourceMatchesWorkspace(e.Source, enabledSources))
                .ToList();
        }

        var filtered = BalanceDevelopmentRows(matched);

        return new WorkspaceDevelopmentsDto(
            ws.Slug,
            filtered.Count,
            filtered.Count == 0 ? null : filtered.Max(e => e.RiskScore ?? 0),
            filtered,
            DateTime.UtcNow,
            true);
    }

    private async Task<List<WorkspaceEventItem>> LoadRecentEventRowsAsync(DateTime from, CancellationToken ct)
    {
        var rows = new List<WorkspaceEventItem>();
        var conn = _db.Database.GetDbConnection();
        if (conn.State != ConnectionState.Open)
            await conn.OpenAsync(ct);

        await using var cmd = conn.CreateCommand();
        cmd.CommandText = """
            select event_id, title, source, event_type, risk_score, timestamp, ingest_time, location_name
            from events
            where coalesce(timestamp, ingest_time) >= @from
            order by coalesce(timestamp, ingest_time) desc
            limit 8000
            """;
        var p = cmd.CreateParameter();
        p.ParameterName = "@from";
        p.Value = from;
        cmd.Parameters.Add(p);

        await using var reader = await cmd.ExecuteReaderAsync(ct);
        while (await reader.ReadAsync(ct))
        {
            var eventId = reader.IsDBNull(0) ? "" : reader.GetString(0);
            var title = reader.IsDBNull(1) ? "" : reader.GetString(1);
            var source = reader.IsDBNull(2) ? "" : reader.GetString(2);
            var eventType = reader.IsDBNull(3) ? null : reader.GetString(3);
            double? risk = reader.IsDBNull(4) ? null : reader.GetDouble(4);
            DateTime? timestamp = reader.IsDBNull(5)
                ? reader.IsDBNull(6) ? null : reader.GetDateTime(6)
                : reader.GetDateTime(5);
            var region = reader.IsDBNull(7) ? null : reader.GetString(7);

            rows.Add(new WorkspaceEventItem(
                eventId,
                title,
                source,
                eventType,
                risk,
                timestamp?.ToUniversalTime().ToString("O"),
                region));
        }

        return rows;
    }

    private static List<WorkspaceEventItem> BalanceDevelopmentRows(List<WorkspaceEventItem> rows)
    {
        if (rows.Count == 0)
            return [];

        var balanced = rows
            .GroupBy(e => SourceFamily(e.Source))
            .OrderBy(g => DevelopmentFamilyRank(g.Key))
            .ThenByDescending(g => g.Count())
            .SelectMany(g => g
                .OrderByDescending(e => e.RiskScore ?? 0)
                .ThenByDescending(e => ParseTimestamp(e.Timestamp))
                .Take(8))
            .OrderByDescending(e => e.RiskScore ?? 0)
            .ThenByDescending(e => ParseTimestamp(e.Timestamp))
            .Take(50)
            .ToList();

        return balanced.Count > 0 ? balanced : rows.Take(50).ToList();
    }

    private static int DevelopmentFamilyRank(string family) => family switch
    {
        "gdelt" => 0,
        "rss" => 1,
        "news" => 2,
        "reddit" => 3,
        "youtube" => 4,
        "usgs" => 5,
        "stocks" => 6,
        "opensky" => 7,
        "ais" => 8,
        _ => 9,
    };

    private static DateTime ParseTimestamp(string? timestamp)
        => DateTime.TryParse(timestamp, out var parsed) ? parsed.ToUniversalTime() : DateTime.MinValue;

    private static bool SourceMatchesWorkspace(string? source, List<string> enabledSources)
    {
        if (enabledSources.Count == 0 || string.IsNullOrWhiteSpace(source))
            return true;

        var src = source.Trim().ToLowerInvariant();
        return enabledSources.Any(enabled =>
            src == enabled ||
            src.StartsWith(enabled + "_", StringComparison.OrdinalIgnoreCase) ||
            SourceFamily(src) == enabled ||
            enabled == SourceFamily(src));
    }

    private static bool MatchesWorkspaceTerms(WorkspaceEventItem e, List<string> queryTerms, List<string> entityTerms)
    {
        if (queryTerms.Count == 0 && entityTerms.Count == 0)
            return true;

        var haystack = $"{e.Title} {e.Region} {e.EventType} {e.Source}".ToLowerInvariant();
        return queryTerms.Any(term => TermMatches(haystack, term)) ||
               entityTerms.Any(term => TermMatches(haystack, term));
    }

    private static bool TermMatches(string haystack, string term)
    {
        if (string.IsNullOrWhiteSpace(term))
            return false;

        if (haystack.Contains(term, StringComparison.OrdinalIgnoreCase))
            return true;

        return term
            .Split(' ', StringSplitOptions.RemoveEmptyEntries | StringSplitOptions.TrimEntries)
            .Where(part => part.Length >= 3)
            .Any(part => haystack.Contains(part, StringComparison.OrdinalIgnoreCase));
    }

    private static string SourceFamily(string source)
    {
        var s = source.ToLowerInvariant();
        if (s.StartsWith("rss_")) return "rss";
        if (s.StartsWith("gdelt")) return "gdelt";
        if (s.StartsWith("newsapi")) return "news";
        if (s.StartsWith("reddit")) return "reddit";
        if (s.StartsWith("youtube")) return "youtube";
        if (s.StartsWith("opensky")) return "opensky";
        if (s.StartsWith("ais")) return "ais";
        if (s.StartsWith("yahoo")) return "stocks";
        return s;
    }

    private static object BuildResolveBody(Workspace ws)
    {
        var primaryGeo = ws.GeoFilters.FirstOrDefault(f => f.Name == "primary") ?? ws.GeoFilters.FirstOrDefault();
        return new
        {
            queries = ws.Queries.Where(q => q.IsActive).Select(q => q.Query).ToList(),
            sources = ws.SourceProfiles.Where(s => s.IsEnabled).Select(s => s.SourceName).ToList(),
            window_hours = ws.DefaultWindowHours,
            geo_filter = primaryGeo is null ? null : new
            {
                min_lat = primaryGeo.MinLat,
                max_lat = primaryGeo.MaxLat,
                min_lon = primaryGeo.MinLon,
                max_lon = primaryGeo.MaxLon,
            },
            entity_seeds = ws.Entities.Select(e => e.DisplayName).ToList(),
        };
    }

    // ── Typed parse helpers ──────────────────────────────────────────────────

    private static List<WorkspaceEventItem> ParseEvents(JsonDocument? doc, int limit)
    {
        var result = new List<WorkspaceEventItem>();
        if (doc is null) return result;
        if (!doc.RootElement.TryGetProperty("events", out var arr) || arr.ValueKind != JsonValueKind.Array)
            return result;
        foreach (var e in arr.EnumerateArray().Take(limit))
        {
            result.Add(new WorkspaceEventItem(
                EventId:   JStr(e, "event_id"),
                Title:     JStr(e, "title"),
                Source:    JStr(e, "source"),
                EventType: JStrN(e, "event_type"),
                RiskScore: JDoubleN(e, "risk_score"),
                Timestamp: JStrN(e, "timestamp"),
                Region:    JStrN(e, "region")
            ));
        }
        return result;
    }

    private static List<string> BuildSummaryBullets(
        List<WorkspaceEventItem> events, List<WorkspaceEntityItem> entities,
        List<WorkspaceNarrativeLink> narratives, int anomalyCount, double? sentimentScore)
    {
        var bullets = new List<string>();
        var highRiskCount = events.Count(e => (e.RiskScore ?? 0) >= 0.7);
        var maxRisk       = events.Count > 0 ? events.Max(e => e.RiskScore ?? 0) : 0;

        if (highRiskCount > 0)
            bullets.Add($"{events.Count} event{(events.Count == 1 ? "" : "s")} tracked — {highRiskCount} high-risk (≥0.70), peak score {maxRisk:F2}. Immediate triage recommended.");
        else if (events.Count > 0)
            bullets.Add($"{events.Count} event{(events.Count == 1 ? "" : "s")} in window, max risk {maxRisk:F2} — situation nominal.");

        if (anomalyCount > 0)
            bullets.Add($"{anomalyCount} asset anomal{(anomalyCount == 1 ? "y" : "ies")} detected in AOI — cross-reference with active events.");
        else
            bullets.Add("No asset anomalies detected in current time window.");

        if (sentimentScore < 0.4 && sentimentScore > 0)
            bullets.Add($"Social sentiment negative ({sentimentScore:P0}) — narrative tension elevated.");
        else if (sentimentScore > 0.6)
            bullets.Add($"Social sentiment positive ({sentimentScore:P0}) — no significant narrative shifts.");

        if (entities.Count > 0)
        {
            var top2 = entities.Take(2).Select(e => e.Name).ToList();
            bullets.Add($"Top entities: {string.Join(", ", top2)} ({entities.Count} total tracked).");
        }

        if (narratives.Count > 0)
        {
            var n = narratives[0];
            bullets.Add($"Key narrative link: {n.Source} → {n.Target} ({narratives.Count} signal{(narratives.Count == 1 ? "" : "s")} active).");
        }

        return bullets;
    }

    private static List<WorkspaceMapEventItem> ParseGeoEvents(JsonDocument? doc, int limit)
    {
        var result = new List<WorkspaceMapEventItem>();
        if (doc is null) return result;
        if (!doc.RootElement.TryGetProperty("events", out var arr) || arr.ValueKind != JsonValueKind.Array)
            return result;
        foreach (var e in arr.EnumerateArray().Take(limit))
        {
            // Try top-level lat/lon, then location nested object
            double? lat = null, lon = null;
            lat = JDoubleN(e, "lat");
            lon = JDoubleN(e, "lon");
            if (lat is null && e.TryGetProperty("location", out var loc) && loc.ValueKind == JsonValueKind.Object)
            {
                lat = JDoubleN(loc, "lat");
                lon = JDoubleN(loc, "lon");
            }
            if (lat is null || lon is null) continue;
            result.Add(new WorkspaceMapEventItem(
                EventId:   JStr(e, "event_id"),
                Title:     JStr(e, "title"),
                Lat:       lat.Value,
                Lon:       lon.Value,
                RiskScore: JDoubleN(e, "risk_score"),
                EventType: JStrN(e, "event_type"),
                Timestamp: JStrN(e, "timestamp")
            ));
        }
        return result;
    }

    private static List<WorkspaceAssetItem> ParseAssets(JsonDocument? doc, int limit)
    {
        var result = new List<WorkspaceAssetItem>();
        if (doc is null) return result;
        if (!doc.RootElement.TryGetProperty("assets", out var arr) || arr.ValueKind != JsonValueKind.Array)
            return result;
        foreach (var a in arr.EnumerateArray().Take(limit))
        {
            var spd = JDoubleN(a, "last_speed");
            var og  = a.TryGetProperty("on_ground",  out var og2) && og2.ValueKind == JsonValueKind.False ? false :
                      a.TryGetProperty("on_ground",  out var og3) && og3.ValueKind == JsonValueKind.True  ? true  : (bool?)null;
            result.Add(new WorkspaceAssetItem(
                AssetId:   JStr(a, "asset_id"),
                AssetType: JStr(a, "asset_type"),
                Name:      JStrN(a, "name"),
                Callsign:  JStrN(a, "callsign"),
                OriginCountry: JStrN(a, "origin_country"),
                LastLat:   JDoubleN(a, "last_lat"),
                LastLon:   JDoubleN(a, "last_lon"),
                LastAltitude: JDoubleN(a, "last_altitude"),
                LastSpeed: spd,
                LastHeading: JDoubleN(a, "last_heading"),
                OnGround:  og,
                LastSeen:  JStrN(a, "last_seen"),
                IsAnomaly: JBool(a, "is_anomaly") ?? false,
                WithinAoi: JBool(a, "within_aoi") ?? true
            ));
        }
        return result;
    }

    private static List<WorkspaceSentimentPoint> ParseSentimentTimeline(JsonDocument? doc, string key)
    {
        var result = new List<WorkspaceSentimentPoint>();
        if (doc is null) return result;
        if (!doc.RootElement.TryGetProperty(key, out var arr) || arr.ValueKind != JsonValueKind.Array)
            return result;
        foreach (var p in arr.EnumerateArray())
        {
            var avg = JDoubleN(p, "avg_score") ?? 0.5;
            result.Add(new WorkspaceSentimentPoint(
                Bucket:     JStr(p, "bucket"),
                AvgScore:   avg,
                EventCount: JInt(p, "event_count"),
                Positive:   JInt(p, "positive"),
                Neutral:    JInt(p, "neutral"),
                Negative:   JInt(p, "negative")
            ));
        }
        return result;
    }

    private static List<WorkspaceSocialItem> ParseSocialItems(JsonDocument? doc, string key)
    {
        var result = new List<WorkspaceSocialItem>();
        if (doc is null) return result;
        if (!doc.RootElement.TryGetProperty(key, out var arr) || arr.ValueKind != JsonValueKind.Array)
            return result;

        foreach (var item in arr.EnumerateArray().Take(40))
        {
            result.Add(new WorkspaceSocialItem(
                EventId:        JStr(item, "event_id"),
                Source:         JStr(item, "source"),
                Title:          JStr(item, "title", "Untitled social item"),
                Url:            JStrN(item, "url"),
                Author:         JStrN(item, "author"),
                Timestamp:      JStrN(item, "timestamp"),
                SentimentScore: JDoubleN(item, "sentiment_score"),
                SentimentLabel: JStrN(item, "sentiment_label"),
                Description:    JStrN(item, "description")
            ));
        }

        return result;
    }

    private static List<WorkspaceEntityItem> ParseEntities(JsonDocument? doc)
    {
        var result = new List<WorkspaceEntityItem>();
        if (doc is null) return result;
        if (!doc.RootElement.TryGetProperty("entities", out var arr) || arr.ValueKind != JsonValueKind.Array)
            return result;
        foreach (var e in arr.EnumerateArray().Take(50))
        {
            result.Add(new WorkspaceEntityItem(
                Name:       JStr(e, "name"),
                EntityType: JStrN(e, "type"),
                Count:      JInt(e, "count"),
                EventCount: JInt(e, "event_count")
            ));
        }
        return result;
    }

    private static List<WorkspaceNarrativeLink> ParseNarratives(JsonDocument? doc)
    {
        var result = new List<WorkspaceNarrativeLink>();
        if (doc is null) return result;
        if (!doc.RootElement.TryGetProperty("narratives", out var arr) || arr.ValueKind != JsonValueKind.Array)
            return result;
        foreach (var n in arr.EnumerateArray().Take(30))
        {
            var src = JStr(n, "source"); var tgt = JStr(n, "target");
            if (!string.IsNullOrEmpty(src) && !string.IsNullOrEmpty(tgt))
                result.Add(new WorkspaceNarrativeLink(src, JStr(n, "rel", "RELATED_TO"), tgt));
        }
        return result;
    }

    private static List<WorkspaceCorrelationEvent> ParseCorrelationEvents(JsonDocument? doc)
    {
        var result = new List<WorkspaceCorrelationEvent>();
        if (doc is null) return result;
        if (!doc.RootElement.TryGetProperty("events", out var arr) || arr.ValueKind != JsonValueKind.Array)
            return result;
        foreach (var e in arr.EnumerateArray().Take(60))
        {
            var id = JStr(e, "id");
            if (string.IsNullOrEmpty(id)) continue;
            result.Add(new WorkspaceCorrelationEvent(
                id,
                JStr(e, "title", id),
                JStr(e, "source"),
                JStr(e, "event_type"),
                JStr(e, "timestamp"),
                JStr(e, "actor")
            ));
        }
        return result;
    }

    private static List<WorkspaceSignalCluster> ParseSignalClusters(JsonDocument? doc)
    {
        var result = new List<WorkspaceSignalCluster>();
        if (doc is null) return result;
        if (!doc.RootElement.TryGetProperty("signal_clusters", out var arr) || arr.ValueKind != JsonValueKind.Array)
            return result;
        foreach (var c in arr.EnumerateArray().Take(20))
        {
            var tag = JStr(c, "tag");
            if (!string.IsNullOrEmpty(tag))
                result.Add(new WorkspaceSignalCluster(
                    tag,
                    JInt(c, "count")
                ));
        }
        return result;
    }

    private static List<WorkspaceAction> BuildActions(
        JsonDocument? events, JsonDocument? correlation,
        JsonDocument? assets, JsonDocument? sentiment)
    {
        var actions = new List<WorkspaceAction>();

        // ── 1. High-risk events → triage (ranked by risk, capped at top-3 evidence) ──
        if (events?.RootElement.TryGetProperty("events", out var evArr) == true &&
            evArr.ValueKind == JsonValueKind.Array)
        {
            var all = evArr.EnumerateArray().ToList();
            var highRisk = all
                .Where(e => (JDoubleN(e, "risk_score") ?? 0) >= 0.6)
                .OrderByDescending(e => JDoubleN(e, "risk_score") ?? 0)
                .Take(3).ToList();

            if (highRisk.Count > 0)
            {
                var top     = highRisk[0];
                var topRisk = JDoubleN(top, "risk_score") ?? 0.6;
                var topSrc  = JStrN(top, "source") ?? JStrN(top, "region") ?? "unknown source";
                var topType = JStrN(top, "event_type");
                var typeStr = topType is null ? "" : $" ({topType})";
                actions.Add(new WorkspaceAction(
                    Title:            $"Immediate triage: {JStr(top, "title", "High-risk event")}",
                    Rationale:        $"Risk score {topRisk:F2}{typeStr} from {topSrc}. " +
                                      $"{highRisk.Count} event{(highRisk.Count > 1 ? "s" : "")} exceed threshold 0.60 in window. " +
                                      "Escalate or dismiss before window expires.",
                    Priority:         topRisk >= 0.8 ? "high" : "medium",
                    Confidence:       topRisk,
                    ActionType:       "triage",
                    EvidenceEventIds: highRisk.Select(e => JStr(e, "event_id")).Where(id => !string.IsNullOrEmpty(id)).ToList(),
                    RelatedEntity:    topSrc
                ));
            }

            // ── 2. Medium-risk cluster investigation ──
            var medium = all
                .Where(e => (JDoubleN(e, "risk_score") ?? 0) >= 0.35 && (JDoubleN(e, "risk_score") ?? 0) < 0.6)
                .OrderByDescending(e => JDoubleN(e, "risk_score") ?? 0)
                .Take(5).ToList();
            if (medium.Count > 0)
            {
                var sources = medium
                    .Select(e => JStrN(e, "source"))
                    .Where(s => !string.IsNullOrEmpty(s))
                    .Distinct()
                    .Take(3)
                    .ToList();
                var sourceStr = sources.Count > 0 ? $" Sources: {string.Join(", ", sources)}." : "";
                actions.Add(new WorkspaceAction(
                    Title:            $"Review {medium.Count} medium-risk development{(medium.Count > 1 ? "s" : "")}",
                    Rationale:        $"{medium.Count} event{(medium.Count > 1 ? "s" : "")} in risk band 0.35–0.60 require analyst review to determine escalation path.{sourceStr}",
                    Priority:         "medium",
                    Confidence:       0.55 + (medium.Count > 3 ? 0.1 : 0),
                    ActionType:       "investigate",
                    EvidenceEventIds: medium.Select(e => JStr(e, "event_id")).Where(id => !string.IsNullOrEmpty(id)).ToList(),
                    RelatedEntity:    null
                ));
            }
        }

        // ── 3. Anomalous assets → verify movement ──
        if (assets?.RootElement.TryGetProperty("assets", out var assetArr) == true &&
            assetArr.ValueKind == JsonValueKind.Array)
        {
            var anomalies = assetArr.EnumerateArray()
                .Where(a =>
                {
                    var spd = JDoubleN(a, "last_speed") ?? 0;
                    var og  = a.TryGetProperty("on_ground",  out var og2) ? og2.ValueKind : JsonValueKind.Null;
                    return spd > 20 || og == JsonValueKind.False;
                })
                .Take(5).ToList();

            if (anomalies.Count > 0)
            {
                var vessels  = anomalies.Count(a => JStr(a, "asset_type") == "vessel");
                var aircraft = anomalies.Count(a => JStr(a, "asset_type") == "aircraft");
                var breakdown = new List<string>();
                if (vessels  > 0) breakdown.Add($"{vessels} vessel{(vessels > 1  ? "s" : "")}");
                if (aircraft > 0) breakdown.Add($"{aircraft} aircraft");
                actions.Add(new WorkspaceAction(
                    Title:            $"Verify {anomalies.Count} anomalous asset{(anomalies.Count > 1 ? "s" : "")} in AOI",
                    Rationale:        $"{string.Join(", ", breakdown)} showing unusual speed or airborne status in the area of interest. Cross-reference with active events to confirm or dismiss.",
                    Priority:         "medium",
                    Confidence:       0.72,
                    ActionType:       "verify",
                    EvidenceEventIds: [],
                    RelatedEntity:    null
                ));
            }
        }

        // ── 4. Correlation actor link ──
        if (correlation?.RootElement.TryGetProperty("narratives", out var narrArr) == true &&
            narrArr.ValueKind == JsonValueKind.Array && narrArr.GetArrayLength() > 0)
        {
            var n   = narrArr.EnumerateArray().First();
            var src = JStr(n, "source"); var rel = JStr(n, "rel", "RELATED_TO"); var tgt = JStr(n, "target");
            if (!string.IsNullOrEmpty(src) && !string.IsNullOrEmpty(tgt))
            {
                var totalNarr = narrArr.GetArrayLength();
                actions.Add(new WorkspaceAction(
                    Title:            $"Investigate actor link: {src} → {tgt}",
                    Rationale:        $"Correlation graph ({totalNarr} narrative link{(totalNarr > 1 ? "s" : "")}) detected '{rel}' relationship. " +
                                      "Review actor network — co-occurrence may indicate coordination or shared source bias.",
                    Priority:         "medium",
                    Confidence:       0.7,
                    ActionType:       "investigate",
                    EvidenceEventIds: [],
                    RelatedEntity:    src
                ));
            }
        }

        // ── 5. Negative social shift ──
        double negShift = 0;
        if (sentiment?.RootElement.TryGetProperty("combined", out var combArr) == true &&
            combArr.ValueKind == JsonValueKind.Array)
        {
            var pts = combArr.EnumerateArray().ToList();
            if (pts.Count >= 2)
            {
                var recent = pts.TakeLast(2).ToList();
                var prev   = JDoubleN(recent[0], "avg_score") ?? 0.5;
                var curr   = JDoubleN(recent[1], "avg_score") ?? 0.5;
                negShift = prev - curr; // positive = sentiment fell
            }
        }
        if (negShift >= 0.08)
        {
            actions.Add(new WorkspaceAction(
                Title:            $"Monitor social sentiment decline ({negShift:F2} drop)",
                Rationale:        $"Combined sentiment fell {negShift:F2} points in the latest period. Social narrative shifts of this magnitude often precede on-ground escalation. Monitor Reddit/YouTube timelines closely.",
                Priority:         negShift >= 0.15 ? "medium" : "low",
                Confidence:       0.65,
                ActionType:       "monitor",
                EvidenceEventIds: [],
                RelatedEntity:    null
            ));
        }
        else
        {
            actions.Add(new WorkspaceAction(
                Title:            "Monitor social sentiment timeline",
                Rationale:        "Social narrative shifts often precede on-ground escalation. Track Reddit/YouTube timelines in the Social tab for early signals.",
                Priority:         "low",
                Confidence:       0.55,
                ActionType:       "monitor",
                EvidenceEventIds: [],
                RelatedEntity:    null
            ));
        }

        // Sort: high → medium → low, then by confidence desc
        return actions
            .OrderByDescending(a => a.Priority switch { "high" => 3, "medium" => 2, _ => 1 })
            .ThenByDescending(a => a.Confidence)
            .ToList();
    }

    private static bool HasUsefulOverview(WorkspaceOverviewDto overview)
        => overview.EventCount > 0
           || overview.TopEvents.Count > 0
           || !string.IsNullOrWhiteSpace(overview.TopActor)
           || overview.NarrativeCount > 0
           || overview.SummaryBullets.Count > 1;

    private static string JStr(JsonElement el, string prop, string fallback = "")
        => el.ValueKind == JsonValueKind.Object &&
           el.TryGetProperty(prop, out var v) &&
           v.ValueKind == JsonValueKind.String
            ? v.GetString() ?? fallback
            : fallback;

    private static string? JStrN(JsonElement el, string prop)
        => el.ValueKind == JsonValueKind.Object &&
           el.TryGetProperty(prop, out var v) &&
           v.ValueKind == JsonValueKind.String
            ? v.GetString()
            : null;

    private static int JInt(JsonElement el, string prop)
        => el.ValueKind == JsonValueKind.Object &&
           el.TryGetProperty(prop, out var v) &&
           v.ValueKind == JsonValueKind.Number &&
           v.TryGetInt32(out var parsed)
            ? parsed
            : 0;

    private static double? JDoubleN(JsonElement el, string prop)
        => el.ValueKind == JsonValueKind.Object &&
           el.TryGetProperty(prop, out var v) &&
           v.ValueKind == JsonValueKind.Number &&
           v.TryGetDouble(out var parsed)
            ? parsed
            : null;

    // ── JSON metric helpers ──────────────────────────────────────────────────

    private static bool? JBool(JsonElement el, string prop)
        => el.ValueKind == JsonValueKind.Object &&
           el.TryGetProperty(prop, out var v)
            ? v.ValueKind switch
            {
                JsonValueKind.True => true,
                JsonValueKind.False => false,
                _ => null
            }
            : null;

    private static int IntProp(JsonDocument? doc, string key)
    {
        if (doc is null) return 0;
        return doc.RootElement.TryGetProperty(key, out var el) &&
               el.ValueKind == JsonValueKind.Number &&
               el.TryGetInt32(out var v) ? v : 0;
    }

    private static int IntNested(JsonDocument? doc, string obj, string key)
    {
        if (doc is null) return 0;
        if (!doc.RootElement.TryGetProperty(obj, out var nested)) return 0;
        return nested.TryGetProperty(key, out var el) &&
               el.ValueKind == JsonValueKind.Number &&
               el.TryGetInt32(out var v) ? v : 0;
    }

    private static int ArrayLen(JsonDocument? doc, string key)
    {
        if (doc is null) return 0;
        return doc.RootElement.TryGetProperty(key, out var el) && el.ValueKind == JsonValueKind.Array
            ? el.GetArrayLength() : 0;
    }

    private static double? MaxDouble(JsonDocument? doc, string arrayKey, string prop)
    {
        if (doc is null) return null;
        if (!doc.RootElement.TryGetProperty(arrayKey, out var arr) || arr.ValueKind != JsonValueKind.Array)
            return null;
        double? max = null;
        foreach (var item in arr.EnumerateArray())
        {
            if (item.TryGetProperty(prop, out var v) &&
                v.ValueKind == JsonValueKind.Number &&
                v.TryGetDouble(out var d))
                max = max is null ? d : Math.Max(max.Value, d);
        }
        return max;
    }

    private static double? AvgSentiment(JsonDocument? doc)
    {
        if (doc is null) return null;
        if (!doc.RootElement.TryGetProperty("combined", out var combined) || combined.ValueKind != JsonValueKind.Array)
            return null;
        double sum = 0; int count = 0;
        foreach (var item in combined.EnumerateArray())
        {
            if (item.TryGetProperty("avg_score", out var v) &&
                v.ValueKind == JsonValueKind.Number &&
                v.TryGetDouble(out var d))
            { sum += d; count++; }
        }
        return count == 0 ? null : Math.Round(sum / count, 4);
    }

    private async Task SaveSnapshotAsync<T>(
        Guid workspaceId, string type, int windowHours, T dto, CancellationToken ct)
    {
        try
        {
            var ttl = _ttlMinutes.GetValueOrDefault(type, 5);
            var snapshot = new WorkspaceSnapshot
            {
                WorkspaceId = workspaceId,
                SnapshotType = type,
                WindowHours = windowHours,
                PayloadJson = JsonSerializer.Serialize(dto),
                GeneratedAt = DateTime.UtcNow,
                ExpiresAt = DateTime.UtcNow.AddMinutes(ttl),
            };
            await _repo.UpsertSnapshotAsync(snapshot, ct);
        }
        catch (Exception ex)
        {
            _log.LogWarning(ex, "Failed to save workspace snapshot {Type} for {WorkspaceId}", type, workspaceId);
        }
    }
}
