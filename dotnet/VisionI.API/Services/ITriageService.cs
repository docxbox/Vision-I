using VisionI.API.Models.Requests;
using VisionI.API.Models.Responses;

namespace VisionI.API.Services;

public interface ITriageService
{
    Task<IReadOnlyList<TriageCandidateResponse>> GetCandidatesAsync(
        int limit = 25,
        string? source = null,
        string? query = null,
        CancellationToken ct = default);

    Task<IReadOnlyList<TriageRecordResponse>> GetQueueAsync(
        string? eventId = null,
        string? status = null,
        string? analystUserId = null,
        int limit = 100,
        int offset = 0,
        CancellationToken ct = default);

    Task<TriageSummaryResponse> GetSummaryAsync(CancellationToken ct = default);
    Task<TriageRecordResponse?> GetRecordAsync(string eventId, CancellationToken ct = default);

    Task<TriageRecordResponse?> UpsertAsync(
        UpsertTriageRequest request,
        string? analystUserId,
        string? analystDisplayName,
        CancellationToken ct = default);
}
