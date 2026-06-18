namespace VisionI.Web.Models;

// ── Typed item models (mirrors VisionI.API.Models) ───────────────────────────

public record WorkspaceEventItem(
    string EventId,
    string Title,
    string Source,
    string? EventType,
    double? RiskScore,
    string? Timestamp,
    string? Region
);

public record WorkspaceAssetItem(
    string AssetId,
    string AssetType,
    string? Name,
    string? Callsign,
    string? OriginCountry,
    double? LastLat,
    double? LastLon,
    double? LastAltitude,
    double? LastSpeed,
    double? LastHeading,
    bool? OnGround,
    string? LastSeen,
    bool IsAnomaly,
    bool WithinAoi
);

public record WorkspaceSentimentPoint(
    string Bucket,
    double AvgScore,
    int EventCount,
    int Positive,
    int Neutral,
    int Negative
);

public record WorkspaceSocialItem(
    string EventId,
    string Source,
    string Title,
    string? Url,
    string? Author,
    string? Timestamp,
    double? SentimentScore,
    string? SentimentLabel,
    string? Description
);

public record WorkspaceAction(
    string Title,
    string Rationale,
    string Priority,
    double Confidence,
    string ActionType,
    List<string> EvidenceEventIds,
    string? RelatedEntity
);

public record WorkspaceEntityItem(string Name, string? EntityType, int Count, int EventCount);
public record WorkspaceNarrativeLink(string Source, string Rel, string Target);
public record WorkspaceSignalCluster(string Tag, int Count);
public record WorkspaceCorrelationEvent(
    string Id,
    string Title,
    string? Source,
    string? EventType,
    string? Timestamp,
    string? Actor
);

public record WorkspaceMapEventItem(
    string EventId,
    string Title,
    double Lat,
    double Lon,
    double? RiskScore,
    string? EventType,
    string? Timestamp
);

public record WorkspaceListItem(
    Guid Id,
    string Slug,
    string Title,
    string? Description,
    string Status,
    string? Classification,
    int DefaultWindowHours,
    DateTime UpdatedAt
);

public record WorkspaceDetailModel(
    Guid Id,
    string Slug,
    string Title,
    string? Description,
    string Status,
    string? Classification,
    int DefaultWindowHours,
    DateTime CreatedAt,
    DateTime UpdatedAt,
    List<WorkspaceGeoFilterItem> GeoFilters,
    List<WorkspaceQueryItem> Queries,
    List<WorkspaceEntityRef> Entities,
    List<WorkspaceSourceItem> SourceProfiles
);

public record WorkspaceGeoFilterItem(
    Guid Id,
    string FilterType,
    string Name,
    double? MinLat,
    double? MaxLat,
    double? MinLon,
    double? MaxLon
);

public record WorkspaceQueryItem(Guid Id, string Query, int Priority, bool IsActive);
public record WorkspaceEntityRef(Guid Id, string EntityKey, string? EntityType, string DisplayName, bool IsPrimary);
public record WorkspaceSourceItem(Guid Id, string SourceName, bool IsEnabled);

public record WorkspaceOverview(
    string Slug,
    string Title,
    int EventCount,
    double? MaxRiskScore,
    int AssetCount,
    int VesselCount,
    int FlightCount,
    double? SentimentScore,
    int NarrativeCount,
    List<WorkspaceEventItem> TopEvents,
    List<string>? SummaryBullets,
    string? TopActor,
    string? TopNarrative,
    int AnomalyDelta,
    DateTime GeneratedAt,
    bool FromCache
);

public record WorkspaceMap(
    string Slug,
    int AssetCount,
    int EventCount,
    WorkspaceGeoFilterItem? PrimaryGeoFilter,
    List<WorkspaceAssetItem> AssetItems,
    List<WorkspaceMapEventItem> EventItems,
    DateTime GeneratedAt,
    bool FromCache
);

public record WorkspaceDevelopments(
    string Slug,
    int EventCount,
    double? MaxRiskScore,
    List<WorkspaceEventItem> Events,
    DateTime GeneratedAt,
    bool FromCache
);

public record WorkspaceEntities(
    string Slug,
    int EntityCount,
    List<WorkspaceEntityItem> EntityItems,
    DateTime GeneratedAt,
    bool FromCache
);

public record WorkspaceAssets(
    string Slug,
    int TotalAssets,
    int VesselCount,
    int FlightCount,
    int AnomalyCount,
    List<WorkspaceAssetItem> AssetItems,
    DateTime GeneratedAt,
    bool FromCache
);

public record WorkspaceSentiment(
    string Slug,
    double? CombinedSentimentScore,
    int SocialEventCount,
    List<WorkspaceSentimentPoint> Reddit,
    List<WorkspaceSentimentPoint> Youtube,
    List<WorkspaceSentimentPoint> Combined,
    List<WorkspaceSocialItem>? RedditItems,
    List<WorkspaceSocialItem>? YoutubeItems,
    List<WorkspaceSocialItem>? SocialItems,
    DateTime GeneratedAt,
    bool FromCache
);

public record WorkspaceCorrelation(
    string Slug,
    int NarrativeCount,
    int ClusterCount,
    List<WorkspaceNarrativeLink> Narratives,
    List<WorkspaceSignalCluster> SignalClusters,
    List<WorkspaceCorrelationEvent> Events,
    DateTime GeneratedAt,
    bool FromCache
);

public record WorkspaceActions(
    string Slug,
    int ActionCount,
    List<WorkspaceAction> ActionItems,
    DateTime GeneratedAt,
    bool FromCache
);

// ── Creation request records (mirrors VisionI.API.Models) ───────────────────

public record CreateWorkspaceRequest(
    string Slug,
    string Title,
    string? Description,
    string? Classification,
    int DefaultWindowHours = 24,
    string Visibility = "private",       // private|team|public
    List<GeoFilterRequest>? GeoFilters = null,
    List<QueryRequest>? Queries = null,
    List<EntityRequest>? Entities = null,
    List<SourceProfileRequest>? SourceProfiles = null
);

public record GeoFilterRequest(
    string FilterType,
    string Name,
    double? MinLat,
    double? MaxLat,
    double? MinLon,
    double? MaxLon
);

public record QueryRequest(string Query, int Priority = 100, bool IsActive = true);

public record EntityRequest(
    string EntityKey,
    string DisplayName,
    string? EntityType = null,
    bool IsPrimary = false
);

public record SourceProfileRequest(string SourceName, bool IsEnabled = true);

public record WorkspaceDecisionRequest(
    string ActionTitle,
    string ActionType,
    string Outcome,
    string? EventId,
    string? Rationale,
    string? AnalystNote,
    double? Confidence,
    List<string>? EvidenceEventIds
);

public record WorkspaceDecisionResultDto(
    Guid ContextId,
    string WorkspaceSlug,
    string ActionTitle,
    string Outcome,
    DateTime CreatedAt
);
