using System.Text;
using System.Text.Json;

namespace VisionI.API.Services;

/// <summary>
/// Background IHostedService that keeps the intelligence pipeline warm.
///
/// Responsibilities:
///   1. Trigger the Python ingest pipeline on a schedule (every 10 min)
///   2. Trigger situation detection (every 15 min) via POST /situations/detect
///   3. Warm the event/escalation cache after each ingest cycle
///
/// This removes the need for analysts to manually click "Refresh" to get
/// up-to-date intelligence — the system self-updates in the background.
/// </summary>
public sealed class IntelligenceHostedService : BackgroundService
{
    private readonly IServiceProvider _services;
    private readonly ILogger<IntelligenceHostedService> _log;

    private static readonly TimeSpan IngestInterval     = TimeSpan.FromMinutes(10);
    private static readonly TimeSpan SituationInterval  = TimeSpan.FromMinutes(15);
    private static readonly TimeSpan StartupDelay       = TimeSpan.FromSeconds(30);
    private static readonly TimeSpan JobPollInterval    = TimeSpan.FromSeconds(5);
    private static readonly TimeSpan JobTimeout         = TimeSpan.FromMinutes(4);

    public IntelligenceHostedService(
        IServiceProvider services,
        ILogger<IntelligenceHostedService> log)
    {
        _services = services;
        _log      = log;
    }

    protected override async Task ExecuteAsync(CancellationToken ct)
    {
        // Give the API time to fully start before the first cycle
        await Task.Delay(StartupDelay, ct);

        _log.LogInformation("IntelligenceHostedService: starting pipeline warm-up loop");

        var ingestDue     = DateTime.UtcNow;
        var situationDue  = DateTime.UtcNow + TimeSpan.FromMinutes(5);

        while (!ct.IsCancellationRequested)
        {
            try
            {
                var now = DateTime.UtcNow;

                if (now >= ingestDue)
                {
                    await TriggerIngestAsync(ct);
                    ingestDue = now + IngestInterval;
                }

                if (now >= situationDue)
                {
                    await TriggerSituationDetectionAsync(ct);
                    situationDue = now + SituationInterval;
                }
            }
            catch (OperationCanceledException)
            {
                break;
            }
            catch (Exception ex)
            {
                _log.LogWarning(ex, "IntelligenceHostedService: unhandled error in loop");
            }

            await Task.Delay(TimeSpan.FromMinutes(1), ct);
        }

        _log.LogInformation("IntelligenceHostedService: stopped");
    }

    private async Task TriggerIngestAsync(CancellationToken ct)
    {
        try
        {
            using var scope = _services.CreateScope();
            var client = scope.ServiceProvider.GetRequiredService<IIntelligenceClient>();
            var cache = scope.ServiceProvider.GetRequiredService<RedisCacheService>();

            _log.LogDebug("IntelligenceHostedService: triggering live ingest");
            var result = await client.TriggerLiveAsync(ct);
            if (result == null)
            {
                _log.LogWarning("IntelligenceHostedService: live ingest trigger returned no response");
                return;
            }

            var jobIds = ExtractJobIds(result.RootElement);
            _log.LogInformation("IntelligenceHostedService: ingest triggered with {Count} jobs", jobIds.Count);

            if (jobIds.Count == 0)
            {
                await InvalidateAndWarmAsync(client, cache, ct);
                return;
            }

            var finished = await WaitForJobsAsync(client, jobIds, ct);
            if (finished)
                await InvalidateAndWarmAsync(client, cache, ct);
        }
        catch (Exception ex)
        {
            _log.LogWarning(ex, "IntelligenceHostedService: ingest trigger failed");
        }
    }

    private async Task TriggerSituationDetectionAsync(CancellationToken ct)
    {
        try
        {
            using var scope = _services.CreateScope();
            var client      = scope.ServiceProvider.GetRequiredService<IIntelligenceClient>();

            _log.LogDebug("IntelligenceHostedService: triggering situation detection");
            await client.PostAsync("/situations/detect?window_hours=6", null, ct);
            _log.LogInformation("IntelligenceHostedService: situation detection triggered");
        }
        catch (Exception ex)
        {
            _log.LogWarning(ex, "IntelligenceHostedService: situation detection trigger failed");
        }
    }

    private async Task<bool> WaitForJobsAsync(
        IIntelligenceClient client,
        IReadOnlyCollection<string> jobIds,
        CancellationToken ct)
    {
        var pending = new HashSet<string>(jobIds, StringComparer.OrdinalIgnoreCase);
        var deadline = DateTime.UtcNow + JobTimeout;
        var sawCompletion = false;

        while (pending.Count > 0 && DateTime.UtcNow < deadline && !ct.IsCancellationRequested)
        {
            foreach (var jobId in pending.ToArray())
            {
                JsonDocument? statusDoc = null;
                try
                {
                    statusDoc = await client.GetIngestStatusAsync(jobId, ct);
                }
                catch (Exception ex)
                {
                    _log.LogDebug(ex, "IntelligenceHostedService: poll failed for job {JobId}", jobId);
                }

                if (statusDoc == null)
                    continue;

                var status = GetStatus(statusDoc.RootElement);
                if (status is null)
                    continue;

                if (status.Equals("done", StringComparison.OrdinalIgnoreCase))
                {
                    pending.Remove(jobId);
                    sawCompletion = true;
                    _log.LogInformation("IntelligenceHostedService: job {JobId} completed", jobId);
                }
                else if (status.Equals("failed", StringComparison.OrdinalIgnoreCase))
                {
                    pending.Remove(jobId);
                    _log.LogWarning("IntelligenceHostedService: job {JobId} failed", jobId);
                }
            }

            if (pending.Count > 0)
                await Task.Delay(JobPollInterval, ct);
        }

        if (pending.Count > 0)
            _log.LogWarning("IntelligenceHostedService: timed out waiting for jobs: {Jobs}", string.Join(", ", pending));

        return sawCompletion;
    }

    private async Task InvalidateAndWarmAsync(
        IIntelligenceClient client,
        RedisCacheService cache,
        CancellationToken ct)
    {
        await cache.RemoveByPrefixesAsync(new[]
        {
            "cache:events:",
            "cache:event:",
            "cache:streams:",
            "cache:dashboard:",
        }, ct);

        await Task.WhenAll(
            client.GetEventsAsync(limit: 200, ct: ct),
            client.GetEventMapAsync(limit: 500, ct: ct),
            client.GetLiveStreamsAsync(limit: 50, ct: ct),
            client.GetAsync("/alerts?limit=10&acknowledged=false", ct));
    }

    private static List<string> ExtractJobIds(JsonElement payload)
    {
        var ids = new List<string>();
        if (payload.ValueKind != JsonValueKind.Object)
            return ids;

        if (!payload.TryGetProperty("job_ids", out var jobIds) || jobIds.ValueKind != JsonValueKind.Array)
            return ids;

        foreach (var item in jobIds.EnumerateArray())
        {
            if (item.ValueKind == JsonValueKind.String && !string.IsNullOrWhiteSpace(item.GetString()))
                ids.Add(item.GetString()!);
        }

        return ids;
    }

    private static string? GetStatus(JsonElement payload)
        => payload.ValueKind == JsonValueKind.Object
           && payload.TryGetProperty("status", out var status)
           && status.ValueKind == JsonValueKind.String
            ? status.GetString()
            : null;
}
