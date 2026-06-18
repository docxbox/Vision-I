namespace VisionI.API.Models.Entities;

public sealed class EventTriageRecord
{
    public int Id { get; set; }
    public string EventId { get; set; } = "";
    public string Title { get; set; } = "";
    public string Source { get; set; } = "";
    public string EventType { get; set; } = "";
    public string Status { get; set; } = "new";
    public string Priority { get; set; } = "medium";
    public double? RiskScore { get; set; }
    public double? ConfidenceScore { get; set; }
    public string? AnalystUserId { get; set; }
    public string? AnalystDisplayName { get; set; }
    public string? Note { get; set; }
    public string? SourceUrl { get; set; }
    public string? Region { get; set; }
    public int SimilarEventCount { get; set; }
    public int RelatedActorCount { get; set; }
    public DateTime LastSeenAt { get; set; } = DateTime.UtcNow;
    public DateTime CreatedAt { get; set; } = DateTime.UtcNow;
    public DateTime UpdatedAt { get; set; } = DateTime.UtcNow;
}
