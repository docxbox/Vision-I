namespace VisionI.API.Models.Entities;

public class Workspace
{
    public Guid Id { get; set; }
    public string Slug { get; set; } = "";
    public string Title { get; set; } = "";
    public string? Description { get; set; }
    public string Status { get; set; } = "active";
    public string? Classification { get; set; }
    public int DefaultWindowHours { get; set; } = 24;
    public string? Theme { get; set; }
    public string? CreatedBy { get; set; }
    public string Visibility { get; set; } = "private";  // private|team|public
    public DateTime CreatedAt { get; set; }
    public DateTime UpdatedAt { get; set; }

    public List<WorkspaceGeoFilter> GeoFilters { get; set; } = [];
    public List<WorkspaceTask> Tasks { get; set; } = [];
    public List<WorkspaceQuery> Queries { get; set; } = [];
    public List<WorkspaceEntity> Entities { get; set; } = [];
    public List<WorkspaceSourceProfile> SourceProfiles { get; set; } = [];
}

public class WorkspaceGeoFilter
{
    public Guid Id { get; set; }
    public Guid WorkspaceId { get; set; }
    public string FilterType { get; set; } = "bbox";
    public string Name { get; set; } = "primary";
    public double? MinLat { get; set; }
    public double? MaxLat { get; set; }
    public double? MinLon { get; set; }
    public double? MaxLon { get; set; }
    public string? GeoJson { get; set; }
    public DateTime CreatedAt { get; set; }

    public Workspace Workspace { get; set; } = null!;
}

public class WorkspaceQuery
{
    public Guid Id { get; set; }
    public Guid WorkspaceId { get; set; }
    public string Query { get; set; } = "";
    public string? SourceScopeJson { get; set; }
    public int Priority { get; set; } = 100;
    public bool IsActive { get; set; } = true;
    public DateTime CreatedAt { get; set; }
    public DateTime UpdatedAt { get; set; }

    public Workspace Workspace { get; set; } = null!;
}

public class WorkspaceEntity
{
    public Guid Id { get; set; }
    public Guid WorkspaceId { get; set; }
    public string EntityKey { get; set; } = "";
    public string? EntityType { get; set; }
    public string DisplayName { get; set; } = "";
    public bool IsPrimary { get; set; }
    public string? Notes { get; set; }
    public DateTime CreatedAt { get; set; }

    public Workspace Workspace { get; set; } = null!;
}

public class WorkspaceSourceProfile
{
    public Guid Id { get; set; }
    public Guid WorkspaceId { get; set; }
    public string SourceName { get; set; } = "";
    public bool IsEnabled { get; set; } = true;
    public string? SettingsJson { get; set; }
    public DateTime CreatedAt { get; set; }

    public Workspace Workspace { get; set; } = null!;
}

public class WorkspaceSnapshot
{
    public Guid Id { get; set; }
    public Guid WorkspaceId { get; set; }
    public string SnapshotType { get; set; } = "";
    public int WindowHours { get; set; }
    public string PayloadJson { get; set; } = "";
    public DateTime GeneratedAt { get; set; }
    public DateTime? ExpiresAt { get; set; }
}

public class WorkspaceDecisionContext
{
    public Guid Id { get; set; }
    public Guid WorkspaceId { get; set; }
    public string EventId { get; set; } = "";
    public double? RelevanceScore { get; set; }
    public string? ContextJson { get; set; }
    public DateTime CreatedAt { get; set; }
}
