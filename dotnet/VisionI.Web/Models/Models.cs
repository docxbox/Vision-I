using System.ComponentModel.DataAnnotations;
using System.Text.Json;
using System.Text.Json.Serialization;

namespace VisionI.Web.Models;
public class LoginDto {
    [Required, EmailAddress] public string Email    { get; set; } = "";
    [Required]               public string Password { get; set; } = "";
}

public class RegisterDto {
    [Required]               public string DisplayName  { get; set; } = "";
                             public string Organization { get; set; } = "";
                             public string Segment      { get; set; } = "";
    [Required, EmailAddress] public string Email        { get; set; } = "";
    [Required, MinLength(8)] public string Password     { get; set; } = "";
}

public class UserDto {
    [JsonPropertyName("id")]
    public string Id { get; set; } = "";

    [JsonPropertyName("email")]
    public string Email { get; set; } = "";

    [JsonPropertyName("display_name")]
    public string DisplayName { get; set; } = "";

    [JsonPropertyName("role")]
    public string Role { get; set; } = "";

    [JsonPropertyName("is_active")]
    public bool IsActive { get; set; } = true;

    public bool IsAdmin => Role == "Admin";
}

public class LoginResponseDto {
    [JsonPropertyName("token")]
    public string Token { get; set; } = "";

    [JsonPropertyName("user")]
    public UserDto User { get; set; } = new();
}
public class IngestStartResponse {
    [JsonPropertyName("job_id")]
    public string? JobId { get; set; }

    [JsonPropertyName("message")]
    public string? Message { get; set; }
}
public class EventDto {
    [JsonPropertyName("event_id")]
    public string? EventId { get; set; }

    [JsonPropertyName("source")]
    public string? Source { get; set; }

    [JsonPropertyName("source_id")]
    public string? SourceId { get; set; }

    [JsonPropertyName("event_type")]
    public string? EventType { get; set; }

    [JsonPropertyName("title")]
    public string? Title { get; set; }

    [JsonPropertyName("description")]
    public string? Description { get; set; }

    [JsonPropertyName("body")]
    public string? Body { get; set; }

    [JsonPropertyName("url")]
    public string? Url { get; set; }

    [JsonPropertyName("language")]
    public string? Language { get; set; }

    [JsonPropertyName("timestamp")]
    public string? Timestamp { get; set; }

    [JsonPropertyName("ingest_time")]
    public string? IngestTime { get; set; }

    [JsonPropertyName("author")]
    public string? Author { get; set; }

    [JsonPropertyName("sentiment")]
    public SentimentDto? Sentiment { get; set; }

    [JsonPropertyName("location")]
    public LocationDto? Location { get; set; }

    [JsonPropertyName("actors")]
    public List<ActorDto> Actors { get; set; } = new();

    [JsonPropertyName("tags")]
    public List<string> Tags { get; set; } = new();

    [JsonPropertyName("confidence_score")]
    public double? ConfidenceScore { get; set; }

    [JsonPropertyName("influence_score")]
    public double? InfluenceScore { get; set; }

    [JsonPropertyName("risk_score")]
    public double? RiskScore { get; set; }

    [JsonPropertyName("provenance_id")]
    public string? ProvenanceId { get; set; }

    [JsonPropertyName("signal_count")]
    public int? SignalCount { get; set; }

    [JsonPropertyName("supporting_signals")]
    public List<string>? SupportingSignals { get; set; }

    [JsonPropertyName("reasoning")]
    public string? Reasoning { get; set; }

    // Social correlation relevance score (0-1), set by /events/{id}/social endpoint
    [JsonPropertyName("_relevance")]
    public double Relevance { get; set; }

    // Marked true when event's actors match a recent entity_spike anomaly (z>=3)
    [JsonPropertyName("is_anomaly")]
    public bool IsAnomaly { get; set; }

    // Optional region-to-percentage map attached to events tied to a clustered narrative
    [JsonPropertyName("narrative_geographic_spread")]
    public Dictionary<string, double>? NarrativeGeographicSpread { get; set; }

    [JsonPropertyName("snippet")]
    public string? Snippet { get; set; }

    [JsonPropertyName("source_family")]
    public string? SourceFamily { get; set; }

    [JsonPropertyName("feed_kind")]
    public string? FeedKind { get; set; }

    [JsonPropertyName("has_external_link")]
    public bool HasExternalLink { get; set; }

    [JsonPropertyName("actor_count")]
    public int? ActorCount { get; set; }

    [JsonPropertyName("tag_count")]
    public int? TagCount { get; set; }

    [JsonPropertyName("location_summary")]
    public string? LocationSummary { get; set; }

    [JsonPropertyName("feed_summary")]
    public string? FeedSummary { get; set; }

    [JsonPropertyName("engagement")]
    public Dictionary<string, JsonElement>? Engagement { get; set; }

    [JsonPropertyName("linked_situation")]
    public EventSituationLinkDto? LinkedSituation { get; set; }

    [JsonPropertyName("linked_source_count")]
    public int LinkedSourceCount { get; set; }

    [JsonPropertyName("corroboration_score")]
    public double? CorroborationScore { get; set; }

    [JsonPropertyName("feed_score")]
    public double? FeedScore { get; set; }
}

public class EventSituationLinkDto
{
    [JsonPropertyName("situation_id")]
    public string? SituationId { get; set; }

    [JsonPropertyName("subcase_id")]
    public string? SubcaseId { get; set; }

    [JsonPropertyName("parent_situation_id")]
    public string? ParentSituationId { get; set; }

    [JsonPropertyName("title")]
    public string? Title { get; set; }

    [JsonPropertyName("severity")]
    public string? Severity { get; set; }

    [JsonPropertyName("risk_score")]
    public double? RiskScore { get; set; }

    [JsonPropertyName("status")]
    public string? Status { get; set; }

    [JsonPropertyName("region")]
    public string? Region { get; set; }

    [JsonPropertyName("event_count")]
    public int EventCount { get; set; }

    [JsonIgnore]
    public bool IsSubcase => !string.IsNullOrWhiteSpace(SubcaseId);

    [JsonIgnore]
    public string DisplayCaseId => IsSubcase
        ? SubcaseId!
        : SituationId ?? "";

    [JsonIgnore]
    public string ThreadId => ParentSituationId
        ?? SituationId
        ?? "";
}

public class SentimentDto {
    [JsonPropertyName("label")]
    public string Label { get; set; } = "NEUTRAL";

    [JsonPropertyName("score")]
    public double Score { get; set; } = 0.5;
}

public class LocationDto {
    [JsonPropertyName("lat")]
    public double? Lat { get; set; }

    [JsonPropertyName("lon")]
    public double? Lon { get; set; }

    [JsonPropertyName("name")]
    public string? Name { get; set; }

    [JsonPropertyName("country")]
    public string? Country { get; set; }
}

public class ActorDto {
    [JsonPropertyName("name")]
    public string? Name { get; set; }

    [JsonPropertyName("type")]
    public string? Type { get; set; }

    [JsonPropertyName("canonical")]
    public string? Canonical { get; set; }
}

public class EventsResponse {
    [JsonPropertyName("total")]
    public int Total { get; set; }

    [JsonPropertyName("limit")]
    public int Limit { get; set; }

    [JsonPropertyName("offset")]
    public int Offset { get; set; }

    [JsonPropertyName("events")]
    public List<EventDto> Events { get; set; } = new();

    [JsonPropertyName("mode")]
    public string? Mode { get; set; }

    [JsonPropertyName("sort")]
    public string? Sort { get; set; }

    [JsonPropertyName("group_by")]
    public string? GroupBy { get; set; }

    [JsonPropertyName("groups")]
    public List<EventFeedGroupDto> Groups { get; set; } = new();
}

public class EventFeedGroupDto
{
    [JsonPropertyName("group_key")]
    public string? GroupKey { get; set; }

    [JsonPropertyName("group_type")]
    public string? GroupType { get; set; }

    [JsonPropertyName("title")]
    public string? Title { get; set; }

    [JsonPropertyName("severity")]
    public string? Severity { get; set; }

    [JsonPropertyName("region")]
    public string? Region { get; set; }

    [JsonPropertyName("source_count")]
    public int SourceCount { get; set; }

    [JsonPropertyName("corroboration_score")]
    public double CorroborationScore { get; set; }

    [JsonPropertyName("event_count")]
    public int EventCount { get; set; }

    [JsonPropertyName("events")]
    public List<EventDto> Events { get; set; } = new();

    [JsonIgnore]
    public EventSituationLinkDto? LinkedSituation
        => Events.Select(static e => e.LinkedSituation).FirstOrDefault(static s => s is not null);
}

public class EventIntelligenceDto
{
    [JsonPropertyName("event")]
    public EventDto Event { get; set; } = new();

    [JsonPropertyName("t0")]
    public string? T0 { get; set; }

    [JsonPropertyName("related_news")]
    public List<EventDto> RelatedNews { get; set; } = new();

    [JsonPropertyName("social_reactions")]
    public List<EventDto> SocialReactions { get; set; } = new();

    [JsonPropertyName("signals")]
    public List<SignalDto> Signals { get; set; } = new();

    [JsonPropertyName("narratives")]
    public List<NarrativeSignalDto> Narratives { get; set; } = new();

    [JsonPropertyName("actors")]
    public List<string> Actors { get; set; } = new();

    [JsonPropertyName("physical_signals")]
    public PhysicalSignalsDto PhysicalSignals { get; set; } = new();

    [JsonPropertyName("reaction_timeline")]
    public List<ReactionTimelineEntryDto> ReactionTimeline { get; set; } = new();

    [JsonPropertyName("narrative_clusters")]
    public List<NarrativeClusterDto> NarrativeClusters { get; set; } = new();

    [JsonPropertyName("divergence_score")]
    public double DivergenceScore { get; set; }

    [JsonPropertyName("influencer_amplification")]
    public InfluencerAmplificationDto InfluencerAmplification { get; set; } = new();
}

public class NarrativeSignalDto
{
    [JsonPropertyName("narrative_id")]
    public string? NarrativeId { get; set; }

    [JsonPropertyName("signal_type")]
    public string? SignalType { get; set; }

    [JsonPropertyName("topic")]
    public string? Topic { get; set; }

    [JsonPropertyName("strength")]
    public double Strength { get; set; }

    [JsonPropertyName("confidence")]
    public double Confidence { get; set; }

    [JsonPropertyName("severity")]
    public string? Severity { get; set; }

    [JsonPropertyName("event_count")]
    public int EventCount { get; set; }

    [JsonPropertyName("source_count")]
    public int SourceCount { get; set; }

    [JsonPropertyName("sources")]
    public List<string> Sources { get; set; } = new();

    [JsonPropertyName("actors")]
    public List<string> Actors { get; set; } = new();

    [JsonPropertyName("sample_titles")]
    public List<string> SampleTitles { get; set; } = new();

    [JsonPropertyName("detected_at")]
    public string? DetectedAt { get; set; }
}

public class PhysicalSignalsDto
{
    [JsonPropertyName("events")]
    public List<EventDto> Events { get; set; } = new();

    [JsonPropertyName("assets")]
    public List<PhysicalAssetDto> Assets { get; set; } = new();
}

public class PhysicalAssetDto
{
    [JsonPropertyName("asset_id")]
    public string? AssetId { get; set; }

    [JsonPropertyName("asset_type")]
    public string? AssetType { get; set; }

    [JsonPropertyName("name")]
    public string? Name { get; set; }

    [JsonPropertyName("callsign")]
    public string? Callsign { get; set; }

    [JsonPropertyName("identifier")]
    public string? Identifier { get; set; }

    [JsonPropertyName("origin_country")]
    public string? OriginCountry { get; set; }

    [JsonPropertyName("last_lat")]
    public double? LastLat { get; set; }

    [JsonPropertyName("last_lon")]
    public double? LastLon { get; set; }

    [JsonPropertyName("last_altitude")]
    public double? LastAltitude { get; set; }

    [JsonPropertyName("last_speed")]
    public double? LastSpeed { get; set; }

    [JsonPropertyName("last_heading")]
    public double? LastHeading { get; set; }

    [JsonPropertyName("last_seen")]
    public string? LastSeen { get; set; }

    [JsonPropertyName("proximity_score")]
    public double ProximityScore { get; set; }
}

public class ReactionTimelineEntryDto
{
    [JsonPropertyName("kind")]
    public string? Kind { get; set; }

    [JsonPropertyName("timestamp")]
    public string? Timestamp { get; set; }

    [JsonPropertyName("source")]
    public string? Source { get; set; }

    [JsonPropertyName("title")]
    public string? Title { get; set; }

    [JsonPropertyName("delta_minutes")]
    public int? DeltaMinutes { get; set; }

    [JsonPropertyName("sentiment_score")]
    public double? SentimentScore { get; set; }

    [JsonPropertyName("amplification_score")]
    public double? AmplificationScore { get; set; }

    [JsonPropertyName("strength")]
    public double? Strength { get; set; }
}

public class NarrativeClusterDto
{
    [JsonPropertyName("cluster_id")]
    public string? ClusterId { get; set; }

    [JsonPropertyName("signal_count")]
    public int SignalCount { get; set; }

    [JsonPropertyName("sources")]
    public List<string> Sources { get; set; } = new();

    [JsonPropertyName("titles")]
    public List<string> Titles { get; set; } = new();
}

public class InfluencerAmplificationDto
{
    [JsonPropertyName("amplification_score")]
    public double AmplificationScore { get; set; }

    [JsonPropertyName("top_amplifiers")]
    public List<AmplifierDto> TopAmplifiers { get; set; } = new();
}

public class AmplifierDto
{
    [JsonPropertyName("event_id")]
    public string? EventId { get; set; }

    [JsonPropertyName("source")]
    public string? Source { get; set; }

    [JsonPropertyName("author")]
    public string? Author { get; set; }

    [JsonPropertyName("title")]
    public string? Title { get; set; }

    [JsonPropertyName("timestamp")]
    public string? Timestamp { get; set; }

    [JsonPropertyName("amplification_score")]
    public double AmplificationScore { get; set; }
}
public class CreateDecisionDto {
    [JsonPropertyName("event_id")]
    public string EventId { get; set; } = "";

    [JsonPropertyName("coa_index")]
    public int CoaIndex { get; set; }

    [JsonPropertyName("coa_text")]
    public string CoaText { get; set; } = "";

    [JsonPropertyName("analyst")]
    public string Analyst { get; set; } = "analyst";

    [JsonPropertyName("status")]
    public string Status { get; set; } = "approved";

    [JsonPropertyName("rationale")]
    public string? Rationale { get; set; }
}

public class DecisionDto {
    [JsonPropertyName("id")]
    public string Id { get; set; } = "";

    [JsonPropertyName("event_id")]
    public string EventId { get; set; } = "";

    [JsonPropertyName("coa_index")]
    public int CoaIndex { get; set; }

    [JsonPropertyName("coa_text")]
    public string CoaText { get; set; } = "";

    [JsonPropertyName("analyst")]
    public string Analyst { get; set; } = "";

    [JsonPropertyName("status")]
    public string Status { get; set; } = "";

    [JsonPropertyName("rationale")]
    public string? Rationale { get; set; }

    [JsonPropertyName("outcome")]
    public string? Outcome { get; set; }

    [JsonPropertyName("outcome_notes")]
    public string? OutcomeNotes { get; set; }

    [JsonPropertyName("created_at")]
    public string? CreatedAt { get; set; }
}

public class DecisionsResponse {
    [JsonPropertyName("total")]
    public int Total { get; set; }

    [JsonPropertyName("limit")]
    public int Limit { get; set; }

    [JsonPropertyName("decisions")]
    public List<DecisionDto> Decisions { get; set; } = new();
}
public class DetectedSituationDto {
    [JsonPropertyName("situation_id")]  public string? SituationId { get; set; }
    [JsonPropertyName("title")]         public string? Title { get; set; }
    [JsonPropertyName("description")]   public string? Description { get; set; }
    [JsonPropertyName("event_ids")]     public List<string> EventIds { get; set; } = new();
    [JsonPropertyName("actor_ids")]     public List<string> ActorIds { get; set; } = new();
    [JsonPropertyName("risk_score")]    public double RiskScore { get; set; }
    [JsonPropertyName("severity")]      public string? Severity { get; set; }
    [JsonPropertyName("region")]        public string? Region { get; set; }
    [JsonPropertyName("event_count")]   public int EventCount { get; set; }
    [JsonPropertyName("status")]        public string? Status { get; set; }
    [JsonPropertyName("detected_at")]   public string? DetectedAt { get; set; }
    [JsonPropertyName("updated_at")]    public string? UpdatedAt { get; set; }
    [JsonPropertyName("meta")]          public Dictionary<string, object>? Meta { get; set; }
    [JsonPropertyName("indicator")]     public AnalystIndicatorDto? Indicator { get; set; }

    [JsonIgnore]
    public string? SubcaseId => GetMetaString("subcase_id");

    [JsonIgnore]
    public string? ParentSituationId => GetMetaString("parent_situation_id");

    [JsonIgnore]
    public string? TopicFamily => GetMetaString("topic_family");

    [JsonIgnore]
    public bool IsSubcase => !string.IsNullOrWhiteSpace(SubcaseId);

    private string? GetMetaString(string key)
    {
        if (Meta is null || !Meta.TryGetValue(key, out var raw) || raw is null)
            return null;

        return raw switch
        {
            string text when !string.IsNullOrWhiteSpace(text) => text,
            JsonElement { ValueKind: JsonValueKind.String } json => json.GetString(),
            JsonElement { ValueKind: JsonValueKind.Number } json => json.ToString(),
            JsonElement { ValueKind: JsonValueKind.True } => "true",
            JsonElement { ValueKind: JsonValueKind.False } => "false",
            _ => raw.ToString()
        };
    }
}

public class SituationsResponse {
    [JsonPropertyName("total")]      public int Total { get; set; }
    [JsonPropertyName("limit")]      public int Limit { get; set; }
    [JsonPropertyName("situations")] public List<DetectedSituationDto> Situations { get; set; } = new();
}
public class EntityDto {
    [JsonPropertyName("id")]
    public string? Id { get; set; }

    [JsonPropertyName("entity_id")]
    public string? EntityId { get; set; }

    [JsonPropertyName("name")]
    public string? Name { get; set; }

    [JsonPropertyName("type")]
    public string? Type { get; set; }

    [JsonPropertyName("mention_count")]
    public int MentionCount { get; set; }

    [JsonPropertyName("influence_score")]
    public double? InfluenceScore { get; set; }

    [JsonPropertyName("sentiment_score")]
    public double? SentimentScore { get; set; }

    [JsonPropertyName("narrative_count")]
    public int NarrativeCount { get; set; }

    [JsonPropertyName("event_count")]
    public int EventCount { get; set; }

    [JsonPropertyName("last_seen")]
    public string? LastSeen { get; set; }
}

public class EntitiesResponse {
    [JsonPropertyName("total")]
    public int Total { get; set; }

    [JsonPropertyName("entities")]
    public List<EntityDto> Entities { get; set; } = new();
}

public class EgoGraphDto {
    [JsonPropertyName("entity_id")]
    public string? EntityId { get; set; }

    [JsonPropertyName("name")]
    public string? Name { get; set; }

    [JsonPropertyName("nodes")]
    public List<GraphNode>? Nodes { get; set; }

    [JsonPropertyName("edges")]
    public List<GraphEdge>? Edges { get; set; }
}

public class GraphNode {
    [JsonPropertyName("id")]
    public string? Id { get; set; }

    [JsonPropertyName("name")]
    public string? Name { get; set; }

    [JsonPropertyName("type")]
    public string? Type { get; set; }

    [JsonPropertyName("mentions")]
    public int Mentions { get; set; }
}

public class GraphEdge {
    [JsonPropertyName("source")]
    public string Source { get; set; } = "";

    [JsonPropertyName("target")]
    public string Target { get; set; } = "";

    [JsonPropertyName("relation")]
    public string Relation { get; set; } = "";

    [JsonPropertyName("weight")]
    public int Weight { get; set; }
}
public class GraphEgoResponse {
    [JsonPropertyName("nodes")] public List<GraphNodeDto>? Nodes { get; set; }
    [JsonPropertyName("edges")] public List<GraphEdgeDto>? Edges { get; set; }
}

public class GraphNodeDto {
    [JsonPropertyName("id")]            public string? Id { get; set; }
    [JsonPropertyName("name")]          public string? Name { get; set; }
    [JsonPropertyName("type")]          public string? Type { get; set; }
    [JsonPropertyName("mention_count")] public int MentionCount { get; set; }
    [JsonPropertyName("last_seen")]     public string? LastSeen { get; set; }
}

public class GraphEdgeDto {
    [JsonPropertyName("source")]        public string? Source { get; set; }
    [JsonPropertyName("target")]        public string? Target { get; set; }
    [JsonPropertyName("relation_type")] public string? RelationType { get; set; }
    [JsonPropertyName("weight")]        public double Weight { get; set; }
    [JsonPropertyName("evidence_mode")] public string? EvidenceMode { get; set; }
}
public class SentimentTimelineResponse {
    // Python returns the array under "data", not "timeline"
    [JsonPropertyName("data")]
    public List<SentimentBucket>? Timeline { get; set; }

    [JsonPropertyName("bucket_size")]
    public string? BucketSize { get; set; }

    [JsonPropertyName("query")]
    public string? Query { get; set; }

    [JsonPropertyName("source")]
    public string? Source { get; set; }
}

public class SentimentBucket {
    [JsonPropertyName("bucket")]
    public string Bucket { get; set; } = "";

    // Python returns "event_count", "positive", "neutral", "negative"
    [JsonPropertyName("event_count")]
    public int TotalEvents { get; set; }

    [JsonPropertyName("positive")]
    public int PositiveCount { get; set; }

    [JsonPropertyName("neutral")]
    public int NeutralCount { get; set; }

    [JsonPropertyName("negative")]
    public int NegativeCount { get; set; }

    [JsonPropertyName("avg_score")]
    public double AvgScore { get; set; }
}
public class StatsDto {
    [JsonPropertyName("total_events")]
    public int TotalEvents { get; set; }

    [JsonPropertyName("by_source")]
    public Dictionary<string, int> BySource { get; set; } = new();

    [JsonPropertyName("by_type")]
    public Dictionary<string, int> ByType { get; set; } = new();

    [JsonPropertyName("generated_at")]
    public string? GeneratedAt { get; set; }
}
public class JobDto {
    [JsonPropertyName("job_id")]
    public string? JobId { get; set; }

    [JsonPropertyName("query")]
    public string? Query { get; set; }

    [JsonPropertyName("status")]
    public string Status { get; set; } = "pending";

    [JsonPropertyName("started_at")]
    public string? StartedAt { get; set; }

    [JsonPropertyName("finished_at")]
    public string? FinishedAt { get; set; }

    [JsonPropertyName("total_events")]
    public int? TotalEvents { get; set; }

    [JsonPropertyName("source_counts")]
    public Dictionary<string, int>? SourceCounts { get; set; }

    [JsonPropertyName("error")]
    public string? Error { get; set; }
}

public class JobsResponse {
    [JsonPropertyName("total")]
    public int Total { get; set; }

    [JsonPropertyName("jobs")]
    public List<JobDto> Jobs { get; set; } = new();
}
public class HealthDto {
    [JsonPropertyName("status")]
    public string? Status { get; set; }

    [JsonPropertyName("db_available")]
    public bool DbAvailable { get; set; }

    [JsonPropertyName("python")]
    public PythonHealthDto? Python { get; set; }

    [JsonPropertyName("timestamp")]
    public string? Timestamp { get; set; }
}

public class PythonHealthDto {
    [JsonPropertyName("status")]
    public string? Status { get; set; }

    [JsonPropertyName("sources")]
    public Dictionary<string, SourceHealth>? Sources { get; set; }
}

public class SourceHealth {
    [JsonPropertyName("status")]
    public string? Status { get; set; }

    [JsonPropertyName("error")]
    public string? Error { get; set; }
}

public class FullHealthDto {
    [JsonPropertyName("status")]
    public string? Status { get; set; }

    [JsonPropertyName("timestamp")]
    public string? Timestamp { get; set; }

    [JsonPropertyName("version")]
    public string? Version { get; set; }

    [JsonPropertyName("db_available")]
    public bool DbAvailable { get; set; }

    [JsonPropertyName("neo4j_available")]
    public bool Neo4jAvailable { get; set; }

    [JsonPropertyName("sources")]
    public Dictionary<string, SourceHealth>? Sources { get; set; }

    [JsonPropertyName("scheduler")]
    public SchedulerInfo? Scheduler { get; set; }

    [JsonPropertyName("memory_jobs")]
    public int MemoryJobs { get; set; }

    [JsonPropertyName("llm")]
    public LlmHealthInfo? Llm { get; set; }

    [JsonPropertyName("swarm")]
    public SwarmHealthInfo? Swarm { get; set; }
}

public class LlmHealthInfo {
    [JsonPropertyName("provider")]  public string? Provider  { get; set; }
    [JsonPropertyName("model")]     public string? Model     { get; set; }
    [JsonPropertyName("available")] public bool Available     { get; set; }
}

public class SwarmHealthInfo {
    [JsonPropertyName("agents")]          public int Agents         { get; set; }
    [JsonPropertyName("total_missions")]  public int TotalMissions  { get; set; }
    [JsonPropertyName("active_missions")] public int ActiveMissions { get; set; }
}

public class SchedulerInfo {
    [JsonPropertyName("running")]
    public bool Running { get; set; }

    [JsonPropertyName("jobs")]
    public List<SchedulerJob>? Jobs { get; set; }

    [JsonPropertyName("reason")]
    public string? Reason { get; set; }
}

public class SchedulerJob {
    [JsonPropertyName("id")]
    public string? Id { get; set; }

    [JsonPropertyName("name")]
    public string? Name { get; set; }

    [JsonPropertyName("next_run")]
    public string? NextRun { get; set; }
}
public class UserResponse {
    [JsonPropertyName("user_id")]
    public string UserId { get; set; } = "";

    [JsonPropertyName("email")]
    public string Email { get; set; } = "";

    [JsonPropertyName("display_name")]
    public string DisplayName { get; set; } = "";

    [JsonPropertyName("role")]
    public string? Role { get; set; }

    [JsonPropertyName("created_at")]
    public DateTime CreatedAt { get; set; }

    [JsonPropertyName("is_active")]
    public bool IsActive { get; set; }
}

public class UsersResponse {
    [JsonPropertyName("total")]
    public int Total { get; set; }

    [JsonPropertyName("users")]
    public List<UserResponse> Users { get; set; } = new();
}

public class TrackedQuery {
    [JsonPropertyName("id")]
    public int Id { get; set; }

    [JsonPropertyName("query")]
    public string? Query { get; set; }

    [JsonPropertyName("created_by")]
    public string? CreatedBy { get; set; }

    [JsonPropertyName("created_at")]
    public string? CreatedAt { get; set; }

    [JsonPropertyName("is_active")]
    public bool IsActive { get; set; }

    [JsonPropertyName("last_run")]
    public string? LastRun { get; set; }

    [JsonPropertyName("run_count")]
    public int RunCount { get; set; }
}

public class QueriesResponse {
    [JsonPropertyName("queries")]
    public List<TrackedQuery> Queries { get; set; } = new();
}

public class NarrativeDto
{
    [JsonPropertyName("narrative_id")]   public string? NarrativeId  { get; set; }
    [JsonPropertyName("signal_type")]    public string  SignalType   { get; set; } = "";
    [JsonPropertyName("topic")]          public string  Topic        { get; set; } = "";
    [JsonPropertyName("strength")]       public double  Strength     { get; set; }
    [JsonPropertyName("confidence")]     public double  Confidence   { get; set; }
    [JsonPropertyName("severity")]       public string  Severity     { get; set; } = "low";
    [JsonPropertyName("event_count")]    public int     EventCount   { get; set; }
    [JsonPropertyName("source_count")]   public int     SourceCount  { get; set; }
    [JsonPropertyName("sources")]        public List<string> Sources { get; set; } = new();
    [JsonPropertyName("actors")]         public List<string> Actors  { get; set; } = new();
    [JsonPropertyName("sample_titles")]  public List<string> SampleTitles { get; set; } = new();
    [JsonPropertyName("window_start")]   public string? WindowStart  { get; set; }
    [JsonPropertyName("window_end")]     public string? WindowEnd    { get; set; }
    [JsonPropertyName("detected_at")]    public string? DetectedAt   { get; set; }
    [JsonPropertyName("status")]         public string  Status       { get; set; } = "active";
    [JsonPropertyName("metadata")]       public Dictionary<string, object>? Metadata { get; set; }
}

public class NarrativesResponse
{
    [JsonPropertyName("total")]      public int Total { get; set; }
    [JsonPropertyName("narratives")] public List<NarrativeDto> Narratives { get; set; } = new();
}

public class NarrativeSummaryDto
{
    [JsonPropertyName("total")]       public int Total { get; set; }
    [JsonPropertyName("by_type")]     public Dictionary<string, int> ByType     { get; set; } = new();
    [JsonPropertyName("by_severity")] public Dictionary<string, int> BySeverity { get; set; } = new();
}

public class AlertDto
{
    [JsonPropertyName("alert_id")]     public string? AlertId     { get; set; }
    [JsonPropertyName("alert_type")]   public string  AlertType   { get; set; } = "";
    [JsonPropertyName("severity")]     public string  Severity    { get; set; } = "medium";
    [JsonPropertyName("title")]        public string  Title       { get; set; } = "";
    [JsonPropertyName("description")]  public string? Description { get; set; }
    [JsonPropertyName("entity")]       public string? Entity      { get; set; }
    [JsonPropertyName("entity_type")]  public string? EntityType  { get; set; }
    [JsonPropertyName("event_count")]  public int     EventCount  { get; set; }
    [JsonPropertyName("baseline")]     public double  Baseline    { get; set; }
    [JsonPropertyName("z_score")]      public double  ZScore      { get; set; }
    [JsonPropertyName("sources")]      public List<string> Sources { get; set; } = new();
    [JsonPropertyName("location")]     public string? Location    { get; set; }
    [JsonPropertyName("detected_at")]  public string? DetectedAt  { get; set; }
    [JsonPropertyName("resolved_at")]  public string? ResolvedAt  { get; set; }
    [JsonPropertyName("acknowledged")] public bool    Acknowledged { get; set; }
    [JsonPropertyName("escalated")]    public bool    Escalated    { get; set; }
    [JsonPropertyName("dismissed")]    public bool    Dismissed    { get; set; }
    [JsonPropertyName("metadata")]     public Dictionary<string, object>? Metadata { get; set; }
    [JsonPropertyName("indicator")]    public AnalystIndicatorDto? Indicator { get; set; }
}

public class AlertsResponse
{
    [JsonPropertyName("total")]  public int Total { get; set; }
    [JsonPropertyName("alerts")] public List<AlertDto> Alerts { get; set; } = new();
}

public class AlertSummaryDto
{
    [JsonPropertyName("unacknowledged")] public int Unacknowledged { get; set; }
    [JsonPropertyName("by_severity")]    public Dictionary<string, int> BySeverity { get; set; } = new();
}

public class UnrestWatchDto
{
    [JsonPropertyName("generated_at")] public string? GeneratedAt { get; set; }
    [JsonPropertyName("window_hours")] public int WindowHours { get; set; }
    [JsonPropertyName("overview")] public UnrestOverviewDto Overview { get; set; } = new();
    [JsonPropertyName("regions")] public List<UnrestRegionDto> Regions { get; set; } = new();
    [JsonPropertyName("narratives")] public List<UnrestNarrativeDto> Narratives { get; set; } = new();
    [JsonPropertyName("actors")] public List<UnrestActorDto> Actors { get; set; } = new();
    [JsonPropertyName("alerts")] public List<UnrestAlertDto> Alerts { get; set; } = new();
}

public class UnrestOverviewDto
{
    [JsonPropertyName("unrest_level")] public string UnrestLevel { get; set; } = "low";
    [JsonPropertyName("overall_pressure")] public double OverallPressure { get; set; }
    [JsonPropertyName("hot_region_count")] public int HotRegionCount { get; set; }
    [JsonPropertyName("rising_narratives")] public int RisingNarratives { get; set; }
    [JsonPropertyName("corroborated_alerts")] public int CorroboratedAlerts { get; set; }
    [JsonPropertyName("watched_actors")] public int WatchedActors { get; set; }
    [JsonPropertyName("top_region")] public string? TopRegion { get; set; }
    [JsonPropertyName("top_narrative")] public string? TopNarrative { get; set; }
    [JsonPropertyName("recommended_action")] public string RecommendedAction { get; set; } = "";
}

public class IndicatorLinkCountsDto
{
    [JsonPropertyName("actors")] public int Actors { get; set; }
    [JsonPropertyName("narratives")] public int Narratives { get; set; }
    [JsonPropertyName("signals")] public int Signals { get; set; }
    [JsonPropertyName("alerts")] public int Alerts { get; set; }
    [JsonPropertyName("regions")] public int Regions { get; set; }
    [JsonPropertyName("sources")] public int Sources { get; set; }
    [JsonPropertyName("events")] public int Events { get; set; }
}

public class AnalystIndicatorDto
{
    [JsonPropertyName("id")]
    public string Id { get; set; } = "";
    [JsonPropertyName("label")]
    public string Label { get; set; } = "";
    [JsonPropertyName("category")]
    public string Category { get; set; } = "";
    [JsonPropertyName("indicator_kind")]
    public string IndicatorKind { get; set; } = "";
    [JsonPropertyName("evidence_kind")]
    public string EvidenceKind { get; set; } = "observed";
    [JsonPropertyName("assessment_kind")]
    public string AssessmentKind { get; set; } = "";
    [JsonPropertyName("severity")]
    public string Severity { get; set; } = "medium";
    [JsonPropertyName("driver")]
    public string Driver { get; set; } = "";
    [JsonPropertyName("driver_code")]
    public string DriverCode { get; set; } = "";
    [JsonPropertyName("trajectory")]
    public string Trajectory { get; set; } = "stable";
    [JsonPropertyName("trajectory_code")]
    public string TrajectoryCode { get; set; } = "stable";
    [JsonPropertyName("recommended_action")]
    public string RecommendedAction { get; set; } = "monitor";
    [JsonPropertyName("recommended_action_code")]
    public string RecommendedActionCode { get; set; } = "monitor";
    [JsonPropertyName("region")]
    public string? Region { get; set; }
    [JsonPropertyName("score")]
    public double Score { get; set; }
    [JsonPropertyName("confidence")]
    public double Confidence { get; set; }
    [JsonPropertyName("corroboration")]
    public double Corroboration { get; set; }
    [JsonPropertyName("linked")]
    public IndicatorLinkCountsDto Linked { get; set; } = new();
    [JsonPropertyName("summary")]
    public string Summary { get; set; } = "";
    [JsonPropertyName("observation_summary")]
    public string ObservationSummary { get; set; } = "";
    [JsonPropertyName("assessment_summary")]
    public string AssessmentSummary { get; set; } = "";
    [JsonPropertyName("correlation_summary")]
    public string CorrelationSummary { get; set; } = "";
}

public class UnrestRegionDto
{
    [JsonPropertyName("indicator_kind")] public string IndicatorKind { get; set; } = "region";
    [JsonPropertyName("evidence_kind")] public string EvidenceKind { get; set; } = "correlated";
    [JsonPropertyName("assessment_kind")] public string AssessmentKind { get; set; } = "region_pressure";
    [JsonPropertyName("region")] public string Region { get; set; } = "";
    [JsonPropertyName("event_count")] public int EventCount { get; set; }
    [JsonPropertyName("avg_sentiment")] public double AvgSentiment { get; set; }
    [JsonPropertyName("negative_ratio")] public double NegativeRatio { get; set; }
    [JsonPropertyName("avg_risk")] public double AvgRisk { get; set; }
    [JsonPropertyName("source_count")] public int SourceCount { get; set; }
    [JsonPropertyName("actor_count")] public int ActorCount { get; set; }
    [JsonPropertyName("narrative_count")] public int NarrativeCount { get; set; }
    [JsonPropertyName("alert_count")] public int AlertCount { get; set; }
    [JsonPropertyName("top_topics")] public List<string> TopTopics { get; set; } = new();
    [JsonPropertyName("top_actors")] public List<string> TopActors { get; set; } = new();
    [JsonPropertyName("momentum")] public string Momentum { get; set; } = "stable";
    [JsonPropertyName("trajectory")] public string Trajectory { get; set; } = "stable";
    [JsonPropertyName("trajectory_code")] public string TrajectoryCode { get; set; } = "stable";
    [JsonPropertyName("driver")] public string Driver { get; set; } = "";
    [JsonPropertyName("driver_code")] public string DriverCode { get; set; } = "";
    [JsonPropertyName("unrest_score")] public double UnrestScore { get; set; }
    [JsonPropertyName("recommended_action")] public string RecommendedAction { get; set; } = "monitor";
    [JsonPropertyName("recommended_action_code")] public string RecommendedActionCode { get; set; } = "monitor";
    [JsonPropertyName("geographic_confidence")] public double GeographicConfidence { get; set; }
    [JsonPropertyName("observation_summary")] public string ObservationSummary { get; set; } = "";
    [JsonPropertyName("assessment_summary")] public string AssessmentSummary { get; set; } = "";
    [JsonPropertyName("correlation_summary")] public string CorrelationSummary { get; set; } = "";
    [JsonPropertyName("watch_reason")] public string WatchReason { get; set; } = "";
}

public class UnrestNarrativeDto
{
    [JsonPropertyName("indicator_kind")] public string IndicatorKind { get; set; } = "narrative";
    [JsonPropertyName("evidence_kind")] public string EvidenceKind { get; set; } = "inferred";
    [JsonPropertyName("assessment_kind")] public string AssessmentKind { get; set; } = "narrative_spread";
    [JsonPropertyName("narrative_id")] public string? NarrativeId { get; set; }
    [JsonPropertyName("topic")] public string Topic { get; set; } = "";
    [JsonPropertyName("signal_type")] public string? SignalType { get; set; }
    [JsonPropertyName("severity")] public string Severity { get; set; } = "low";
    [JsonPropertyName("strength")] public double Strength { get; set; }
    [JsonPropertyName("confidence")] public double Confidence { get; set; }
    [JsonPropertyName("event_count")] public int EventCount { get; set; }
    [JsonPropertyName("source_count")] public int SourceCount { get; set; }
    [JsonPropertyName("actor_count")] public int ActorCount { get; set; }
    [JsonPropertyName("actors")] public List<string> Actors { get; set; } = new();
    [JsonPropertyName("top_region")] public string? TopRegion { get; set; }
    [JsonPropertyName("geographic_spread")] public Dictionary<string, double> GeographicSpread { get; set; } = new();
    [JsonPropertyName("momentum")] public string Momentum { get; set; } = "stable";
    [JsonPropertyName("trajectory")] public string Trajectory { get; set; } = "stable";
    [JsonPropertyName("trajectory_code")] public string TrajectoryCode { get; set; } = "stable";
    [JsonPropertyName("driver")] public string Driver { get; set; } = "";
    [JsonPropertyName("driver_code")] public string DriverCode { get; set; } = "";
    [JsonPropertyName("unrest_score")] public double UnrestScore { get; set; }
    [JsonPropertyName("protest_signal")] public double ProtestSignal { get; set; }
    [JsonPropertyName("recommended_action")] public string RecommendedAction { get; set; } = "monitor";
    [JsonPropertyName("recommended_action_code")] public string RecommendedActionCode { get; set; } = "monitor";
    [JsonPropertyName("observation_summary")] public string ObservationSummary { get; set; } = "";
    [JsonPropertyName("assessment_summary")] public string AssessmentSummary { get; set; } = "";
    [JsonPropertyName("correlation_summary")] public string CorrelationSummary { get; set; } = "";
    [JsonPropertyName("watch_reason")] public string WatchReason { get; set; } = "";
}

public class UnrestActorDto
{
    [JsonPropertyName("indicator_kind")] public string IndicatorKind { get; set; } = "actor";
    [JsonPropertyName("evidence_kind")] public string EvidenceKind { get; set; } = "correlated";
    [JsonPropertyName("assessment_kind")] public string AssessmentKind { get; set; } = "actor_influence";
    [JsonPropertyName("actor_id")] public string ActorId { get; set; } = "";
    [JsonPropertyName("name")] public string Name { get; set; } = "";
    [JsonPropertyName("type")] public string Type { get; set; } = "";
    [JsonPropertyName("mention_count")] public int MentionCount { get; set; }
    [JsonPropertyName("influence_score")] public double InfluenceScore { get; set; }
    [JsonPropertyName("event_count")] public int EventCount { get; set; }
    [JsonPropertyName("narrative_count")] public int NarrativeCount { get; set; }
    [JsonPropertyName("alert_count")] public int AlertCount { get; set; }
    [JsonPropertyName("avg_risk")] public double AvgRisk { get; set; }
    [JsonPropertyName("avg_sentiment")] public double AvgSentiment { get; set; }
    [JsonPropertyName("primary_regions")] public List<string> PrimaryRegions { get; set; } = new();
    [JsonPropertyName("driver")] public string Driver { get; set; } = "";
    [JsonPropertyName("driver_code")] public string DriverCode { get; set; } = "";
    [JsonPropertyName("trajectory")] public string Trajectory { get; set; } = "stable";
    [JsonPropertyName("trajectory_code")] public string TrajectoryCode { get; set; } = "stable";
    [JsonPropertyName("unrest_score")] public double UnrestScore { get; set; }
    [JsonPropertyName("recommended_action")] public string RecommendedAction { get; set; } = "monitor";
    [JsonPropertyName("recommended_action_code")] public string RecommendedActionCode { get; set; } = "monitor";
    [JsonPropertyName("observation_summary")] public string ObservationSummary { get; set; } = "";
    [JsonPropertyName("assessment_summary")] public string AssessmentSummary { get; set; } = "";
    [JsonPropertyName("correlation_summary")] public string CorrelationSummary { get; set; } = "";
    [JsonPropertyName("watch_reason")] public string WatchReason { get; set; } = "";
}

public class UnrestAlertDto
{
    [JsonPropertyName("indicator_kind")] public string IndicatorKind { get; set; } = "alert";
    [JsonPropertyName("evidence_kind")] public string EvidenceKind { get; set; } = "correlated";
    [JsonPropertyName("assessment_kind")] public string AssessmentKind { get; set; } = "corroborated_alert";
    [JsonPropertyName("alert_id")] public string AlertId { get; set; } = "";
    [JsonPropertyName("title")] public string Title { get; set; } = "";
    [JsonPropertyName("severity")] public string Severity { get; set; } = "medium";
    [JsonPropertyName("alert_type")] public string AlertType { get; set; } = "";
    [JsonPropertyName("event_count")] public int EventCount { get; set; }
    [JsonPropertyName("source_count")] public int SourceCount { get; set; }
    [JsonPropertyName("corroboration_score")] public double CorroborationScore { get; set; }
    [JsonPropertyName("unrest_score")] public double UnrestScore { get; set; }
    [JsonPropertyName("linked_region")] public string? LinkedRegion { get; set; }
    [JsonPropertyName("linked_narratives")] public List<string> LinkedNarratives { get; set; } = new();
    [JsonPropertyName("driver")] public string Driver { get; set; } = "";
    [JsonPropertyName("driver_code")] public string DriverCode { get; set; } = "";
    [JsonPropertyName("trajectory")] public string Trajectory { get; set; } = "stable";
    [JsonPropertyName("trajectory_code")] public string TrajectoryCode { get; set; } = "stable";
    [JsonPropertyName("recommended_action")] public string RecommendedAction { get; set; } = "review";
    [JsonPropertyName("recommended_action_code")] public string RecommendedActionCode { get; set; } = "review";
    [JsonPropertyName("observation_summary")] public string ObservationSummary { get; set; } = "";
    [JsonPropertyName("assessment_summary")] public string AssessmentSummary { get; set; } = "";
    [JsonPropertyName("correlation_summary")] public string CorrelationSummary { get; set; } = "";
    [JsonPropertyName("watch_reason")] public string WatchReason { get; set; } = "";
}

public static class AnalystIndicatorFactory
{
    public static AnalystIndicatorDto CreateCustom(
        string id,
        string label,
        string category,
        string indicatorKind,
        string evidenceKind,
        string assessmentKind,
        string severity,
        string driver,
        string trajectory,
        string recommendedAction,
        double score,
        double confidence,
        double corroboration,
        IndicatorLinkCountsDto linked,
        string observationSummary,
        string assessmentSummary,
        string correlationSummary,
        string? region = null,
        string summary = "")
        => new()
        {
            Id = id,
            Label = label,
            Category = category,
            IndicatorKind = indicatorKind,
            EvidenceKind = evidenceKind,
            AssessmentKind = assessmentKind,
            Severity = severity,
            Driver = driver,
            DriverCode = NormalizeCode(driver, "emerging_signal"),
            Trajectory = trajectory,
            TrajectoryCode = NormalizeCode(trajectory, "stable"),
            RecommendedAction = recommendedAction,
            RecommendedActionCode = NormalizeCode(recommendedAction, "monitor"),
            Region = region,
            Score = score,
            Confidence = confidence,
            Corroboration = corroboration,
            Linked = linked,
            Summary = IfBlank(summary, observationSummary),
            ObservationSummary = observationSummary,
            AssessmentSummary = assessmentSummary,
            CorrelationSummary = correlationSummary,
        };

    public static AnalystIndicatorDto FromRegion(UnrestRegionDto region) => new()
    {
        Id = region.Region,
        Label = region.Region,
        Category = "region",
        IndicatorKind = region.IndicatorKind,
        EvidenceKind = region.EvidenceKind,
        AssessmentKind = region.AssessmentKind,
        Severity = SeverityFromScore(region.UnrestScore),
        Driver = region.Driver,
        DriverCode = region.DriverCode,
        Trajectory = IfBlank(region.Trajectory, region.Momentum),
        TrajectoryCode = IfBlank(region.TrajectoryCode, IfBlank(region.Trajectory, region.Momentum)),
        RecommendedAction = region.RecommendedAction,
        RecommendedActionCode = IfBlank(region.RecommendedActionCode, NormalizeCode(region.RecommendedAction, "monitor")),
        Region = region.Region,
        Score = region.UnrestScore,
        Confidence = region.GeographicConfidence,
        Corroboration = region.SourceCount > 0 ? Math.Min(1.0, region.SourceCount / 6.0) : 0,
        Summary = region.WatchReason,
        ObservationSummary = IfBlank(region.ObservationSummary, $"{region.EventCount} event(s) were observed in {region.Region}."),
        AssessmentSummary = IfBlank(region.AssessmentSummary, $"Regional pressure score is {region.UnrestScore:0.00}."),
        CorrelationSummary = IfBlank(region.CorrelationSummary, $"{region.NarrativeCount} narrative(s) and {region.AlertCount} alert(s) are linked."),
        Linked = new IndicatorLinkCountsDto
        {
            Actors = region.ActorCount,
            Narratives = region.NarrativeCount,
            Alerts = region.AlertCount,
            Sources = region.SourceCount,
            Events = region.EventCount,
            Regions = 1,
        }
    };

    public static AnalystIndicatorDto FromNarrative(UnrestNarrativeDto narrative) => new()
    {
        Id = narrative.NarrativeId ?? narrative.Topic,
        Label = narrative.Topic,
        Category = "narrative",
        IndicatorKind = narrative.IndicatorKind,
        EvidenceKind = narrative.EvidenceKind,
        AssessmentKind = narrative.AssessmentKind,
        Severity = narrative.Severity,
        Driver = narrative.Driver,
        DriverCode = narrative.DriverCode,
        Trajectory = IfBlank(narrative.Trajectory, narrative.Momentum),
        TrajectoryCode = IfBlank(narrative.TrajectoryCode, IfBlank(narrative.Trajectory, narrative.Momentum)),
        RecommendedAction = narrative.RecommendedAction,
        RecommendedActionCode = IfBlank(narrative.RecommendedActionCode, NormalizeCode(narrative.RecommendedAction, "watch_narrative")),
        Region = narrative.TopRegion,
        Score = narrative.UnrestScore,
        Confidence = narrative.Confidence,
        Corroboration = Math.Min(1.0, narrative.SourceCount / 6.0),
        Summary = narrative.WatchReason,
        ObservationSummary = IfBlank(narrative.ObservationSummary, $"{narrative.EventCount} event(s) and {narrative.SourceCount} source(s) mention this narrative."),
        AssessmentSummary = IfBlank(narrative.AssessmentSummary, $"Narrative strength is {narrative.Strength:0.00} with confidence {narrative.Confidence:0.00}."),
        CorrelationSummary = IfBlank(narrative.CorrelationSummary, $"{narrative.ActorCount} actor(s) and {Math.Max(1, narrative.GeographicSpread.Count)} region(s) are linked."),
        Linked = new IndicatorLinkCountsDto
        {
            Actors = narrative.ActorCount,
            Sources = narrative.SourceCount,
            Events = narrative.EventCount,
            Regions = Math.Max(1, narrative.GeographicSpread.Count),
            Narratives = 1,
        }
    };

    public static AnalystIndicatorDto FromAlert(UnrestAlertDto alert) => new()
    {
        Id = alert.AlertId,
        Label = alert.Title,
        Category = "alert",
        IndicatorKind = alert.IndicatorKind,
        EvidenceKind = alert.EvidenceKind,
        AssessmentKind = alert.AssessmentKind,
        Severity = alert.Severity,
        Driver = IfBlank(alert.Driver, alert.AlertType),
        DriverCode = IfBlank(alert.DriverCode, NormalizeCode(IfBlank(alert.Driver, alert.AlertType), "corroborated_alert")),
        Trajectory = alert.Trajectory,
        TrajectoryCode = IfBlank(alert.TrajectoryCode, alert.Trajectory),
        RecommendedAction = alert.RecommendedAction,
        RecommendedActionCode = IfBlank(alert.RecommendedActionCode, NormalizeCode(alert.RecommendedAction, "review")),
        Region = alert.LinkedRegion,
        Score = alert.UnrestScore,
        Confidence = alert.CorroborationScore,
        Corroboration = alert.CorroborationScore,
        Summary = alert.WatchReason,
        ObservationSummary = IfBlank(alert.ObservationSummary, $"{alert.SourceCount} source(s) observed the alert condition."),
        AssessmentSummary = IfBlank(alert.AssessmentSummary, $"Corroboration score is {alert.CorroborationScore:0.00}."),
        CorrelationSummary = IfBlank(alert.CorrelationSummary, $"{alert.LinkedNarratives.Count} linked narrative(s) support this alert."),
        Linked = new IndicatorLinkCountsDto
        {
            Narratives = alert.LinkedNarratives.Count,
            Sources = alert.SourceCount,
            Events = alert.EventCount,
            Regions = string.IsNullOrWhiteSpace(alert.LinkedRegion) ? 0 : 1,
            Alerts = 1,
        }
    };

    public static AnalystIndicatorDto FromActor(UnrestActorDto actor) => new()
    {
        Id = actor.ActorId,
        Label = actor.Name,
        Category = "actor",
        IndicatorKind = actor.IndicatorKind,
        EvidenceKind = actor.EvidenceKind,
        AssessmentKind = actor.AssessmentKind,
        Severity = SeverityFromScore(actor.UnrestScore),
        Driver = actor.Driver,
        DriverCode = IfBlank(actor.DriverCode, NormalizeCode(actor.Driver, "actor_influence")),
        Trajectory = actor.Trajectory,
        TrajectoryCode = IfBlank(actor.TrajectoryCode, actor.Trajectory),
        RecommendedAction = actor.RecommendedAction,
        RecommendedActionCode = IfBlank(actor.RecommendedActionCode, NormalizeCode(actor.RecommendedAction, "review_actor")),
        Region = actor.PrimaryRegions.FirstOrDefault(),
        Score = actor.UnrestScore,
        Confidence = actor.InfluenceScore,
        Corroboration = Math.Min(1.0, (actor.EventCount + actor.AlertCount + actor.NarrativeCount) / 8.0),
        Summary = actor.WatchReason,
        ObservationSummary = IfBlank(actor.ObservationSummary, $"{actor.EventCount} event(s) and {actor.AlertCount} alert(s) mention this actor."),
        AssessmentSummary = IfBlank(actor.AssessmentSummary, $"Influence score is {actor.InfluenceScore:0.00}."),
        CorrelationSummary = IfBlank(actor.CorrelationSummary, $"{actor.NarrativeCount} narrative(s) and {actor.PrimaryRegions.Count} region(s) are linked."),
        Linked = new IndicatorLinkCountsDto
        {
            Actors = 1,
            Narratives = actor.NarrativeCount,
            Alerts = actor.AlertCount,
            Regions = actor.PrimaryRegions.Count,
            Events = actor.EventCount,
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

    private static string NormalizeCode(string? value, string fallback)
        => string.IsNullOrWhiteSpace(value)
            ? fallback
            : value.Trim().ToLowerInvariant().Replace("-", "_").Replace(" ", "_");

    private static string IfBlank(string? value, string fallback)
        => string.IsNullOrWhiteSpace(value) ? fallback : value!;
}

public class InfluenceNetworkResponse
{
    [JsonPropertyName("nodes")]       public List<InfluenceNode> Nodes { get; set; } = new();
    [JsonPropertyName("edges")]       public List<InfluenceEdge> Edges { get; set; } = new();
    [JsonPropertyName("node_count")]  public int NodeCount { get; set; }
    [JsonPropertyName("edge_count")]  public int EdgeCount { get; set; }
}

public class InfluenceNode
{
    [JsonPropertyName("id")]              public string? Id            { get; set; }
    [JsonPropertyName("label")]           public string? Label         { get; set; }
    [JsonPropertyName("group")]           public string? Group         { get; set; }
    [JsonPropertyName("type")]            public string? Type          { get; set; }
    [JsonPropertyName("value")]           public double  Value         { get; set; }
    [JsonPropertyName("influence_score")] public double? InfluenceScore { get; set; }
    [JsonPropertyName("mention_count")]   public int     MentionCount  { get; set; }
}

public class InfluenceEdge
{
    [JsonPropertyName("from")]   public string? From   { get; set; }
    [JsonPropertyName("to")]     public string? To     { get; set; }
    [JsonPropertyName("label")]  public string? Label  { get; set; }
    [JsonPropertyName("weight")] public double  Weight { get; set; }
}

public class AgentDto
{
    [JsonPropertyName("agent_id")]     public string AgentId     { get; set; } = "";
    [JsonPropertyName("name")]         public string Name        { get; set; } = "";
    [JsonPropertyName("role")]         public string Role        { get; set; } = "";
    [JsonPropertyName("status")]       public string Status      { get; set; } = "idle";
    [JsonPropertyName("current_task")] public string? CurrentTask { get; set; }
}

public class AgentsListResponse
{
    [JsonPropertyName("agents")] public List<AgentDto> Agents { get; set; } = new();
    [JsonPropertyName("total")]  public int Total { get; set; }
}

public class MissionDto
{
    [JsonPropertyName("mission_id")]  public string MissionId  { get; set; } = "";
    [JsonPropertyName("query")]       public string Query      { get; set; } = "";
    [JsonPropertyName("status")]      public string Status     { get; set; } = "pending";
    [JsonPropertyName("stage")]       public string? Stage     { get; set; }
    [JsonPropertyName("started_at")]  public string? StartedAt { get; set; }
    [JsonPropertyName("finished_at")] public string? FinishedAt { get; set; }
    [JsonPropertyName("error")]       public string? Error     { get; set; }
    [JsonPropertyName("results")]     public Dictionary<string, object>? Results { get; set; }

    // LLM-generated intelligence brief (from results.intelligence_brief)
    public string? IntelligenceBrief =>
        Results != null && Results.TryGetValue("intelligence_brief", out var brief)
            ? brief?.ToString()
            : null;
}

public class MissionsResponse
{
    [JsonPropertyName("missions")] public List<MissionDto> Missions { get; set; } = new();
    [JsonPropertyName("total")]    public int Total { get; set; }
}

public class MissionStartResponse
{
    [JsonPropertyName("mission_id")] public string MissionId { get; set; } = "";
    [JsonPropertyName("status")]     public string Status    { get; set; } = "";
    [JsonPropertyName("query")]      public string Query     { get; set; } = "";
}

public class MissionLogEntry
{
    [JsonPropertyName("timestamp")]  public string Timestamp  { get; set; } = "";
    [JsonPropertyName("agent_id")]   public string AgentId    { get; set; } = "";
    [JsonPropertyName("action")]     public string Action     { get; set; } = "";
    [JsonPropertyName("detail")]     public string? Detail    { get; set; }
    [JsonPropertyName("mission_id")] public string? MissionId { get; set; }
}

public class MissionLogResponse
{
    [JsonPropertyName("entries")] public List<MissionLogEntry> Entries { get; set; } = new();
    [JsonPropertyName("total")]  public int Total { get; set; }
}

public class AuditLogEntry
{
    [JsonPropertyName("id")]         public long Id          { get; set; }
    [JsonPropertyName("user_id")]    public string UserId    { get; set; } = "";
    [JsonPropertyName("user_name")]  public string UserName  { get; set; } = "";
    [JsonPropertyName("action")]     public string Action    { get; set; } = "";
    [JsonPropertyName("resource")]   public string Resource  { get; set; } = "";
    [JsonPropertyName("detail")]     public string? Detail   { get; set; }
    [JsonPropertyName("ip_address")] public string? IpAddress { get; set; }
    [JsonPropertyName("timestamp")]  public string Timestamp { get; set; } = "";
}

public class AuditLogResponse
{
    [JsonPropertyName("total")]   public int Total { get; set; }
    [JsonPropertyName("entries")] public List<AuditLogEntry> Entries { get; set; } = new();
}

public class PipelineStage
{
    [JsonPropertyName("name")]        public string Name        { get; set; } = "";
    [JsonPropertyName("description")] public string Description { get; set; } = "";
    [JsonPropertyName("status")]      public string Status      { get; set; } = "";
    [JsonPropertyName("icon")]        public string Icon        { get; set; } = "";
}

public class DataPipelineResponse
{
    [JsonPropertyName("stages")]           public List<PipelineStage> Stages    { get; set; } = new();
    [JsonPropertyName("python_reachable")] public bool PythonReachable          { get; set; }
    [JsonPropertyName("db_available")]     public bool DbAvailable              { get; set; }
}

public class SystemInfoDto
{
    [JsonPropertyName("platform")]            public string? Platform          { get; set; }
    [JsonPropertyName("environment")]         public string? Environment       { get; set; }
    [JsonPropertyName("dotnet_version")]      public string? DotnetVersion     { get; set; }
    [JsonPropertyName("python_version")]      public string? PythonVersion     { get; set; }
    [JsonPropertyName("total_users")]         public int TotalUsers            { get; set; }
    [JsonPropertyName("total_audit_entries")] public int TotalAuditEntries     { get; set; }
    [JsonPropertyName("neo4j_connected")]     public bool Neo4jConnected       { get; set; }
    [JsonPropertyName("llm_provider")]        public string? LlmProvider       { get; set; }
    [JsonPropertyName("llm_model")]           public string? LlmModel          { get; set; }
    [JsonPropertyName("uptime")]              public string? Uptime            { get; set; }
}

public class SignalDto
{
    [JsonPropertyName("signal_id")]       public string? SignalId       { get; set; }
    [JsonPropertyName("source_event_id")] public string? SourceEventId  { get; set; }
    [JsonPropertyName("source")]          public string? Source         { get; set; }
    [JsonPropertyName("signal_type")]     public string? SignalType     { get; set; }
    [JsonPropertyName("title")]           public string? Title          { get; set; }
    [JsonPropertyName("body")]            public string? Body           { get; set; }
    [JsonPropertyName("timestamp")]       public string? Timestamp      { get; set; }
    [JsonPropertyName("actors")]          public List<string> Actors    { get; set; } = new();
    [JsonPropertyName("location_name")]   public string? LocationName   { get; set; }
    [JsonPropertyName("location_lat")]    public double? LocationLat    { get; set; }
    [JsonPropertyName("location_lon")]    public double? LocationLon    { get; set; }
    [JsonPropertyName("sentiment_score")] public double? SentimentScore { get; set; }
    [JsonPropertyName("confidence")]      public double Confidence      { get; set; }
    [JsonPropertyName("cluster_id")]      public string? ClusterId      { get; set; }
    [JsonPropertyName("indicator")]       public AnalystIndicatorDto? Indicator { get; set; }
}

public class SignalsResponse
{
    [JsonPropertyName("total")]   public int Total { get; set; }
    [JsonPropertyName("signals")] public List<SignalDto> Signals { get; set; } = new();
}

public class SignalClusterDto
{
    [JsonPropertyName("cluster_id")]            public string? ClusterId           { get; set; }
    [JsonPropertyName("signal_count")]          public int SignalCount             { get; set; }
    [JsonPropertyName("sources")]               public List<string> Sources        { get; set; } = new();
    [JsonPropertyName("shared_actors")]         public List<string> SharedActors   { get; set; } = new();
    [JsonPropertyName("composite_score")]       public double CompositeScore       { get; set; }
    [JsonPropertyName("representative_title")]  public string? RepresentativeTitle { get; set; }
    [JsonPropertyName("time_span_hours")]       public double TimeSpanHours        { get; set; }
    [JsonPropertyName("indicator")]             public AnalystIndicatorDto? Indicator { get; set; }
}

public class SignalClustersResponse
{
    [JsonPropertyName("clusters")] public List<SignalClusterDto> Clusters { get; set; } = new();
    [JsonPropertyName("total")]    public int Total { get; set; }
}

public class SourceCatalogResponse
{
    [JsonPropertyName("total")] public int Total { get; set; }
    [JsonPropertyName("sources")] public List<SourceCatalogItemDto> Sources { get; set; } = new();
}

public class SourceCatalogItemDto
{
    [JsonPropertyName("key")] public string Key { get; set; } = "";
    [JsonPropertyName("label")] public string Label { get; set; } = "";
    [JsonPropertyName("category")] public string Category { get; set; } = "";
    [JsonPropertyName("extractor")] public string Extractor { get; set; } = "";
    [JsonPropertyName("modes")] public List<string> Modes { get; set; } = new();
    [JsonPropertyName("supports_query")] public bool SupportsQuery { get; set; }
    [JsonPropertyName("nlp_mode")] public string NlpMode { get; set; } = "";
    [JsonPropertyName("description")] public string Description { get; set; } = "";
    [JsonPropertyName("route")] public string Route { get; set; } = "";
    [JsonPropertyName("aliases")] public List<string> Aliases { get; set; } = new();
    [JsonPropertyName("requires_credentials")] public bool RequiresCredentials { get; set; }
    [JsonPropertyName("parameters")] public List<SourceParameterDto> Parameters { get; set; } = new();
    [JsonPropertyName("health")] public SourceHealthStatusDto Health { get; set; } = new();
}

public class SourceParameterDto
{
    [JsonPropertyName("name")] public string Name { get; set; } = "";
    [JsonPropertyName("kind")] public string Kind { get; set; } = "";
    [JsonPropertyName("required")] public bool Required { get; set; }
    [JsonPropertyName("description")] public string Description { get; set; } = "";
    [JsonPropertyName("default")] public string? Default { get; set; }
}

public class SourceHealthStatusDto
{
    [JsonPropertyName("source")] public string? Source { get; set; }
    [JsonPropertyName("status")] public string? Status { get; set; }
    [JsonPropertyName("sample_count")] public int? SampleCount { get; set; }
    [JsonPropertyName("detail")] public string? Detail { get; set; }
}

public class CorrelationSummaryDto
{
    [JsonPropertyName("total_signals")]     public int TotalSignals    { get; set; }
    [JsonPropertyName("clustered_signals")] public int ClusteredSignals { get; set; }
    [JsonPropertyName("cluster_count")]     public int ClusterCount    { get; set; }
    [JsonPropertyName("avg_cluster_size")]  public double AvgClusterSize { get; set; }
    [JsonPropertyName("top_clusters")]      public List<SignalClusterDto> TopClusters { get; set; } = new();
}

public class ConfidenceDistributionDto
{
    [JsonPropertyName("high")]     public int High     { get; set; }
    [JsonPropertyName("medium")]   public int Medium   { get; set; }
    [JsonPropertyName("low")]      public int Low      { get; set; }
    [JsonPropertyName("unscored")] public int Unscored { get; set; }
    [JsonPropertyName("total")]    public int Total    { get; set; }
}

public class DashboardOverviewDto
{
    [JsonPropertyName("is_admin_view")]            public bool IsAdminView { get; set; }
    [JsonPropertyName("generated_at")]             public string? GeneratedAt { get; set; }
    [JsonPropertyName("situations")]               public SituationOverviewDto Situations { get; set; } = new();
    [JsonPropertyName("events")]                   public List<EventDto> Events { get; set; } = new();
    [JsonPropertyName("live_events")]              public List<EventDto> LiveEvents { get; set; } = new();
    [JsonPropertyName("entities")]                 public EntitiesResponse Entities { get; set; } = new();
    [JsonPropertyName("stats")]                    public StatsDto Stats { get; set; } = new();
    [JsonPropertyName("health")]                   public FullHealthDto Health { get; set; } = new();
    [JsonPropertyName("alert_summary")]            public AlertSummaryDto AlertSummary { get; set; } = new();
    [JsonPropertyName("recent_alerts")]            public List<AlertDto> RecentAlerts { get; set; } = new();
    [JsonPropertyName("narrative_summary")]        public NarrativeSummaryDto NarrativeSummary { get; set; } = new();
    [JsonPropertyName("sentiment_timeline")]       public SentimentTimelineResponse SentimentTimeline { get; set; } = new();
    [JsonPropertyName("jobs")]                     public JobsResponse Jobs { get; set; } = new();
    [JsonPropertyName("swarm")]                    public AgentsListResponse Swarm { get; set; } = new();
    [JsonPropertyName("confidence_distribution")]  public ConfidenceDistributionDto ConfidenceDistribution { get; set; } = new();
    [JsonPropertyName("correlation_summary")]      public CorrelationSummaryDto CorrelationSummary { get; set; } = new();
    [JsonPropertyName("jarvis_insight")]           public string? JarvisInsight { get; set; }
}

public class SituationOverviewDto
{
    [JsonPropertyName("generated_at")] public string? GeneratedAt { get; set; }
    [JsonPropertyName("total")] public int Total { get; set; }
    [JsonPropertyName("situations")] public List<SituationDto> Situations { get; set; } = new();
}

public class SituationDto
{
    [JsonPropertyName("id")] public string? Id { get; set; }
    [JsonPropertyName("title")] public string? Title { get; set; }
    [JsonPropertyName("summary")] public string? Summary { get; set; }
    [JsonPropertyName("event_type")] public string? EventType { get; set; }
    [JsonPropertyName("timestamp")] public string? Timestamp { get; set; }
    [JsonPropertyName("confidence_score")] public double ConfidenceScore { get; set; }
    [JsonPropertyName("priority_score")] public double PriorityScore { get; set; }
    [JsonPropertyName("signal_count")] public int SignalCount { get; set; }
    [JsonPropertyName("supporting_signals")] public List<string> SupportingSignals { get; set; } = new();
    [JsonPropertyName("reasoning")] public string? Reasoning { get; set; }
    [JsonPropertyName("actors")] public List<ActorDto> Actors { get; set; } = new();
    [JsonPropertyName("primary_actor")] public string? PrimaryActor { get; set; }
    [JsonPropertyName("location")] public LocationDto? Location { get; set; }
    [JsonPropertyName("sentiment")] public SentimentDto? Sentiment { get; set; }
    [JsonPropertyName("narrative_tags")] public List<string> NarrativeTags { get; set; } = new();
    [JsonPropertyName("source_mix")] public List<string> SourceMix { get; set; } = new();
}

public class OntologyEventDetailDto
{
    [JsonPropertyName("event")] public EventDto Event { get; set; } = new();
    [JsonPropertyName("situation")] public SituationDto Situation { get; set; } = new();
    [JsonPropertyName("ontology")] public OntologyMetadataDto Ontology { get; set; } = new();
}

public class OntologyActorDetailDto
{
    [JsonPropertyName("id")] public string? Id { get; set; }
    [JsonPropertyName("name")] public string? Name { get; set; }
    [JsonPropertyName("type")] public string? Type { get; set; }
    [JsonPropertyName("mention_count")] public int MentionCount { get; set; }
    [JsonPropertyName("source_count")] public int SourceCount { get; set; }
    [JsonPropertyName("influence_score")] public double? InfluenceScore { get; set; }
    [JsonPropertyName("aliases")] public List<string> Aliases { get; set; } = new();
    [JsonPropertyName("recent_events")] public List<SituationDto> RecentEvents { get; set; } = new();
}

public class OntologyGraphSnapshotDto
{
    [JsonPropertyName("generated_at")] public string? GeneratedAt { get; set; }
    [JsonPropertyName("nodes")] public List<OntologyGraphNodeDto> Nodes { get; set; } = new();
    [JsonPropertyName("edges")] public List<OntologyGraphEdgeDto> Edges { get; set; } = new();
    [JsonPropertyName("node_count")] public int NodeCount { get; set; }
    [JsonPropertyName("edge_count")] public int EdgeCount { get; set; }
}

public class OntologyGraphNodeDto
{
    [JsonPropertyName("id")] public string? Id { get; set; }
    [JsonPropertyName("label")] public string? Label { get; set; }
    [JsonPropertyName("group")] public string? Group { get; set; }
    [JsonPropertyName("entity_type")] public string? EntityType { get; set; }
    [JsonPropertyName("event_type")] public string? EventType { get; set; }
    [JsonPropertyName("confidence_score")] public double? ConfidenceScore { get; set; }
    [JsonPropertyName("value")] public int? Value { get; set; }
}

public class OntologyGraphEdgeDto
{
    [JsonPropertyName("source")] public string Source { get; set; } = "";
    [JsonPropertyName("target")] public string Target { get; set; } = "";
    [JsonPropertyName("relation")] public string Relation { get; set; } = "";
    [JsonPropertyName("weight")] public double Weight { get; set; }
}

public class OntologyMetadataDto
{
    [JsonPropertyName("object_type")] public string? ObjectType { get; set; }
    [JsonPropertyName("relationships")] public List<OntologyRelationshipDto> Relationships { get; set; } = new();
}

public class OntologyRelationshipDto
{
    [JsonPropertyName("type")] public string? Type { get; set; }
    [JsonPropertyName("count")] public int Count { get; set; }
}

public class OperationsOverviewDto
{
    [JsonPropertyName("generated_at")] public string? GeneratedAt { get; set; }
    [JsonPropertyName("threat_posture")] public string ThreatPosture { get; set; } = "steady";
    [JsonPropertyName("total")] public int Total { get; set; }
    [JsonPropertyName("items")] public List<OperationQueueItemDto> Items { get; set; } = new();
}

public class OperationQueueItemDto
{
    [JsonPropertyName("id")] public string? Id { get; set; }
    [JsonPropertyName("title")] public string? Title { get; set; }
    [JsonPropertyName("summary")] public string? Summary { get; set; }
    [JsonPropertyName("event_type")] public string? EventType { get; set; }
    [JsonPropertyName("timestamp")] public string? Timestamp { get; set; }
    [JsonPropertyName("priority_score")] public double PriorityScore { get; set; }
    [JsonPropertyName("risk_score")] public double RiskScore { get; set; }
    [JsonPropertyName("confidence_score")] public double ConfidenceScore { get; set; }
    [JsonPropertyName("signal_count")] public int SignalCount { get; set; }
    [JsonPropertyName("supporting_signals")] public List<string> SupportingSignals { get; set; } = new();
    [JsonPropertyName("source_family_count")] public int SourceFamilyCount { get; set; }
    [JsonPropertyName("actors")] public List<ActorDto> Actors { get; set; } = new();
    [JsonPropertyName("location")] public LocationDto? Location { get; set; }
    [JsonPropertyName("sentiment")] public SentimentDto? Sentiment { get; set; }
    [JsonPropertyName("alerts")] public List<AlertDto> Alerts { get; set; } = new();
    [JsonPropertyName("narratives")] public List<NarrativeDto> Narratives { get; set; } = new();
    [JsonPropertyName("playbook")] public PlaybookRecommendationDto? Playbook { get; set; }
    [JsonPropertyName("courses_of_action")] public List<CourseOfActionDto> CoursesOfAction { get; set; } = new();
    [JsonPropertyName("recommended_next_action")] public string RecommendedNextAction { get; set; } = "";
}

public class PlaybookRecommendationDto
{
    [JsonPropertyName("name")] public string Name { get; set; } = "";
    [JsonPropertyName("status")] public string Status { get; set; } = "";
    [JsonPropertyName("objective")] public string Objective { get; set; } = "";
    [JsonPropertyName("trigger_reason")] public string TriggerReason { get; set; } = "";
    [JsonPropertyName("requires_approval")] public bool RequiresApproval { get; set; }
    [JsonPropertyName("steps")] public List<PlaybookStepDto> Steps { get; set; } = new();
}

public class PlaybookStepDto
{
    [JsonPropertyName("name")] public string Name { get; set; } = "";
    [JsonPropertyName("kind")] public string Kind { get; set; } = "";
    [JsonPropertyName("automated")] public bool Automated { get; set; }
}

public class CourseOfActionDto
{
    [JsonPropertyName("title")] public string Title { get; set; } = "";
    [JsonPropertyName("expected_impact")] public string ExpectedImpact { get; set; } = "";
    [JsonPropertyName("risk")] public string Risk { get; set; } = "";
    [JsonPropertyName("dependencies")] public List<string> Dependencies { get; set; } = new();
}

public class AssetDto
{
    [JsonPropertyName("asset_id")]       public string? AssetId       { get; set; }
    [JsonPropertyName("asset_type")]     public string? AssetType     { get; set; }
    [JsonPropertyName("name")]           public string? Name          { get; set; }
    [JsonPropertyName("callsign")]       public string? Callsign      { get; set; }
    [JsonPropertyName("identifier")]     public string? Identifier    { get; set; }
    [JsonPropertyName("origin_country")] public string? OriginCountry { get; set; }
    [JsonPropertyName("last_lat")]       public double? LastLat       { get; set; }
    [JsonPropertyName("last_lon")]       public double? LastLon       { get; set; }
    [JsonPropertyName("last_altitude")]  public double? LastAltitude  { get; set; }
    [JsonPropertyName("last_speed")]     public double? LastSpeed     { get; set; }
    [JsonPropertyName("last_heading")]   public double? LastHeading   { get; set; }
    [JsonPropertyName("last_seen")]      public string? LastSeen      { get; set; }
    [JsonPropertyName("on_ground")]      public bool? OnGround        { get; set; }
    [JsonPropertyName("profile")]        public AssetProfileDto? Profile { get; set; }
}

public class AssetProfileDto
{
    [JsonPropertyName("title")]       public string? Title       { get; set; }
    [JsonPropertyName("description")] public string? Description { get; set; }
    [JsonPropertyName("extract")]     public string? Extract     { get; set; }
    [JsonPropertyName("source")]      public string? Source      { get; set; }
    [JsonPropertyName("image_url")]   public string? ImageUrl    { get; set; }
    [JsonPropertyName("visual_label")] public string? VisualLabel { get; set; }
    [JsonPropertyName("visual_kind")] public string? VisualKind   { get; set; }
    [JsonPropertyName("external_links")] public List<AssetProfileLinkDto> ExternalLinks { get; set; } = new();
    [JsonPropertyName("facts")]       public List<AssetProfileFactDto> Facts { get; set; } = new();
}

public class AssetProfileLinkDto
{
    [JsonPropertyName("label")] public string? Label { get; set; }
    [JsonPropertyName("url")]   public string? Url   { get; set; }
    [JsonPropertyName("kind")]  public string? Kind  { get; set; }
}

public class AssetProfileFactDto
{
    [JsonPropertyName("label")] public string? Label { get; set; }
    [JsonPropertyName("value")] public string? Value { get; set; }
}

public class AssetsResponse
{
    [JsonPropertyName("total")]  public int Total { get; set; }
    [JsonPropertyName("assets")] public List<AssetDto> Assets { get; set; } = new();
}

public class AssetCountsDto
{
    [JsonPropertyName("total")] public int Total { get; set; }
    [JsonPropertyName("counts")] public AssetCountsBreakdownDto Counts { get; set; } = new();

    [JsonIgnore] public int Aircraft => Counts.Aircraft;
    [JsonIgnore] public int Vessel => Counts.Vessel;
    [JsonIgnore] public int Facility => Counts.Facility;
}

public class AssetCountsBreakdownDto
{
    [JsonPropertyName("aircraft")] public int Aircraft { get; set; }
    [JsonPropertyName("vessel")] public int Vessel { get; set; }
    [JsonPropertyName("facility")] public int Facility { get; set; }
}

public class AssetSnapshotDto
{
    [JsonPropertyName("snapshot_at")] public string? SnapshotAt { get; set; }
    [JsonPropertyName("asset_type")]  public string? AssetType  { get; set; }
    [JsonPropertyName("total")]       public int     Total      { get; set; }
    [JsonPropertyName("assets")]      public List<AssetDto> Assets { get; set; } = new();
}
public static class Ui
{
    public static string FmtSrc(string? s) => s switch
    {
        "newsapi"       => "NewsAPI",
        "gdelt_doc"     => "GDELT",
        "gdelt"         => "GDELT",
        "reddit"        => "Reddit",
        "youtube"       => "YouTube",
        "usgs"          => "USGS",
        "yahoo_finance" => "Yahoo Finance",
        "opensky"       => "OpenSky",
        "hackernews"    => "HackerNews",
        "telegram"      => "Telegram",
        "firms"         => "NASA FIRMS",
        "nws"           => "NWS Weather",
        "who"           => "WHO Health",
        "composite"     => "Composite",
        _               => s?.StartsWith("rss_") == true
                           ? "RSS/" + s[4..].Replace("_", " ")
                           : s?.Replace("_", " ") ?? "--"
    };

    public static string FmtSignalType(string? s) => s switch
    {
        "velocity_spike"             => "Velocity Spike",
        "cross_source_amplification" => "Cross-Source Amp",
        "sentiment_divergence"       => "Sentiment Divergence",
        "actor_coactivation"         => "Actor Co-Activation",
        _                            => s ?? "--"
    };

    public static string FmtAlertType(string? s) => s switch
    {
        "entity_spike"   => "Entity Spike",
        "geo_cluster"    => "Geo Cluster",
        "source_silence" => "Source Silence",
        "sentiment_shift"=> "Sentiment Shift",
        _                => s ?? "--"
    };

    public static string SeverityColor(string? s) => s switch
    {
        "critical" => "#ef4444",
        "high"     => "#f97316",
        "medium"   => "#f59e0b",
        "low"      => "#22c55e",
        _          => "#94a3b8"
    };

    public static string FmtType(string? s) => s switch
    {
        "news"              => "News",
        "disaster"          => "Disaster",
        "market"            => "Market",
        "social"            => "Social",
        "transport"         => "Transport",
        "transport_anomaly" => "Transport Anomaly",
        "video"             => "Video",
        "weather"           => "Weather",
        "health"            => "Health",
        "composite"         => "Composite",
        _                   => s ?? "Unknown"
    };

    public static string FmtStatus(string? s) => s switch
    {
        "pending"  => "Pending",
        "running"  => "Running",
        "done"     => "Done",
        "failed"   => "Failed",
        _          => s ?? "--"
    };

    public static string Ts(string? s) =>
        DateTime.TryParse(s, out var d) ? d.ToString("HH:mm:ss") : s ?? "--";

    public static string TsFull(string? s) =>
        DateTime.TryParse(s, out var d) ? d.ToString("yyyy-MM-dd HH:mm:ss") + " UTC" : s ?? "--";

    public static string FmtN(int n) =>
        n >= 1_000_000 ? (n / 1_000_000.0).ToString("F1") + "M" :
        n >= 1_000     ? (n / 1_000.0).ToString("F1") + "K" :
                         n.ToString();
}

// ── Threat Board ─────────────────────────────────────────────────────────────

public class ThreatZoneDto
{
    [JsonPropertyName("name")]               public string  Name              { get; set; } = "";
    [JsonPropertyName("threat_level")]       public string  ThreatLevel       { get; set; } = "monitoring";
    [JsonPropertyName("dominant_severity")]  public string  DominantSeverity  { get; set; } = "low";
    [JsonPropertyName("score")]              public double  Score             { get; set; }
    [JsonPropertyName("trend")]              public string  Trend             { get; set; } = "stable";
    [JsonPropertyName("alert_count")]        public int     AlertCount        { get; set; }
    [JsonPropertyName("narrative_count")]    public int     NarrativeCount    { get; set; }
    [JsonPropertyName("event_count")]        public int     EventCount        { get; set; }
    [JsonPropertyName("top_signals")]        public List<string> TopSignals   { get; set; } = new();
    [JsonPropertyName("top_actors")]         public List<string> TopActors    { get; set; } = new();
    [JsonPropertyName("location")]           public string? Location          { get; set; }
}

public class ThreatBoardResponse
{
    [JsonPropertyName("generated_at")]   public string  GeneratedAt  { get; set; } = "";
    [JsonPropertyName("hours")]          public int     Hours        { get; set; }
    [JsonPropertyName("overall_level")]  public string  OverallLevel { get; set; } = "monitoring";
    [JsonPropertyName("zones")]          public List<ThreatZoneDto> Zones { get; set; } = new();
    [JsonPropertyName("summary")]        public Dictionary<string, int> Summary { get; set; } = new();
    [JsonPropertyName("db_available")]   public bool    DbAvailable  { get; set; } = true;
}

public class ThreatBoardSummaryDto
{
    [JsonPropertyName("overall_level")] public string  OverallLevel { get; set; } = "monitoring";
    [JsonPropertyName("summary")]       public Dictionary<string, int> Summary { get; set; } = new();
    [JsonPropertyName("zone_count")]    public int     ZoneCount    { get; set; }
    [JsonPropertyName("generated_at")] public string  GeneratedAt  { get; set; } = "";
}

public class CopilotAskDto
{
    [JsonPropertyName("question")]     public string Question    { get; set; } = "";
    [JsonPropertyName("event_id")]     public string? EventId    { get; set; }
    [JsonPropertyName("actor_id")]     public string? ActorId    { get; set; }
    [JsonPropertyName("narrative_id")] public string? NarrativeId { get; set; }
    [JsonPropertyName("analyst")]      public string Analyst     { get; set; } = "analyst";
}

public class CopilotResponseDto
{
    [JsonPropertyName("question")]           public string Question    { get; set; } = "";
    [JsonPropertyName("answer")]             public string Answer      { get; set; } = "";
    [JsonPropertyName("llm_used")]           public bool   LlmUsed     { get; set; }
    [JsonPropertyName("model")]              public string? Model      { get; set; }
    [JsonPropertyName("context_summary")]    public CopilotContextSummary? Context { get; set; }
}

public class CopilotContextSummary
{
    [JsonPropertyName("event_id")]              public string? EventId              { get; set; }
    [JsonPropertyName("has_event")]             public bool    HasEvent             { get; set; }
    [JsonPropertyName("past_decisions_count")]  public int     PastDecisionsCount   { get; set; }
    [JsonPropertyName("alert_count")]           public int     AlertCount           { get; set; }
    [JsonPropertyName("narrative_count")]       public int     NarrativeCount       { get; set; }
    [JsonPropertyName("actor_count")]           public int     ActorCount           { get; set; }
    [JsonPropertyName("similar_event_count")]   public int     SimilarEventCount    { get; set; }
}

public class CopilotExplainDto
{
    [JsonPropertyName("event_id")]     public string EventId   { get; set; } = "";
    [JsonPropertyName("event_title")]  public string EventTitle { get; set; } = "";
    [JsonPropertyName("risk_score")]   public double RiskScore  { get; set; }
    [JsonPropertyName("briefing")]     public string Briefing   { get; set; } = "";
    [JsonPropertyName("evidence")]     public CopilotContextSummary? Evidence { get; set; }
    [JsonPropertyName("llm_used")]     public bool   LlmUsed    { get; set; }
    [JsonPropertyName("model")]        public string? Model     { get; set; }
}

public class CopilotRecommendDto
{
    [JsonPropertyName("event_id")]               public string  EventId              { get; set; } = "";
    [JsonPropertyName("risk_score")]             public double  RiskScore            { get; set; }
    [JsonPropertyName("primary_recommendation")] public string  PrimaryRecommendation { get; set; } = "";
    [JsonPropertyName("historical_precedent")]   public string  HistoricalPrecedent  { get; set; } = "";
    [JsonPropertyName("confidence")]             public string  Confidence           { get; set; } = "low";
    [JsonPropertyName("reasoning")]              public string  Reasoning            { get; set; } = "";
    [JsonPropertyName("evidence")]               public CopilotContextSummary? Evidence { get; set; }
    [JsonPropertyName("ai_recommendation")]      public string? AiRecommendation     { get; set; }
    [JsonPropertyName("llm_used")]               public bool    LlmUsed              { get; set; }
}

public class CopilotSimilarDto
{
    [JsonPropertyName("event_id")]          public string EventId       { get; set; } = "";
    [JsonPropertyName("event_type")]        public string EventType     { get; set; } = "";
    [JsonPropertyName("similar_decisions")] public List<DecisionDto> SimilarDecisions { get; set; } = new();
    [JsonPropertyName("total")]             public int    Total         { get; set; }
    [JsonPropertyName("insight")]           public string Insight       { get; set; } = "";
}

public class RecordOutcomeDto
{
    [JsonPropertyName("outcome")]       public string  Outcome      { get; set; } = "inconclusive";
    [JsonPropertyName("outcome_notes")] public string? OutcomeNotes { get; set; }
}

public class PlaybookRunDto
{
    [JsonPropertyName("id")]              public string   Id             { get; set; } = "";
    [JsonPropertyName("event_id")]        public string   EventId        { get; set; } = "";
    [JsonPropertyName("playbook_name")]   public string   PlaybookName   { get; set; } = "";
    [JsonPropertyName("objective")]       public string?  Objective      { get; set; }
    [JsonPropertyName("analyst")]         public string   Analyst        { get; set; } = "";
    [JsonPropertyName("status")]          public string   Status         { get; set; } = "in_progress";
    [JsonPropertyName("steps_total")]     public int      StepsTotal     { get; set; }
    [JsonPropertyName("steps_done")]      public int      StepsDone      { get; set; }
    [JsonPropertyName("started_at")]      public string?  StartedAt      { get; set; }
    [JsonPropertyName("completed_at")]    public string?  CompletedAt    { get; set; }
    [JsonPropertyName("outcome_summary")] public string?  OutcomeSummary { get; set; }
}

public class LlmProviderConfigDto
{
    [JsonPropertyName("id")] public int Id { get; set; }
    [JsonPropertyName("provider")] public string Provider { get; set; } = "";
    [JsonPropertyName("model")] public string Model { get; set; } = "";
    [JsonPropertyName("base_url")] public string? BaseUrl { get; set; }
    [JsonPropertyName("is_enabled")] public bool IsEnabled { get; set; }
    [JsonPropertyName("is_default")] public bool IsDefault { get; set; }
    [JsonPropertyName("updated_at")] public string? UpdatedAt { get; set; }
    [JsonPropertyName("last_tested_at")] public string? LastTestedAt { get; set; }
    [JsonPropertyName("last_test_succeeded")] public bool? LastTestSucceeded { get; set; }
    [JsonPropertyName("last_test_message")] public string? LastTestMessage { get; set; }
    [JsonPropertyName("api_key_masked")] public string ApiKeyMasked { get; set; } = "";
}

public class LlmProvidersResponse
{
    [JsonPropertyName("providers")] public List<LlmProviderConfigDto> Providers { get; set; } = new();
    [JsonPropertyName("runtime")] public RuntimeLlmDto? Runtime { get; set; }
    [JsonPropertyName("supported_providers")] public List<LlmProviderOptionDto> SupportedProviders { get; set; } = new();
}

public class RuntimeLlmDto
{
    [JsonPropertyName("provider")] public string? Provider { get; set; }
    [JsonPropertyName("model")] public string? Model { get; set; }
    [JsonPropertyName("base_url")] public string? BaseUrl { get; set; }
    [JsonPropertyName("available")] public bool Available { get; set; }
    [JsonPropertyName("runtime_source")] public string? RuntimeSource { get; set; }
    [JsonPropertyName("supported_providers")] public List<LlmProviderOptionDto> SupportedProviders { get; set; } = new();
}

public class LlmProviderOptionDto
{
    [JsonPropertyName("key")] public string Key { get; set; } = "";
    [JsonPropertyName("label")] public string Label { get; set; } = "";
    [JsonPropertyName("aliases")] public List<string> Aliases { get; set; } = new();
    [JsonPropertyName("default_model")] public string DefaultModel { get; set; } = "";
    [JsonPropertyName("default_base_url")] public string DefaultBaseUrl { get; set; } = "";
    [JsonPropertyName("requires_api_key")] public bool RequiresApiKey { get; set; }
    [JsonPropertyName("api_key_label")] public string ApiKeyLabel { get; set; } = "";
}

public class UpsertLlmProviderDto
{
    [JsonPropertyName("provider")] public string Provider { get; set; } = "openai";
    [JsonPropertyName("model")] public string Model { get; set; } = "";
    [JsonPropertyName("base_url")] public string? BaseUrl { get; set; }
    [JsonPropertyName("api_key")] public string ApiKey { get; set; } = "";
    [JsonPropertyName("is_enabled")] public bool IsEnabled { get; set; } = true;
    [JsonPropertyName("is_default")] public bool IsDefault { get; set; } = true;
}

public class EventSocialDto
{
    [JsonPropertyName("event_id")]     public string? EventId    { get; set; }
    [JsonPropertyName("query")]        public string? Query      { get; set; }
    [JsonPropertyName("posts")]        public List<EventDto> Posts { get; set; } = new();
    [JsonPropertyName("narrative")]    public SocialNarrativeDto? Narrative { get; set; }
    [JsonPropertyName("narrative_db")] public List<NarrativeSignalDto> NarrativeDb { get; set; } = new();
    [JsonPropertyName("generated_at")] public string? GeneratedAt { get; set; }
}

public class SocialNarrativeDto
{
    [JsonPropertyName("label")]      public string? Label      { get; set; }
    [JsonPropertyName("confidence")] public double  Confidence { get; set; }
    [JsonPropertyName("signals")]    public List<SocialNarrativeSignalDto> Signals { get; set; } = new();
}

public class SocialNarrativeSignalDto
{
    [JsonPropertyName("type")]       public string? Type       { get; set; }
    [JsonPropertyName("label")]      public string? Label      { get; set; }
    [JsonPropertyName("confidence")] public double  Confidence { get; set; }
    [JsonPropertyName("detail")]     public string? Detail     { get; set; }
}


public class LlmTestResponse
{
    [JsonPropertyName("status")] public string? Status { get; set; }
    [JsonPropertyName("message")] public string? Message { get; set; }
    [JsonPropertyName("model_used")] public string? ModelUsed { get; set; }
    [JsonPropertyName("latency_ms")] public int? LatencyMs { get; set; }
    [JsonPropertyName("result")] public LlmTestResultDto? Result { get; set; }
}

public class LlmTestResultDto
{
    [JsonPropertyName("ok")] public bool Ok { get; set; }
    [JsonPropertyName("detail")] public string? Detail { get; set; }
    [JsonPropertyName("model_used")] public string? ModelUsed { get; set; }
    [JsonPropertyName("latency_ms")] public int? LatencyMs { get; set; }
}

public class AgentLlmStatusDto
{
    [JsonPropertyName("provider")] public string? Provider { get; set; }
    [JsonPropertyName("model")] public string? Model { get; set; }
    [JsonPropertyName("available")] public bool Available { get; set; }
}

public class NarrativeForecastDto
{
    [JsonPropertyName("narrative_id")] public string? NarrativeId { get; set; }
    [JsonPropertyName("horizon")] public int? Horizon { get; set; }
    [JsonPropertyName("method")] public string? Method { get; set; }
    [JsonPropertyName("confidence")] public double Confidence { get; set; }
    [JsonPropertyName("history")] public List<double> History { get; set; } = new();
    [JsonPropertyName("forecast")] public List<double> Forecast { get; set; } = new();
    [JsonPropertyName("lower")] public List<double> Lower { get; set; } = new();
    [JsonPropertyName("upper")] public List<double> Upper { get; set; } = new();
}

public class EventFullDto
{
    [JsonPropertyName("event_id")] public string? EventId { get; set; }
    [JsonPropertyName("context")] public EventContextEnvelopeDto? Context { get; set; }
    [JsonPropertyName("intelligence")] public EventIntelligenceDto? Intelligence { get; set; }
    [JsonPropertyName("social")] public EventSocialDto? Social { get; set; }
    [JsonPropertyName("similar")] public CopilotSimilarDto? Similar { get; set; }
    [JsonPropertyName("explain")] public CopilotExplainDto? Explain { get; set; }
    [JsonPropertyName("recommend")] public CopilotRecommendDto? Recommend { get; set; }
    [JsonPropertyName("fetched_at")] public string? FetchedAt { get; set; }
}

public class EventContextEnvelopeDto
{
    [JsonPropertyName("event")] public EventDto Event { get; set; } = new();
    [JsonPropertyName("situation")] public SituationDto Situation { get; set; } = new();
    [JsonPropertyName("divergence")] public EventContextDivergenceDto Divergence { get; set; } = new();
    [JsonPropertyName("amplification")] public EventContextAmplificationDto Amplification { get; set; } = new();
    [JsonPropertyName("actors")] public List<string> Actors { get; set; } = new();
    [JsonPropertyName("related_events")] public List<EventContextRelatedEventDto> RelatedEvents { get; set; } = new();
    [JsonPropertyName("narratives")] public List<NarrativeDto> Narratives { get; set; } = new();
    [JsonPropertyName("alerts")] public List<AlertDto> Alerts { get; set; } = new();
    [JsonPropertyName("playbooks")] public List<PlaybookDefinitionDto> Playbooks { get; set; } = new();
    [JsonPropertyName("forecast")] public NarrativeForecastDto? Forecast { get; set; }
}

public class EventContextDivergenceDto
{
    [JsonPropertyName("score")] public double Score { get; set; }
    [JsonPropertyName("samples")] public int Samples { get; set; }
}

public class EventContextAmplificationDto
{
    [JsonPropertyName("source_families")] public int SourceFamilies { get; set; }
    [JsonPropertyName("event_count")] public int EventCount { get; set; }
    [JsonPropertyName("narrative_strength")] public double NarrativeStrength { get; set; }
    [JsonPropertyName("tier")] public string Tier { get; set; } = "normal";
}

public class EventContextRelatedEventDto
{
    [JsonPropertyName("event_id")] public string? EventId { get; set; }
    [JsonPropertyName("source")] public string? Source { get; set; }
    [JsonPropertyName("event_type")] public string? EventType { get; set; }
    [JsonPropertyName("title")] public string? Title { get; set; }
    [JsonPropertyName("timestamp")] public string? Timestamp { get; set; }
    [JsonPropertyName("url")] public string? Url { get; set; }
    [JsonPropertyName("sentiment_score")] public double? SentimentScore { get; set; }
}

public class PlaybooksResponse
{
    [JsonPropertyName("total")] public int Total { get; set; }
    [JsonPropertyName("playbooks")] public List<PlaybookDefinitionDto> Playbooks { get; set; } = new();
}

public class PlaybookDefinitionDto
{
    [JsonPropertyName("id")] public string Id { get; set; } = "";
    [JsonPropertyName("name")] public string Name { get; set; } = "";
    [JsonPropertyName("objective")] public string Objective { get; set; } = "";
    [JsonPropertyName("trigger")] public Dictionary<string, object?> Trigger { get; set; } = new();
    [JsonPropertyName("requires_approval")] public bool RequiresApproval { get; set; }
    [JsonPropertyName("status")] public string Status { get; set; } = "ready";
    [JsonPropertyName("steps")] public List<PlaybookDefinitionStepDto> Steps { get; set; } = new();
}

public class PlaybookDefinitionStepDto
{
    [JsonPropertyName("action")] public string Action { get; set; } = "";
    [JsonPropertyName("params")] public Dictionary<string, object?> Params { get; set; } = new();
    [JsonPropertyName("automated")] public bool Automated { get; set; }
}

public class PlaybookExecutionResponseDto
{
    [JsonPropertyName("playbook_id")] public string PlaybookId { get; set; } = "";
    [JsonPropertyName("name")] public string Name { get; set; } = "";
    [JsonPropertyName("status")] public string Status { get; set; } = "";
    [JsonPropertyName("results")] public List<Dictionary<string, object?>> Results { get; set; } = new();
}

public class SignalStatsDto
{
    [JsonPropertyName("total")] public int Total { get; set; }
    [JsonPropertyName("clustered")] public int Clustered { get; set; }
    [JsonPropertyName("cluster_count")] public int ClusterCount { get; set; }
    [JsonPropertyName("source_count")] public int SourceCount { get; set; }
    [JsonPropertyName("_served_from")] public string? ServedFrom { get; set; }
}

public class DeadLetterQueueResponse
{
    [JsonPropertyName("events")] public List<Dictionary<string, object?>> Events { get; set; } = new();
    [JsonPropertyName("size")] public int Size { get; set; }
    [JsonPropertyName("note")] public string? Note { get; set; }
    [JsonPropertyName("error")] public string? Error { get; set; }
}

public class DeadLetterQueueRetryResponse
{
    [JsonPropertyName("status")] public string Status { get; set; } = "";
    [JsonPropertyName("event")] public Dictionary<string, object?>? Event { get; set; }
}

public class TriggerLiveResponse
{
    [JsonPropertyName("status")] public string Status { get; set; } = "";
    [JsonPropertyName("job_ids")] public List<string> JobIds { get; set; } = new();
}

public class EscalationScoreDto
{
    [JsonPropertyName("region")]       public string Region        { get; set; } = "";
    [JsonPropertyName("score")]        public double Score         { get; set; }
    [JsonPropertyName("risk_level")]   public string RiskLevel     { get; set; } = "";
    [JsonPropertyName("drivers")]      public List<string> Drivers { get; set; } = new();
    [JsonPropertyName("confidence")]   public double Confidence    { get; set; }
    [JsonPropertyName("event_count")]  public int EventCount       { get; set; }
    [JsonPropertyName("computed_at")]  public string? ComputedAt   { get; set; }
}

public class EscalationResponse
{
    [JsonPropertyName("scores")]       public List<EscalationScoreDto> Scores { get; set; } = new();
    [JsonPropertyName("generated_at")] public string? GeneratedAt              { get; set; }
}

public class BotScoreDto
{
    [JsonPropertyName("actor_name")]  public string ActorName  { get; set; } = "";
    [JsonPropertyName("actor_id")]    public string ActorId    { get; set; } = "";
    [JsonPropertyName("bot_score")]   public double BotScore   { get; set; }
    [JsonPropertyName("risk_level")]  public string RiskLevel  { get; set; } = "";
    [JsonPropertyName("signals")]     public Dictionary<string, double> Signals { get; set; } = new();
    [JsonPropertyName("event_count")] public int EventCount    { get; set; }
    [JsonPropertyName("sources")]     public List<string> Sources { get; set; } = new();
    [JsonPropertyName("computed_at")] public string? ComputedAt { get; set; }
}

public class BotScoresResponse
{
    [JsonPropertyName("total")]     public int Total    { get; set; }
    [JsonPropertyName("high_risk")] public int HighRisk { get; set; }
    [JsonPropertyName("actors")]    public List<BotScoreDto> Actors { get; set; } = new();
}

public class SourceCredibilityDto
{
    [JsonPropertyName("source_key")]        public string SourceKey        { get; set; } = "";
    [JsonPropertyName("display_name")]      public string DisplayName      { get; set; } = "";
    [JsonPropertyName("credibility_score")] public double CredibilityScore { get; set; }
    [JsonPropertyName("tier")]              public int Tier                { get; set; }
    [JsonPropertyName("penalty_count")]     public int PenaltyCount        { get; set; }
    [JsonPropertyName("boost_count")]       public int BoostCount          { get; set; }
    [JsonPropertyName("last_computed")]     public string? LastComputed    { get; set; }
}

public class CredibilityResponse
{
    [JsonPropertyName("sources")] public List<SourceCredibilityDto> Sources { get; set; } = new();
}

public class CausalityPointDto
{
    [JsonPropertyName("hour")]  public string Hour  { get; set; } = "";
    [JsonPropertyName("value")] public double Value { get; set; }
}

public class CausalityResponse
{
    [JsonPropertyName("series_a")]        public string SeriesA          { get; set; } = "";
    [JsonPropertyName("series_b")]        public string SeriesB          { get; set; } = "";
    [JsonPropertyName("lag_hours")]       public int LagHours            { get; set; }
    [JsonPropertyName("granger_p_value")] public double? GrangerPValue   { get; set; }
    [JsonPropertyName("significant")]     public bool Significant        { get; set; }
    [JsonPropertyName("interpretation")]  public string Interpretation   { get; set; } = "";
    [JsonPropertyName("time_series_a")]   public List<CausalityPointDto> TimeSeriesA { get; set; } = new();
    [JsonPropertyName("time_series_b")]   public List<CausalityPointDto> TimeSeriesB { get; set; } = new();
    [JsonPropertyName("error")]           public string? Error           { get; set; }
}

public class CommunityNodeDto
{
    [JsonPropertyName("id")]           public string Id         { get; set; } = "";
    [JsonPropertyName("label")]        public string Label      { get; set; } = "";
    [JsonPropertyName("community_id")] public int? CommunityId  { get; set; }
}

public class CommunityEdgeDto
{
    [JsonPropertyName("from")]     public string From     { get; set; } = "";
    [JsonPropertyName("to")]       public string To       { get; set; } = "";
    [JsonPropertyName("weight")]   public int Weight      { get; set; }
    [JsonPropertyName("first_co")] public string? FirstCo { get; set; }
    [JsonPropertyName("last_co")]  public string? LastCo  { get; set; }
}

public class CommunityGraphDto
{
    [JsonPropertyName("nodes")] public List<CommunityNodeDto> Nodes { get; set; } = new();
    [JsonPropertyName("edges")] public List<CommunityEdgeDto> Edges { get; set; } = new();
}

public class CommunityGraphResponse
{
    [JsonPropertyName("communities")]    public int Communities          { get; set; }
    [JsonPropertyName("actor_count")]    public int ActorCount           { get; set; }
    [JsonPropertyName("temporal_graph")] public CommunityGraphDto? TemporalGraph { get; set; }
    [JsonPropertyName("generated_at")]   public string? GeneratedAt     { get; set; }
}

public class TriageRecordDto
{
    [JsonPropertyName("id")] public int Id { get; set; }
    [JsonPropertyName("eventId")] public string EventId { get; set; } = "";
    [JsonPropertyName("title")] public string Title { get; set; } = "";
    [JsonPropertyName("source")] public string Source { get; set; } = "";
    [JsonPropertyName("eventType")] public string EventType { get; set; } = "";
    [JsonPropertyName("status")] public string Status { get; set; } = "new";
    [JsonPropertyName("priority")] public string Priority { get; set; } = "medium";
    [JsonPropertyName("riskScore")] public double? RiskScore { get; set; }
    [JsonPropertyName("confidenceScore")] public double? ConfidenceScore { get; set; }
    [JsonPropertyName("analystUserId")] public string? AnalystUserId { get; set; }
    [JsonPropertyName("analystDisplayName")] public string? AnalystDisplayName { get; set; }
    [JsonPropertyName("note")] public string? Note { get; set; }
    [JsonPropertyName("sourceUrl")] public string? SourceUrl { get; set; }
    [JsonPropertyName("region")] public string? Region { get; set; }
    [JsonPropertyName("similarEventCount")] public int SimilarEventCount { get; set; }
    [JsonPropertyName("relatedActorCount")] public int RelatedActorCount { get; set; }
    [JsonPropertyName("lastSeenAt")] public string? LastSeenAt { get; set; }
    [JsonPropertyName("createdAt")] public string? CreatedAt { get; set; }
    [JsonPropertyName("updatedAt")] public string? UpdatedAt { get; set; }
}

public class TriageCandidateDto
{
    [JsonPropertyName("eventId")] public string EventId { get; set; } = "";
    [JsonPropertyName("title")] public string Title { get; set; } = "";
    [JsonPropertyName("source")] public string Source { get; set; } = "";
    [JsonPropertyName("eventType")] public string EventType { get; set; } = "";
    [JsonPropertyName("riskScore")] public double? RiskScore { get; set; }
    [JsonPropertyName("confidenceScore")] public double? ConfidenceScore { get; set; }
    [JsonPropertyName("timestamp")] public string? Timestamp { get; set; }
    [JsonPropertyName("sourceUrl")] public string? SourceUrl { get; set; }
    [JsonPropertyName("region")] public string? Region { get; set; }
    [JsonPropertyName("status")] public string Status { get; set; } = "new";
    [JsonPropertyName("priority")] public string Priority { get; set; } = "medium";
    [JsonPropertyName("analystDisplayName")] public string? AnalystDisplayName { get; set; }
    [JsonPropertyName("similarEventCount")] public int SimilarEventCount { get; set; }
    [JsonPropertyName("relatedActorCount")] public int RelatedActorCount { get; set; }
}

public class TriageSummaryDto
{
    [JsonPropertyName("total")] public int Total { get; set; }
    [JsonPropertyName("new")] public int New { get; set; }
    [JsonPropertyName("reviewing")] public int Reviewing { get; set; }
    [JsonPropertyName("escalated")] public int Escalated { get; set; }
    [JsonPropertyName("actioned")] public int Actioned { get; set; }
    [JsonPropertyName("dismissed")] public int Dismissed { get; set; }
}

public class TriageListResponse<T>
{
    [JsonPropertyName("total")] public int Total { get; set; }
    [JsonPropertyName("items")] public List<T> Items { get; set; } = new();
}

public class UpsertTriageDto
{
    [JsonPropertyName("eventId")] public string EventId { get; set; } = "";
    [JsonPropertyName("title")] public string Title { get; set; } = "";
    [JsonPropertyName("source")] public string? Source { get; set; }
    [JsonPropertyName("eventType")] public string? EventType { get; set; }
    [JsonPropertyName("riskScore")] public double? RiskScore { get; set; }
    [JsonPropertyName("confidenceScore")] public double? ConfidenceScore { get; set; }
    [JsonPropertyName("status")] public string Status { get; set; } = "new";
    [JsonPropertyName("priority")] public string Priority { get; set; } = "medium";
    [JsonPropertyName("note")] public string? Note { get; set; }
    [JsonPropertyName("sourceUrl")] public string? SourceUrl { get; set; }
    [JsonPropertyName("region")] public string? Region { get; set; }
    [JsonPropertyName("similarEventCount")] public int SimilarEventCount { get; set; }
    [JsonPropertyName("relatedActorCount")] public int RelatedActorCount { get; set; }
}

// ── Airspace / Domain Intelligence ──────────────────────────────────────────

public class AirspaceClosureItem
{
    [JsonPropertyName("id")]          public string? Id          { get; set; }
    [JsonPropertyName("name")]        public string? Name        { get; set; }
    [JsonPropertyName("identifier")]  public string? Identifier  { get; set; }
    [JsonPropertyName("title")]       public string  Title       { get; set; } = "";
    [JsonPropertyName("description")] public string? Description { get; set; }
    [JsonPropertyName("reason")]      public string? Reason      { get; set; }
    [JsonPropertyName("type")]        public string? Type        { get; set; }
    [JsonPropertyName("status")]      public string? Status      { get; set; }
    [JsonPropertyName("active")]      public bool? Active        { get; set; }
    [JsonPropertyName("start")]       public string? Start       { get; set; }
    [JsonPropertyName("end")]         public string? End         { get; set; }
    [JsonPropertyName("lat_min")]     public double? LatMin      { get; set; }
    [JsonPropertyName("lon_min")]     public double? LonMin      { get; set; }
    [JsonPropertyName("lat_max")]     public double? LatMax      { get; set; }
    [JsonPropertyName("lon_max")]     public double? LonMax      { get; set; }
}

public class AirspaceClosuresResponse
{
    [JsonPropertyName("generated_at")] public string?                   GeneratedAt { get; set; }
    [JsonPropertyName("total")]        public int                        Total       { get; set; }
    [JsonPropertyName("closures")]     public List<AirspaceClosureItem>  Closures    { get; set; } = new();
    [JsonPropertyName("note")]         public string?                    Note        { get; set; }
    [JsonPropertyName("_cached")]      public bool                       Cached      { get; set; }
}

public class JammingTile
{
    [JsonPropertyName("lat")]       public double  Lat       { get; set; }
    [JsonPropertyName("lon")]       public double  Lon       { get; set; }
    [JsonPropertyName("lat_min")]   public double? LatMin    { get; set; }
    [JsonPropertyName("lon_min")]   public double? LonMin    { get; set; }
    [JsonPropertyName("lat_max")]   public double? LatMax    { get; set; }
    [JsonPropertyName("lon_max")]   public double? LonMax    { get; set; }
    [JsonPropertyName("count")]     public int     Count     { get; set; }
    [JsonPropertyName("intensity")] public double? Intensity { get; set; }
    [JsonPropertyName("density")]   public double? Density   { get; set; }
}

public class JammingHeatmapResponse
{
    [JsonPropertyName("generated_at")]   public string?         GeneratedAt  { get; set; }
    [JsonPropertyName("window_hours")]   public int             WindowHours  { get; set; }
    [JsonPropertyName("tiles")]          public List<JammingTile> Tiles      { get; set; } = new();
    [JsonPropertyName("note")]           public string?         Note         { get; set; }
}

public class RerouteEventItem
{
    [JsonPropertyName("event_id")]   public string? EventId   { get; set; }
    [JsonPropertyName("title")]      public string? Title     { get; set; }
    [JsonPropertyName("source")]     public string? Source    { get; set; }
    [JsonPropertyName("lat")]        public double? Lat       { get; set; }
    [JsonPropertyName("lon")]        public double? Lon       { get; set; }
    [JsonPropertyName("timestamp")]  public string? Timestamp { get; set; }
    [JsonPropertyName("risk_score")] public double? RiskScore { get; set; }
}

public class ReroutesResponse
{
    [JsonPropertyName("generated_at")] public string?                  GeneratedAt { get; set; }
    [JsonPropertyName("total")]        public int                       Total       { get; set; }
    [JsonPropertyName("events")]       public List<RerouteEventItem>    Events      { get; set; } = new();
    [JsonPropertyName("note")]         public string?                   Note        { get; set; }
}

public class SatellitePass
{
    [JsonPropertyName("sat_name")]   public string? SatName   { get; set; }
    [JsonPropertyName("sat_id")]     public string? SatId     { get; set; }
    [JsonPropertyName("norad_id")]   public string? NoradId   { get; set; }
    [JsonPropertyName("aos")]        public string? Aos        { get; set; }
    [JsonPropertyName("los")]        public string? Los        { get; set; }
    [JsonPropertyName("max_el")]     public double? MaxEl      { get; set; }
    [JsonPropertyName("duration_s")] public double? DurationS  { get; set; }
    [JsonPropertyName("lat")]        public double? Lat        { get; set; }
    [JsonPropertyName("lon")]        public double? Lon        { get; set; }
    [JsonPropertyName("points")]     public List<SatellitePassPoint> Points { get; set; } = new();
}

public class SatellitePassPoint
{
    [JsonPropertyName("lat")]  public double Lat  { get; set; }
    [JsonPropertyName("lon")]  public double Lon  { get; set; }
    [JsonPropertyName("time")] public string? Time { get; set; }
}

public class SatellitePassesResponse
{
    [JsonPropertyName("generated_at")] public string?               GeneratedAt { get; set; }
    [JsonPropertyName("total")]        public int                    Total       { get; set; }
    [JsonPropertyName("passes")]       public List<SatellitePass>    Passes      { get; set; } = new();
    [JsonPropertyName("note")]         public string?                Note        { get; set; }
}
