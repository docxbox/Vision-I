namespace VisionI.API.Models.Entities;

/// <summary>
/// An item an analyst has pinned into a workspace as curated evidence — an event,
/// asset, entity, signal, or narrative. The Title/Source are snapshotted at pin time
/// so the board stays readable even if the underlying item ages out of a feed.
/// </summary>
public class WorkspaceEvidence
{
    public Guid Id { get; set; }
    public Guid WorkspaceId { get; set; }
    public string ItemType { get; set; } = "event";   // event|asset|entity|signal|narrative
    public string ItemId { get; set; } = "";
    public string Title { get; set; } = "";
    public string? Source { get; set; }
    public string? Note { get; set; }
    public string? PinnedByUserId { get; set; }
    public string? PinnedByDisplayName { get; set; }
    public DateTime CreatedAt { get; set; }

    public Workspace Workspace { get; set; } = null!;
}
