using System.Text.Json;
using System.Text.Json.Serialization;

namespace VisionI.API.Models.Responses;

public record AuthResponse(
    string AccessToken,
    string TokenType,
    int ExpiresIn,     // seconds
    string Role,
    string UserId,
    string DisplayName,
    string Email
);

public record UserResponse(
    string UserId,
    string Email,
    string DisplayName,
    string Role,
    DateTime CreatedAt,
    bool IsActive
);

public record ApiError(
    string Code,
    string Message,
    string? Detail = null
);

public sealed class DashboardOverviewResponse
{
    [JsonPropertyName("is_admin_view")]
    public bool IsAdminView { get; set; }
    [JsonPropertyName("generated_at")]
    public string GeneratedAt { get; set; } = DateTime.UtcNow.ToString("O");
    [JsonPropertyName("situations")]
    public JsonElement Situations { get; set; }
    [JsonPropertyName("events")]
    public JsonElement Events { get; set; }
    [JsonPropertyName("live_events")]
    public JsonElement LiveEvents { get; set; }
    [JsonPropertyName("entities")]
    public JsonElement Entities { get; set; }
    [JsonPropertyName("stats")]
    public JsonElement Stats { get; set; }
    [JsonPropertyName("health")]
    public JsonElement Health { get; set; }
    [JsonPropertyName("alert_summary")]
    public JsonElement AlertSummary { get; set; }
    [JsonPropertyName("recent_alerts")]
    public JsonElement RecentAlerts { get; set; }
    [JsonPropertyName("narrative_summary")]
    public JsonElement NarrativeSummary { get; set; }
    [JsonPropertyName("sentiment_timeline")]
    public JsonElement SentimentTimeline { get; set; }
    [JsonPropertyName("jobs")]
    public JsonElement Jobs { get; set; }
    [JsonPropertyName("swarm")]
    public JsonElement Swarm { get; set; }
    [JsonPropertyName("confidence_distribution")]
    public JsonElement ConfidenceDistribution { get; set; }
    [JsonPropertyName("correlation_summary")]
    public JsonElement CorrelationSummary { get; set; }
    [JsonPropertyName("jarvis_insight")]
    public string? JarvisInsight { get; set; }
}

public sealed record TriageRecordResponse(
    int Id,
    string EventId,
    string Title,
    string Source,
    string EventType,
    string Status,
    string Priority,
    double? RiskScore,
    double? ConfidenceScore,
    string? AnalystUserId,
    string? AnalystDisplayName,
    string? Note,
    string? SourceUrl,
    string? Region,
    int SimilarEventCount,
    int RelatedActorCount,
    string LastSeenAt,
    string CreatedAt,
    string UpdatedAt
);

public sealed record TriageCandidateResponse(
    string EventId,
    string Title,
    string Source,
    string EventType,
    double? RiskScore,
    double? ConfidenceScore,
    string? Timestamp,
    string? SourceUrl,
    string? Region,
    string Status,
    string Priority,
    string? AnalystDisplayName,
    int SimilarEventCount,
    int RelatedActorCount
);

public sealed record TriageSummaryResponse(
    int Total,
    int New,
    int Reviewing,
    int Escalated,
    int Actioned,
    int Dismissed
);

public sealed record GraphNodeResponse(
    string Id,
    string Label,
    string Group,
    string? Type = null,
    double? Value = null,
    double? InfluenceScore = null,
    int? MentionCount = null
);

public sealed record GraphEdgeResponse(
    string From,
    string To,
    string Label,
    double? Weight = null,
    string? EvidenceMode = null
);

public sealed record InfluenceNetworkResponse(
    List<GraphNodeResponse> Nodes,
    List<GraphEdgeResponse> Edges,
    int NodeCount,
    int EdgeCount,
    string? ServedFrom = null
);

public sealed record SignalRecordResponse(
    string SignalId,
    string? SourceEventId,
    string Source,
    string SignalType,
    string Title,
    string? Body,
    string? Timestamp,
    List<string> Actors,
    string? LocationName,
    double? LocationLat,
    double? LocationLon,
    double? SentimentScore,
    double? Confidence,
    string? ClusterId,
    JsonElement Meta,
    double? Similarity = null,
    AnalystIndicatorResponse? Indicator = null
);

public sealed record SignalListResponse(
    int Total,
    List<SignalRecordResponse> Signals,
    string? Mode = null
);

public sealed record SignalClusterResponse(
    string ClusterId,
    int SignalCount,
    List<string> Sources,
    List<string> SharedActors,
    double CompositeScore,
    string? RepresentativeTitle,
    double TimeSpanHours,
    string? Earliest,
    string? Latest,
    AnalystIndicatorResponse? Indicator = null
);

public sealed record SignalClustersResponse(
    List<SignalClusterResponse> Clusters,
    int Total = 0,
    string? ServedFrom = null
);

public sealed record SignalCorrelationSummaryResponse(
    int TotalSignals,
    int ClusteredSignals,
    int ClusterCount,
    int? SourceCount,
    JsonElement TopClusters,
    string? ServedFrom = null
);

public sealed record SignalConfidenceDistributionResponse(
    int High,
    int Medium,
    int Low,
    int Unscored,
    int Total
);

public sealed record SignalLinkedEventResponse(
    string EventId,
    string Title,
    string Source,
    string EventType,
    string? Timestamp,
    double? RiskScore,
    double? ConfidenceScore
);

public sealed record SignalEvidenceResponse(
    int ActorCount,
    bool HasLocation,
    int ClusterPeerCount,
    int ThemeCount
);

public sealed record SignalDetailResponse(
    string SignalId,
    string? SourceEventId,
    string Source,
    string SignalType,
    string Title,
    string? Body,
    string? Timestamp,
    List<string> Actors,
    string? LocationName,
    double? LocationLat,
    double? LocationLon,
    double? SentimentScore,
    double? Confidence,
    string? ClusterId,
    JsonElement Meta,
    SignalLinkedEventResponse? LinkedEvent,
    List<SignalRecordResponse> ClusterPeers,
    SignalEvidenceResponse Evidence,
    AnalystIndicatorResponse? Indicator = null
);

public sealed record AlertRecordResponse(
    string? AlertId,
    string AlertType,
    string Severity,
    string Title,
    string? Description,
    string? Entity,
    string? EntityType,
    int EventCount,
    double Baseline,
    double ZScore,
    List<string> Sources,
    string? Location,
    string? DetectedAt,
    string? ResolvedAt,
    bool Acknowledged,
    JsonElement Metadata,
    AnalystIndicatorResponse? Indicator = null
);

public sealed record AlertsResponse(
    int Total,
    List<AlertRecordResponse> Alerts
);

public sealed record SituationRecordResponse(
    string SituationId,
    string Title,
    string? Description,
    List<string> EventIds,
    List<string> ActorIds,
    double RiskScore,
    string Severity,
    string Region,
    int EventCount,
    string Status,
    string? DetectedAt,
    string? UpdatedAt,
    JsonElement Meta,
    AnalystIndicatorResponse? Indicator = null
);

public sealed record SituationsResponse(
    int Total,
    int Limit,
    List<SituationRecordResponse> Situations
);

public sealed record NarrativeRecordResponse(
    string NarrativeId,
    string SignalType,
    string Topic,
    double Strength,
    double Confidence,
    string Severity,
    int EventCount,
    int SourceCount,
    List<string> Sources,
    List<string> Actors,
    List<string> SampleTitles,
    string? WindowStart,
    string? WindowEnd,
    string? DetectedAt,
    JsonElement Metadata,
    JsonElement GeographicSpread,
    string Status
);

public sealed record NarrativeListResponse(
    int Total,
    int Limit,
    int Offset,
    List<NarrativeRecordResponse> Narratives
);

public sealed record NarrativeSummaryResponse(
    int Total,
    Dictionary<string, int> ByType,
    Dictionary<string, int> BySeverity,
    string? ServedFrom = null
);

public sealed record NarrativeForecastResponse(
    string NarrativeId,
    int Horizon,
    string Method,
    double Confidence,
    List<double> History,
    List<double> Forecast,
    List<double> Lower,
    List<double> Upper
);

public sealed record CopilotContextSummaryResponse(
    string? EventId,
    bool HasEvent,
    int PastDecisionsCount,
    int AlertCount,
    int NarrativeCount,
    int ActorCount,
    int SimilarEventCount
);

public sealed record CopilotAnswerResponse(
    string Question,
    string Answer,
    CopilotContextSummaryResponse ContextSummary,
    bool LlmUsed,
    string Model,
    IReadOnlyList<CopilotActionResponse> Actions
);

// A platform action the operator copilot proposes for the analyst to execute.
public sealed record CopilotActionResponse(
    string Type,                       // navigate|open_event|open_entity|open_workspace|search|pin_evidence|create_task|open_report|focus_map|ack_alert
    string Label,
    Dictionary<string, string> Params
);

public sealed record CopilotExplainResponse(
    string EventId,
    string EventTitle,
    double RiskScore,
    string Briefing,
    CopilotContextSummaryResponse Evidence,
    bool LlmUsed,
    string Model
);

public sealed record CopilotSimilarDecisionResponse(
    string? EventId,
    string? CoaText,
    string? Analyst,
    string? Status,
    string? Outcome,
    string? CreatedAt
);

public sealed record CopilotSimilarResponse(
    string EventId,
    string EventType,
    List<CopilotSimilarDecisionResponse> SimilarDecisions,
    int Total,
    string Insight
);

public sealed record CopilotRecommendationResponse(
    string EventId,
    double RiskScore,
    string PrimaryRecommendation,
    string HistoricalPrecedent,
    string Confidence,
    string Reasoning,
    string? AiRecommendation,
    bool LlmUsed,
    CopilotContextSummaryResponse Evidence
);

public sealed record CopilotSummaryItemResponse(
    string? Severity,
    string? Title,
    string? Topic,
    string? Source,
    string? EventType
);

public sealed record CopilotSummaryResponse(
    string GeneratedAt,
    int WindowHours,
    double RiskScore,
    List<CopilotSummaryItemResponse> Alerts,
    List<CopilotSummaryItemResponse> Narratives,
    List<CopilotSummaryItemResponse> RecentEvents,
    List<string> Coas
);

public sealed record IndicatorLinkCountsResponse(
    int Actors,
    int Narratives,
    int Signals,
    int Alerts,
    int Regions,
    int Sources,
    int Events
);

public sealed record AnalystIndicatorResponse(
    string Id,
    string Label,
    string Category,
    string IndicatorKind,
    string EvidenceKind,
    string AssessmentKind,
    string Severity,
    string Driver,
    string DriverCode,
    string Trajectory,
    string TrajectoryCode,
    string RecommendedAction,
    string RecommendedActionCode,
    string? Region,
    double Score,
    double Confidence,
    double Corroboration,
    IndicatorLinkCountsResponse Linked,
    string Summary,
    string ObservationSummary,
    string AssessmentSummary,
    string CorrelationSummary
);

public sealed record UnrestOverviewResponse(
    string UnrestLevel,
    double OverallPressure,
    int HotRegionCount,
    int RisingNarratives,
    int CorroboratedAlerts,
    int WatchedActors,
    string? TopRegion,
    string? TopNarrative,
    string RecommendedAction
);

public sealed record UnrestRegionResponse(
    string IndicatorKind,
    string EvidenceKind,
    string AssessmentKind,
    string Region,
    int EventCount,
    double AvgSentiment,
    double NegativeRatio,
    double AvgRisk,
    int SourceCount,
    int ActorCount,
    int NarrativeCount,
    int AlertCount,
    List<string> TopTopics,
    List<string> TopActors,
    string Momentum,
    string Trajectory,
    string TrajectoryCode,
    string Driver,
    string DriverCode,
    double UnrestScore,
    string RecommendedAction,
    string RecommendedActionCode,
    double GeographicConfidence,
    string ObservationSummary,
    string AssessmentSummary,
    string CorrelationSummary,
    string WatchReason
);

public sealed record UnrestNarrativeResponse(
    string IndicatorKind,
    string EvidenceKind,
    string AssessmentKind,
    string? NarrativeId,
    string Topic,
    string? SignalType,
    string Severity,
    double Strength,
    double Confidence,
    int EventCount,
    int SourceCount,
    int ActorCount,
    List<string> Actors,
    string? TopRegion,
    Dictionary<string, double> GeographicSpread,
    string Momentum,
    string Trajectory,
    string TrajectoryCode,
    string Driver,
    string DriverCode,
    double UnrestScore,
    double ProtestSignal,
    string RecommendedAction,
    string RecommendedActionCode,
    string ObservationSummary,
    string AssessmentSummary,
    string CorrelationSummary,
    string WatchReason
);

public sealed record UnrestActorResponse(
    string IndicatorKind,
    string EvidenceKind,
    string AssessmentKind,
    string ActorId,
    string Name,
    string Type,
    int MentionCount,
    double InfluenceScore,
    int EventCount,
    int NarrativeCount,
    int AlertCount,
    double AvgRisk,
    double AvgSentiment,
    List<string> PrimaryRegions,
    string Driver,
    string DriverCode,
    string Trajectory,
    string TrajectoryCode,
    double UnrestScore,
    string RecommendedAction,
    string RecommendedActionCode,
    string ObservationSummary,
    string AssessmentSummary,
    string CorrelationSummary,
    string WatchReason
);

public sealed record UnrestAlertResponse(
    string IndicatorKind,
    string EvidenceKind,
    string AssessmentKind,
    string AlertId,
    string Title,
    string Severity,
    string AlertType,
    int EventCount,
    int SourceCount,
    double CorroborationScore,
    double UnrestScore,
    string? LinkedRegion,
    List<string> LinkedNarratives,
    string Driver,
    string DriverCode,
    string Trajectory,
    string TrajectoryCode,
    string RecommendedAction,
    string RecommendedActionCode,
    string ObservationSummary,
    string AssessmentSummary,
    string CorrelationSummary,
    string WatchReason
);

public sealed record UnrestWatchResponse(
    string? GeneratedAt,
    int WindowHours,
    UnrestOverviewResponse Overview,
    List<UnrestRegionResponse> Regions,
    List<UnrestNarrativeResponse> Narratives,
    List<UnrestActorResponse> Actors,
    List<UnrestAlertResponse> Alerts,
    string? ServedFrom = null
);
