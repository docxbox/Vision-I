using VisionI.API.Models.Entities;

namespace VisionI.API.Repositories;

public interface ITriageRepository
{
    Task<List<EventTriageRecord>> GetQueueAsync(
        string? eventId = null,
        string? status = null,
        string? analystUserId = null,
        int limit = 100,
        int offset = 0,
        CancellationToken ct = default);

    Task<Dictionary<string, EventTriageRecord>> GetByEventIdsAsync(
        IEnumerable<string> eventIds,
        CancellationToken ct = default);

    Task<EventTriageRecord?> GetByEventIdAsync(string eventId, CancellationToken ct = default);
    Task<Dictionary<string, int>> GetSummaryAsync(CancellationToken ct = default);
    Task<EventTriageRecord> SaveAsync(EventTriageRecord record, CancellationToken ct = default);
}
