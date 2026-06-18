using System.Text.Json;
using VisionI.API.Models.Entities;
using VisionI.API.Models.Requests;
using VisionI.API.Models.Responses;
using VisionI.API.Repositories;

namespace VisionI.API.Services;

public sealed class TriageService : ITriageService
{
    private readonly ITriageRepository _repository;
    private readonly IIntelligenceService _intelligence;

    public TriageService(ITriageRepository repository, IIntelligenceService intelligence)
    {
        _repository = repository;
        _intelligence = intelligence;
    }

    public async Task<IReadOnlyList<TriageCandidateResponse>> GetCandidatesAsync(
        int limit = 25,
        string? source = null,
        string? query = null,
        CancellationToken ct = default)
    {
        var path = $"/events?limit={Math.Clamp(limit, 1, 100)}";
        if (!string.IsNullOrWhiteSpace(source))
            path += $"&source={Uri.EscapeDataString(source)}";
        if (!string.IsNullOrWhiteSpace(query))
            path += $"&query={Uri.EscapeDataString(query)}";

        var json = await _intelligence.GetPythonJsonAsync(path, ct);
        if (string.IsNullOrWhiteSpace(json))
            return Array.Empty<TriageCandidateResponse>();

        using var doc = JsonDocument.Parse(json);
        if (!doc.RootElement.TryGetProperty("events", out var eventsElement) || eventsElement.ValueKind != JsonValueKind.Array)
            return Array.Empty<TriageCandidateResponse>();

        var eventIds = eventsElement.EnumerateArray()
            .Select(static item => item.TryGetProperty("event_id", out var id) ? id.GetString() ?? "" : "")
            .Where(static id => !string.IsNullOrWhiteSpace(id))
            .ToList();

        var existing = await _repository.GetByEventIdsAsync(eventIds, ct);
        var result = new List<TriageCandidateResponse>(eventIds.Count);

        foreach (var item in eventsElement.EnumerateArray())
        {
            var eventId = item.TryGetProperty("event_id", out var idProp) ? idProp.GetString() ?? "" : "";
            if (string.IsNullOrWhiteSpace(eventId))
                continue;

            existing.TryGetValue(eventId, out var record);

            var title = item.TryGetProperty("title", out var titleProp) ? titleProp.GetString() ?? "Untitled event" : "Untitled event";
            var itemSource = item.TryGetProperty("source", out var sourceProp) ? sourceProp.GetString() ?? "" : "";
            var eventType = item.TryGetProperty("event_type", out var typeProp) ? typeProp.GetString() ?? "" : "";
            var timestamp = item.TryGetProperty("timestamp", out var tsProp) ? tsProp.GetString() : null;
            var sourceUrl = item.TryGetProperty("url", out var urlProp) ? urlProp.GetString() : null;
            var region = ExtractRegion(item);
            var risk = TryReadDouble(item, "risk_score");
            var confidence = TryReadDouble(item, "confidence_score");
            var relatedActorCount = item.TryGetProperty("actors", out var actorProp) && actorProp.ValueKind == JsonValueKind.Array
                ? actorProp.GetArrayLength()
                : 0;
            var similarEventCount = item.TryGetProperty("supporting_signals", out var supportProp) && supportProp.ValueKind == JsonValueKind.Array
                ? supportProp.GetArrayLength()
                : 0;

            result.Add(new TriageCandidateResponse(
                eventId,
                title,
                itemSource,
                eventType,
                record?.RiskScore ?? risk,
                record?.ConfidenceScore ?? confidence,
                timestamp,
                record?.SourceUrl ?? sourceUrl,
                record?.Region ?? region,
                record?.Status ?? "new",
                record?.Priority ?? ResolvePriority(risk, confidence),
                record?.AnalystDisplayName,
                record?.SimilarEventCount ?? similarEventCount,
                record?.RelatedActorCount ?? relatedActorCount
            ));
        }

        return result
            .OrderByDescending(static x => x.RiskScore ?? 0)
            .ThenByDescending(static x => x.ConfidenceScore ?? 0)
            .ToList();
    }

    public async Task<IReadOnlyList<TriageRecordResponse>> GetQueueAsync(
        string? eventId = null,
        string? status = null,
        string? analystUserId = null,
        int limit = 100,
        int offset = 0,
        CancellationToken ct = default)
    {
        var items = await _repository.GetQueueAsync(eventId, status, analystUserId, Math.Clamp(limit, 1, 200), Math.Max(0, offset), ct);
        return items.Select(MapRecord).ToList();
    }

    public async Task<TriageSummaryResponse> GetSummaryAsync(CancellationToken ct = default)
    {
        var counts = await _repository.GetSummaryAsync(ct);
        return new TriageSummaryResponse(
            Total: counts.Values.Sum(),
            New: counts.GetValueOrDefault("new"),
            Reviewing: counts.GetValueOrDefault("reviewing"),
            Escalated: counts.GetValueOrDefault("escalated"),
            Actioned: counts.GetValueOrDefault("actioned"),
            Dismissed: counts.GetValueOrDefault("dismissed")
        );
    }

    public async Task<TriageRecordResponse?> GetRecordAsync(string eventId, CancellationToken ct = default)
    {
        var record = await _repository.GetByEventIdAsync(eventId, ct);
        return record is null ? null : MapRecord(record);
    }

    public async Task<TriageRecordResponse?> UpsertAsync(
        UpsertTriageRequest request,
        string? analystUserId,
        string? analystDisplayName,
        CancellationToken ct = default)
    {
        if (string.IsNullOrWhiteSpace(request.EventId) || string.IsNullOrWhiteSpace(request.Title))
            return null;

        var now = DateTime.UtcNow;
        var status = NormalizeStatus(request.Status);
        var priority = NormalizePriority(request.Priority, request.RiskScore, request.ConfidenceScore);

        var record = await _repository.GetByEventIdAsync(request.EventId, ct) ?? new EventTriageRecord
        {
            EventId = request.EventId,
            CreatedAt = now,
        };

        record.Title = request.Title.Trim();
        record.Source = request.Source?.Trim() ?? "";
        record.EventType = request.EventType?.Trim() ?? "";
        record.Status = status;
        record.Priority = priority;
        record.RiskScore = request.RiskScore;
        record.ConfidenceScore = request.ConfidenceScore;
        record.AnalystUserId = analystUserId;
        record.AnalystDisplayName = analystDisplayName;
        record.Note = string.IsNullOrWhiteSpace(request.Note) ? null : request.Note.Trim();
        record.SourceUrl = string.IsNullOrWhiteSpace(request.SourceUrl) ? null : request.SourceUrl.Trim();
        record.Region = string.IsNullOrWhiteSpace(request.Region) ? null : request.Region.Trim();
        record.SimilarEventCount = Math.Max(0, request.SimilarEventCount);
        record.RelatedActorCount = Math.Max(0, request.RelatedActorCount);
        record.LastSeenAt = now;
        record.UpdatedAt = now;

        var saved = await _repository.SaveAsync(record, ct);
        return MapRecord(saved);
    }

    private static TriageRecordResponse MapRecord(EventTriageRecord record)
        => new(
            record.Id,
            record.EventId,
            record.Title,
            record.Source,
            record.EventType,
            record.Status,
            record.Priority,
            record.RiskScore,
            record.ConfidenceScore,
            record.AnalystUserId,
            record.AnalystDisplayName,
            record.Note,
            record.SourceUrl,
            record.Region,
            record.SimilarEventCount,
            record.RelatedActorCount,
            record.LastSeenAt.ToString("O"),
            record.CreatedAt.ToString("O"),
            record.UpdatedAt.ToString("O")
        );

    private static string ExtractRegion(JsonElement item)
    {
        if (item.TryGetProperty("location", out var location) && location.ValueKind == JsonValueKind.Object)
        {
            if (location.TryGetProperty("country", out var country))
                return country.GetString() ?? "";
            if (location.TryGetProperty("name", out var name))
                return name.GetString() ?? "";
        }

        return "";
    }

    private static double? TryReadDouble(JsonElement item, string property)
    {
        if (!item.TryGetProperty(property, out var prop))
            return null;

        return prop.ValueKind == JsonValueKind.Number && prop.TryGetDouble(out var value)
            ? value
            : null;
    }

    private static string ResolvePriority(double? risk, double? confidence)
        => NormalizePriority(null, risk, confidence);

    private static string NormalizeStatus(string? status)
    {
        var normalized = status?.Trim().ToLowerInvariant();
        return normalized switch
        {
            "reviewing" => "reviewing",
            "escalated" => "escalated",
            "actioned" => "actioned",
            "dismissed" => "dismissed",
            _ => "new",
        };
    }

    private static string NormalizePriority(string? priority, double? risk, double? confidence)
    {
        var normalized = priority?.Trim().ToLowerInvariant();
        if (normalized is "critical" or "high" or "medium" or "low")
            return normalized;

        if ((risk ?? 0) >= 0.85 || (confidence ?? 0) >= 0.9) return "critical";
        if ((risk ?? 0) >= 0.65 || (confidence ?? 0) >= 0.75) return "high";
        if ((risk ?? 0) >= 0.4 || (confidence ?? 0) >= 0.55) return "medium";
        return "low";
    }
}
