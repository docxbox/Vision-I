using Microsoft.EntityFrameworkCore;
using VisionI.API.Infrastructure;
using VisionI.API.Models;
using VisionI.API.Models.Entities;

namespace VisionI.API.Repositories;

public class WorkspaceRepository : IWorkspaceRepository
{
    private readonly AppDbContext _db;

    public WorkspaceRepository(AppDbContext db) => _db = db;

    public async Task<List<WorkspaceListDto>> ListAsync(CancellationToken ct = default)
    {
        return await _db.Workspaces
            .OrderByDescending(w => w.UpdatedAt)
            .Select(w => new WorkspaceListDto(
                w.Id, w.Slug, w.Title, w.Description, w.Status,
                w.Classification, w.DefaultWindowHours, w.UpdatedAt))
            .ToListAsync(ct);
    }

    /// <summary>
    /// Object-level RBAC: a workspace is visible if you're Admin, it's not private,
    /// it's yours, or it's a shared/seed workspace (system/null creator).
    /// </summary>
    public async Task<List<WorkspaceListDto>> ListVisibleAsync(string? userId, bool isAdmin, CancellationToken ct = default)
    {
        var q = _db.Workspaces.AsQueryable();
        if (!isAdmin)
            q = q.Where(w =>
                w.Visibility != "private"
                || w.CreatedBy == userId
                || w.CreatedBy == "system"
                || w.CreatedBy == null);

        return await q
            .OrderByDescending(w => w.UpdatedAt)
            .Select(w => new WorkspaceListDto(
                w.Id, w.Slug, w.Title, w.Description, w.Status,
                w.Classification, w.DefaultWindowHours, w.UpdatedAt))
            .ToListAsync(ct);
    }

    public async Task<Workspace?> GetBySlugAsync(string slug, CancellationToken ct = default)
    {
        return await _db.Workspaces
            .Include(w => w.GeoFilters)
            .Include(w => w.Queries)
            .Include(w => w.Entities)
            .Include(w => w.SourceProfiles)
            // 4 collection Includes in one query = cartesian explosion (rows multiply).
            // Split into one query per collection to avoid the >18s timeout on Overview.
            .AsSplitQuery()
            .FirstOrDefaultAsync(w => w.Slug == slug, ct);
    }

    public async Task<Workspace> CreateAsync(Workspace workspace, CancellationToken ct = default)
    {
        workspace.Id = Guid.NewGuid();
        workspace.CreatedAt = workspace.UpdatedAt = DateTime.UtcNow;

        foreach (var f in workspace.GeoFilters)    { f.Id = Guid.NewGuid(); f.WorkspaceId = workspace.Id; f.CreatedAt = DateTime.UtcNow; }
        foreach (var q in workspace.Queries)       { q.Id = Guid.NewGuid(); q.WorkspaceId = workspace.Id; q.CreatedAt = q.UpdatedAt = DateTime.UtcNow; }
        foreach (var e in workspace.Entities)      { e.Id = Guid.NewGuid(); e.WorkspaceId = workspace.Id; e.CreatedAt = DateTime.UtcNow; }
        foreach (var s in workspace.SourceProfiles){ s.Id = Guid.NewGuid(); s.WorkspaceId = workspace.Id; s.CreatedAt = DateTime.UtcNow; }

        _db.Workspaces.Add(workspace);
        await _db.SaveChangesAsync(ct);
        return workspace;
    }

    public async Task<Workspace?> UpdateAsync(string slug, UpdateWorkspaceRequest req, CancellationToken ct = default)
    {
        var workspace = await _db.Workspaces
            .Include(w => w.GeoFilters)
            .Include(w => w.Queries)
            .Include(w => w.Entities)
            .Include(w => w.SourceProfiles)
            .AsSplitQuery()
            .FirstOrDefaultAsync(w => w.Slug == slug, ct);
        if (workspace is null) return null;

        if (req.Title is not null)                workspace.Title = req.Title;
        if (req.Description is not null)          workspace.Description = req.Description;
        if (req.Status is not null)               workspace.Status = req.Status;
        if (req.Classification is not null)       workspace.Classification = req.Classification;
        if (req.DefaultWindowHours.HasValue)      workspace.DefaultWindowHours = req.DefaultWindowHours.Value;
        workspace.UpdatedAt = DateTime.UtcNow;

        if (req.GeoFilters is not null)
        {
            _db.WorkspaceGeoFilters.RemoveRange(workspace.GeoFilters);
            workspace.GeoFilters = req.GeoFilters.Select(f => new WorkspaceGeoFilter
            {
                Id = Guid.NewGuid(), WorkspaceId = workspace.Id,
                FilterType = f.FilterType, Name = f.Name,
                MinLat = f.MinLat, MaxLat = f.MaxLat, MinLon = f.MinLon, MaxLon = f.MaxLon,
                GeoJson = f.GeoJson, CreatedAt = DateTime.UtcNow,
            }).ToList();
        }

        if (req.Queries is not null)
        {
            _db.WorkspaceQueries.RemoveRange(workspace.Queries);
            workspace.Queries = req.Queries.Select(q => new WorkspaceQuery
            {
                Id = Guid.NewGuid(), WorkspaceId = workspace.Id,
                Query = q.Query, Priority = q.Priority, IsActive = q.IsActive,
                CreatedAt = DateTime.UtcNow, UpdatedAt = DateTime.UtcNow,
            }).ToList();
        }

        if (req.Entities is not null)
        {
            _db.WorkspaceEntities.RemoveRange(workspace.Entities);
            workspace.Entities = req.Entities.Select(e => new WorkspaceEntity
            {
                Id = Guid.NewGuid(), WorkspaceId = workspace.Id,
                EntityKey = e.EntityKey, DisplayName = e.DisplayName,
                EntityType = e.EntityType, IsPrimary = e.IsPrimary, Notes = e.Notes,
                CreatedAt = DateTime.UtcNow,
            }).ToList();
        }

        if (req.SourceProfiles is not null)
        {
            _db.WorkspaceSourceProfiles.RemoveRange(workspace.SourceProfiles);
            workspace.SourceProfiles = req.SourceProfiles.Select(s => new WorkspaceSourceProfile
            {
                Id = Guid.NewGuid(), WorkspaceId = workspace.Id,
                SourceName = s.SourceName, IsEnabled = s.IsEnabled, SettingsJson = s.SettingsJson,
                CreatedAt = DateTime.UtcNow,
            }).ToList();
        }

        await _db.SaveChangesAsync(ct);
        return workspace;
    }

    public async Task<bool> DeleteAsync(string slug, CancellationToken ct = default)
    {
        var workspace = await _db.Workspaces.FirstOrDefaultAsync(w => w.Slug == slug, ct);
        if (workspace is null) return false;
        _db.Workspaces.Remove(workspace);
        await _db.SaveChangesAsync(ct);
        return true;
    }

    public async Task<WorkspaceSnapshot?> GetSnapshotAsync(
        Guid workspaceId, string type, int windowHours, CancellationToken ct = default)
    {
        return await _db.WorkspaceSnapshots
            .Where(s =>
                s.WorkspaceId == workspaceId &&
                s.SnapshotType == type &&
                s.WindowHours == windowHours &&
                (s.ExpiresAt == null || s.ExpiresAt > DateTime.UtcNow))
            .OrderByDescending(s => s.GeneratedAt)
            .FirstOrDefaultAsync(ct);
    }

    public async Task UpsertSnapshotAsync(WorkspaceSnapshot snapshot, CancellationToken ct = default)
    {
        var existing = await _db.WorkspaceSnapshots
            .FirstOrDefaultAsync(s =>
                s.WorkspaceId == snapshot.WorkspaceId &&
                s.SnapshotType == snapshot.SnapshotType &&
                s.WindowHours == snapshot.WindowHours, ct);

        if (existing is null)
        {
            snapshot.Id = Guid.NewGuid();
            _db.WorkspaceSnapshots.Add(snapshot);
        }
        else
        {
            existing.PayloadJson = snapshot.PayloadJson;
            existing.GeneratedAt = snapshot.GeneratedAt;
            existing.ExpiresAt = snapshot.ExpiresAt;
        }

        await _db.SaveChangesAsync(ct);
    }

    public async Task<bool> ExistsAsync(string slug, CancellationToken ct = default)
        => await _db.Workspaces.AnyAsync(w => w.Slug == slug, ct);

    public async Task SaveDecisionContextAsync(WorkspaceDecisionContext ctx, CancellationToken ct = default)
    {
        ctx.Id = Guid.NewGuid();
        ctx.CreatedAt = DateTime.UtcNow;
        _db.WorkspaceDecisionContexts.Add(ctx);
        await _db.SaveChangesAsync(ct);
    }

    public async Task<List<WorkspaceDecisionContext>> GetDecisionContextsAsync(
        Guid workspaceId, int limit, int offset = 0, CancellationToken ct = default)
        => await _db.WorkspaceDecisionContexts
            .Where(d => d.WorkspaceId == workspaceId)
            .OrderByDescending(d => d.CreatedAt)
            .Skip(offset)
            .Take(limit)
            .ToListAsync(ct);

    public async Task<WorkspaceQuery?> AddQueryAsync(Guid workspaceId, string query, CancellationToken ct = default)
    {
        var q = new WorkspaceQuery
        {
            WorkspaceId = workspaceId,
            Query       = query.Trim()[..Math.Min(query.Trim().Length, 512)],
            Priority    = 100,
            IsActive    = true,
            CreatedAt   = DateTime.UtcNow,
            UpdatedAt   = DateTime.UtcNow,
        };
        _db.WorkspaceQueries.Add(q);
        await _db.SaveChangesAsync(ct);
        return q;
    }

    public async Task<bool> DeactivateQueryAsync(Guid queryId, CancellationToken ct = default)
    {
        var q = await _db.WorkspaceQueries.FindAsync(new object[] { queryId }, ct);
        if (q is null) return false;
        q.IsActive  = false;
        q.UpdatedAt = DateTime.UtcNow;
        await _db.SaveChangesAsync(ct);
        return true;
    }
}
