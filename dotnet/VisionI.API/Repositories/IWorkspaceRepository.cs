using VisionI.API.Models;
using VisionI.API.Models.Entities;

namespace VisionI.API.Repositories;

public interface IWorkspaceRepository
{
    Task<List<WorkspaceListDto>> ListAsync(CancellationToken ct = default);
    Task<List<WorkspaceListDto>> ListVisibleAsync(string? userId, bool isAdmin, CancellationToken ct = default);
    Task<Workspace?> GetBySlugAsync(string slug, CancellationToken ct = default);
    Task<Workspace> CreateAsync(Workspace workspace, CancellationToken ct = default);
    Task<Workspace?> UpdateAsync(string slug, UpdateWorkspaceRequest req, CancellationToken ct = default);
    Task<bool> DeleteAsync(string slug, CancellationToken ct = default);
    Task<WorkspaceSnapshot?> GetSnapshotAsync(Guid workspaceId, string type, int windowHours, CancellationToken ct = default);
    Task UpsertSnapshotAsync(WorkspaceSnapshot snapshot, CancellationToken ct = default);
    Task<bool> ExistsAsync(string slug, CancellationToken ct = default);
    Task SaveDecisionContextAsync(WorkspaceDecisionContext ctx, CancellationToken ct = default);
    Task<List<WorkspaceDecisionContext>> GetDecisionContextsAsync(Guid workspaceId, int limit, int offset = 0, CancellationToken ct = default);
    Task<WorkspaceQuery?> AddQueryAsync(Guid workspaceId, string query, CancellationToken ct = default);
    Task<bool> DeactivateQueryAsync(Guid queryId, CancellationToken ct = default);
}
