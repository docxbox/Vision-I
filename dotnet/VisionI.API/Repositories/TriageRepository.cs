using Microsoft.EntityFrameworkCore;
using VisionI.API.Infrastructure;
using VisionI.API.Models.Entities;

namespace VisionI.API.Repositories;

public sealed class TriageRepository : ITriageRepository
{
    private readonly AppDbContext _db;

    public TriageRepository(AppDbContext db)
    {
        _db = db;
    }

    public async Task<List<EventTriageRecord>> GetQueueAsync(
        string? eventId = null,
        string? status = null,
        string? analystUserId = null,
        int limit = 100,
        int offset = 0,
        CancellationToken ct = default)
    {
        var query = _db.EventTriageRecords.AsNoTracking();

        if (!string.IsNullOrWhiteSpace(eventId))
            query = query.Where(x => x.EventId == eventId);

        if (!string.IsNullOrWhiteSpace(status))
            query = query.Where(x => x.Status == status);

        if (!string.IsNullOrWhiteSpace(analystUserId))
            query = query.Where(x => x.AnalystUserId == analystUserId);

        return await query
            .OrderByDescending(x => x.UpdatedAt)
            .Skip(offset)
            .Take(limit)
            .ToListAsync(ct);
    }

    public async Task<Dictionary<string, EventTriageRecord>> GetByEventIdsAsync(
        IEnumerable<string> eventIds,
        CancellationToken ct = default)
    {
        var ids = eventIds
            .Where(static x => !string.IsNullOrWhiteSpace(x))
            .Distinct(StringComparer.OrdinalIgnoreCase)
            .ToList();

        if (ids.Count == 0)
            return new Dictionary<string, EventTriageRecord>(StringComparer.OrdinalIgnoreCase);

        var items = await _db.EventTriageRecords
            .AsNoTracking()
            .Where(x => ids.Contains(x.EventId))
            .ToListAsync(ct);

        return items.ToDictionary(x => x.EventId, StringComparer.OrdinalIgnoreCase);
    }

    public Task<EventTriageRecord?> GetByEventIdAsync(string eventId, CancellationToken ct = default)
        => _db.EventTriageRecords.FirstOrDefaultAsync(x => x.EventId == eventId, ct);

    public async Task<Dictionary<string, int>> GetSummaryAsync(CancellationToken ct = default)
    {
        var grouped = await _db.EventTriageRecords
            .AsNoTracking()
            .GroupBy(x => x.Status)
            .Select(g => new { Status = g.Key, Count = g.Count() })
            .ToListAsync(ct);

        return grouped.ToDictionary(x => x.Status, x => x.Count, StringComparer.OrdinalIgnoreCase);
    }

    public async Task<EventTriageRecord> SaveAsync(EventTriageRecord record, CancellationToken ct = default)
    {
        if (record.Id == 0)
            _db.EventTriageRecords.Add(record);
        else
            _db.EventTriageRecords.Update(record);

        await _db.SaveChangesAsync(ct);
        return record;
    }
}
