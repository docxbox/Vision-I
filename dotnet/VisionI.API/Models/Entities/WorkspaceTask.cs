namespace VisionI.API.Models.Entities;

public class WorkspaceTask
{
    public Guid Id { get; set; }
    public Guid WorkspaceId { get; set; }
    public string Title { get; set; } = "";
    public string? Description { get; set; }
    public string Status { get; set; } = "open";       // open|in_progress|done|cancelled
    public string Priority { get; set; } = "medium";   // low|medium|high|critical
    public string? CreatedByUserId { get; set; }
    public string? AssigneeUserId { get; set; }
    public string? AssigneeDisplayName { get; set; }
    public DateTime CreatedAt { get; set; }
    public DateTime UpdatedAt { get; set; }
    public DateTime? CompletedAt { get; set; }

    public Workspace Workspace { get; set; } = null!;
}
