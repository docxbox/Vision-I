using VisionI.API.Models;

namespace VisionI.API.Services;

public interface IWorkspaceComposerService
{
    Task<WorkspaceOverviewDto?> GetOverviewAsync(string slug, CancellationToken ct = default);
    Task<WorkspaceMapDto?> GetMapAsync(string slug, CancellationToken ct = default);
    Task<WorkspaceDevelopmentsDto?> GetDevelopmentsAsync(string slug, CancellationToken ct = default);
    Task<WorkspaceEntitiesDto?> GetEntitiesAsync(string slug, CancellationToken ct = default);
    Task<WorkspaceAssetsDto?> GetAssetsAsync(string slug, CancellationToken ct = default);
    Task<WorkspaceSentimentDto?> GetSentimentAsync(string slug, CancellationToken ct = default);
    Task<WorkspaceCorrelationDto?> GetCorrelationAsync(string slug, CancellationToken ct = default);
    Task<WorkspaceActionsDto?> GetActionsAsync(string slug, CancellationToken ct = default);
    Task RefreshAsync(string slug, CancellationToken ct = default);
}
