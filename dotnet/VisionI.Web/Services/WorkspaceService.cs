using VisionI.Web.Models;

namespace VisionI.Web.Services;

public class WorkspaceService
{
    private readonly ApiService _api;
    private static readonly TimeSpan WorkspaceReadTimeout = TimeSpan.FromSeconds(20);
    private static readonly TimeSpan WorkspaceRefreshTimeout = TimeSpan.FromSeconds(120);

    public WorkspaceService(ApiService api) => _api = api;

    public Task<WorkspaceListResponse?> GetWorkspacesAsync(CancellationToken ct = default)
        => _api.GetAsync<WorkspaceListResponse>("api/workspaces", ct);

    public Task<WorkspaceDetailModel?> GetWorkspaceAsync(string slug, CancellationToken ct = default)
        => _api.GetAsync<WorkspaceDetailModel>($"api/workspaces/{slug}", ct);

    public Task<WorkspaceOverview?> GetWorkspaceOverviewAsync(string slug, CancellationToken ct = default)
        => _api.GetAsync<WorkspaceOverview>($"api/workspaces/{slug}/overview", WorkspaceReadTimeout, ct);

    public Task<WorkspaceMap?> GetWorkspaceMapAsync(string slug, CancellationToken ct = default)
        => _api.GetAsync<WorkspaceMap>($"api/workspaces/{slug}/map", WorkspaceReadTimeout, ct);

    public Task<WorkspaceDevelopments?> GetWorkspaceDevelopmentsAsync(string slug, CancellationToken ct = default)
        => _api.GetAsync<WorkspaceDevelopments>($"api/workspaces/{slug}/developments", WorkspaceReadTimeout, ct);

    public Task<WorkspaceEntities?> GetWorkspaceEntitiesAsync(string slug, CancellationToken ct = default)
        => _api.GetAsync<WorkspaceEntities>($"api/workspaces/{slug}/entities", WorkspaceReadTimeout, ct);

    public Task<WorkspaceAssets?> GetWorkspaceAssetsAsync(string slug, CancellationToken ct = default)
        => _api.GetAsync<WorkspaceAssets>($"api/workspaces/{slug}/assets", WorkspaceReadTimeout, ct);

    public Task<WorkspaceSentiment?> GetWorkspaceSentimentAsync(string slug, CancellationToken ct = default)
        => _api.GetAsync<WorkspaceSentiment>($"api/workspaces/{slug}/sentiment", WorkspaceReadTimeout, ct);

    public Task<WorkspaceCorrelation?> GetWorkspaceCorrelationAsync(string slug, CancellationToken ct = default)
        => _api.GetAsync<WorkspaceCorrelation>($"api/workspaces/{slug}/correlation", WorkspaceReadTimeout, ct);

    public Task<WorkspaceActions?> GetWorkspaceActionsAsync(string slug, CancellationToken ct = default)
        => _api.GetAsync<WorkspaceActions>($"api/workspaces/{slug}/actions", WorkspaceReadTimeout, ct);

    public Task<object?> RefreshWorkspaceAsync(string slug, CancellationToken ct = default)
        => _api.PostAsync<object>($"api/workspaces/{slug}/refresh", new { }, WorkspaceRefreshTimeout, ct);

    public Task<WorkspaceDetailModel?> CreateWorkspaceAsync(CreateWorkspaceRequest req, CancellationToken ct = default)
        => _api.PostAsync<WorkspaceDetailModel>("api/workspaces", req, ct);

    public Task<(WorkspaceDetailModel? Value, string? Error)> CreateWorkspaceWithErrorAsync(
        CreateWorkspaceRequest req, CancellationToken ct = default)
        => _api.PostWithErrorAsync<WorkspaceDetailModel>("api/workspaces", req, ct);

    public Task<WorkspaceSavedQueriesResponse?> GetSavedQueriesAsync(string slug, CancellationToken ct = default)
        => _api.GetAsync<WorkspaceSavedQueriesResponse>($"api/workspaces/{slug}/queries", ct);

    public Task<object?> AddSavedQueryAsync(string slug, string query, CancellationToken ct = default)
        => _api.PostAsync<object>($"api/workspaces/{slug}/queries", new { query }, ct);

    public Task<bool> RemoveSavedQueryAsync(string slug, Guid queryId, CancellationToken ct = default)
        => _api.DeleteAsync($"api/workspaces/{slug}/queries/{queryId}", ct);

    // ── Evidence board ──────────────────────────────────────────────────────

    public Task<WorkspaceEvidenceResponse?> GetEvidenceAsync(string slug, CancellationToken ct = default)
        => _api.GetAsync<WorkspaceEvidenceResponse>($"api/workspaces/{slug}/evidence", ct);

    public Task<(WorkspacePinResult? Value, string? Error)> PinEvidenceAsync(
        string slug, PinEvidenceRequest req, CancellationToken ct = default)
        => _api.PostWithErrorAsync<WorkspacePinResult>($"api/workspaces/{slug}/evidence", req, ct);

    public Task<bool> UnpinEvidenceAsync(string slug, Guid evidenceId, CancellationToken ct = default)
        => _api.DeleteAsync($"api/workspaces/{slug}/evidence/{evidenceId}", ct);
}

public record WorkspaceListResponse(int Total, List<WorkspaceListItem> Items);
public record WorkspaceSavedQueriesResponse(List<WorkspaceSavedQuery> Queries);
public record WorkspaceSavedQuery(Guid Id, string Query, bool IsActive, int Priority, string? CreatedAt);

public record WorkspaceEvidenceResponse(int Total, List<WorkspaceEvidenceItem> Items);
public record WorkspaceEvidenceItem(
    Guid Id, string ItemType, string ItemId, string Title,
    string? Source, string? Note, string? PinnedBy, DateTime CreatedAt);
public record PinEvidenceRequest(string ItemType, string ItemId, string? Title, string? Source, string? Note = null);
public record WorkspacePinResult(Guid Id, bool AlreadyPinned);
