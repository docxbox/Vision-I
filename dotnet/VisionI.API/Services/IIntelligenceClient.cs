using System.Text.Json;

namespace VisionI.API.Services;

/// <summary>
/// Typed contract for the internal Python intelligence layer.
/// The browser never talks to Python directly; the .NET API owns this boundary.
/// </summary>
public interface IIntelligenceClient
{
    Task<JsonDocument?> HealthAsync(CancellationToken ct = default);
    Task<JsonDocument?> AdminHealthAsync(CancellationToken ct = default);

    Task<JsonDocument?> TriggerIngestAsync(
        string query,
        int limit = 10,
        bool enrich = false,
        string[]? sources = null,
        CancellationToken ct = default);

    Task<JsonDocument?> GetIngestStatusAsync(string jobId, CancellationToken ct = default);
    Task<JsonDocument?> TriggerLiveAsync(CancellationToken ct = default);

    Task<JsonDocument?> GetEventsAsync(
        string? source = null,
        string? eventType = null,
        string? query = null,
        string? sentiment = null,
        string? from = null,
        string? to = null,
        int limit = 50,
        int offset = 0,
        string? jobId = null,
        CancellationToken ct = default);

    Task<JsonDocument?> GetEventMapAsync(
        string? source = null,
        string? eventType = null,
        string? from = null,
        string? to = null,
        int limit = 500,
        CancellationToken ct = default);

    Task<JsonDocument?> GetEntitiesAsync(
        string? type = null,
        int minMentions = 1,
        int limit = 100,
        int offset = 0,
        CancellationToken ct = default);

    Task<JsonDocument?> GetLiveStreamsAsync(
        int limit = 20,
        string? sources = null,
        CancellationToken ct = default);

    Task<JsonDocument?> GetSentimentTimelineAsync(
        string? query = null,
        string? source = null,
        string? entityId = null,
        string? from = null,
        string? to = null,
        int? hours = null,
        string bucket = "day",
        CancellationToken ct = default);

    Task<JsonDocument?> GetJobsAsync(int limit = 20, string? status = null, CancellationToken ct = default);
    Task<JsonDocument?> GetStatsAsync(CancellationToken ct = default);
    Task<JsonDocument?> GetOntologyOverviewAsync(int limit = 12, CancellationToken ct = default);

    Task<JsonDocument?> GetAsync(string path, CancellationToken ct = default);
    Task<JsonDocument?> PostAsync(string path, object? body, CancellationToken ct = default);
}
