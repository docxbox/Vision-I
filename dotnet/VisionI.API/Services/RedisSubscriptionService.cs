using Microsoft.AspNetCore.SignalR;
using StackExchange.Redis;
using System.Text.Json;
using VisionI.API.Hubs;
using VisionI.API.Models.Realtime;

namespace VisionI.API.Services;

/// <summary>
/// Background service that bridges Redis Pub/Sub events from the Python
/// intelligence layer to the .NET API layer:
///   1. Subscribes to pipeline:ingest_complete and pipeline:intelligence_complete
///   2. Pushes real-time updates to Blazor frontend via SignalR
///   3. Invalidates stale cache entries on data arrival
///
/// This is the key component connecting Layer 3 (Intelligence) → Layer 4 (Serving) → Layer 5 (Application).
/// </summary>
public class RedisSubscriptionService : BackgroundService
{
    private readonly IHubContext<EventHub> _hub;
    private readonly IConnectionMultiplexer _redis;
    private readonly ILogger<RedisSubscriptionService> _log;

    public RedisSubscriptionService(
        IHubContext<EventHub> hub,
        IConnectionMultiplexer redis,
        ILogger<RedisSubscriptionService> log)
    {
        _hub = hub;
        _redis = redis;
        _log = log;
    }

    protected override async Task ExecuteAsync(CancellationToken stoppingToken)
    {
        _log.LogInformation("RedisSubscriptionService starting — subscribing to pipeline events");

        try
        {
            var subscriber = _redis.GetSubscriber();

            // Subscribe to ingest completion events
            await subscriber.SubscribeAsync(
                RedisChannel.Literal("pipeline:ingest_complete"),
                async (channel, message) =>
                {
                    try
                    {
                        _log.LogInformation("Received pipeline:ingest_complete");

                        var payload = ParsePayload(message!);

                        var data = JsonSerializer.SerializeToElement(new
                        {
                            batch_id = payload.GetValueOrDefault("batch_id", ""),
                            event_count = payload.GetValueOrDefault("event_count", "0"),
                            job_type = payload.GetValueOrDefault("job_type", ""),
                            timestamp = payload.GetValueOrDefault("timestamp", DateTime.UtcNow.ToString("o")),
                        });

                        var env = new RealtimeEnvelope(
                            V: 1,
                            Type: RealtimeEventType.IngestComplete,
                            Ts: DateTime.UtcNow.ToString("O"),
                            Data: data);

                        // New stable contract
                        await _hub.Clients.Group(RealtimeGroups.IntelligenceStream)
                            .SendAsync(RealtimeMethods.RealtimeEvent, env, stoppingToken);

                        // Legacy method name for older clients
                        await _hub.Clients.Group(RealtimeGroups.All)
                            .SendAsync("IngestComplete", env.Data, stoppingToken);

                        _log.LogInformation("Pushed IngestComplete to SignalR clients");
                    }
                    catch (Exception ex)
                    {
                        _log.LogError(ex, "Error handling ingest_complete event");
                    }
                });

            // Subscribe to intelligence completion events
            await subscriber.SubscribeAsync(
                RedisChannel.Literal("pipeline:intelligence_complete"),
                async (channel, message) =>
                {
                    try
                    {
                        _log.LogInformation("Received pipeline:intelligence_complete");

                        var payload = ParsePayload(message!);

                        var data = JsonSerializer.SerializeToElement(new
                        {
                            batch_id = payload.GetValueOrDefault("batch_id", ""),
                            narratives_count = payload.GetValueOrDefault("narratives_count", "0"),
                            alerts_count = payload.GetValueOrDefault("alerts_count", "0"),
                            trigger = payload.GetValueOrDefault("trigger", ""),
                        });

                        var env = new RealtimeEnvelope(
                            V: 1,
                            Type: RealtimeEventType.IntelligenceUpdate,
                            Ts: DateTime.UtcNow.ToString("O"),
                            Data: data);

                        await _hub.Clients.Group(RealtimeGroups.IntelligenceStream)
                            .SendAsync(RealtimeMethods.RealtimeEvent, env, stoppingToken);

                        await _hub.Clients.Group(RealtimeGroups.All)
                            .SendAsync("IntelligenceUpdate", env.Data, stoppingToken);

                        _log.LogInformation("Pushed IntelligenceUpdate to SignalR clients");
                    }
                    catch (Exception ex)
                    {
                        _log.LogError(ex, "Error handling intelligence_complete event");
                    }
                });

            // Subscribe to correlation completion events
            await subscriber.SubscribeAsync(
                RedisChannel.Literal("pipeline:correlation_complete"),
                async (channel, message) =>
                {
                    try
                    {
                        _log.LogInformation("Received pipeline:correlation_complete");
                        var payload = ParsePayload(message!);

                        var data = JsonSerializer.SerializeToElement(new
                        {
                            cluster_count = payload.GetValueOrDefault("cluster_count", "0"),
                            total_signals = payload.GetValueOrDefault("total_signals", "0"),
                            timestamp = DateTime.UtcNow.ToString("o"),
                        });

                        var env = new RealtimeEnvelope(
                            V: 1,
                            Type: RealtimeEventType.CorrelationUpdate,
                            Ts: DateTime.UtcNow.ToString("O"),
                            Data: data);

                        await _hub.Clients.Group(RealtimeGroups.IntelligenceStream)
                            .SendAsync(RealtimeMethods.RealtimeEvent, env, stoppingToken);

                        await _hub.Clients.Group(RealtimeGroups.All)
                            .SendAsync("CorrelationUpdate", env.Data, stoppingToken);

                        _log.LogInformation("Pushed CorrelationUpdate to SignalR clients");
                    }
                    catch (Exception ex)
                    {
                        _log.LogError(ex, "Error handling correlation_complete event");
                    }
                });

            // Subscribe to composite event detection events
            await subscriber.SubscribeAsync(
                RedisChannel.Literal("pipeline:composite_events"),
                async (channel, message) =>
                {
                    try
                    {
                        _log.LogInformation("Received pipeline:composite_events");
                        var payload = ParsePayload(message!);

                        var data = JsonSerializer.SerializeToElement(new
                        {
                            event_count = payload.GetValueOrDefault("event_count", "0"),
                            cluster_count = payload.GetValueOrDefault("cluster_count", "0"),
                            timestamp = DateTime.UtcNow.ToString("o"),
                        });

                        var env = new RealtimeEnvelope(
                            V: 1,
                            Type: RealtimeEventType.CompositeEventDetected,
                            Ts: DateTime.UtcNow.ToString("O"),
                            Data: data);

                        await _hub.Clients.Group(RealtimeGroups.IntelligenceStream)
                            .SendAsync(RealtimeMethods.RealtimeEvent, env, stoppingToken);

                        await _hub.Clients.Group(RealtimeGroups.All)
                            .SendAsync("CompositeEventDetected", env.Data, stoppingToken);

                        _log.LogInformation("Pushed CompositeEventDetected to SignalR clients");
                    }
                    catch (Exception ex)
                    {
                        _log.LogError(ex, "Error handling composite_events event");
                    }
                });

            _log.LogInformation("RedisSubscriptionService: subscribed to pipeline events (including correlation + composite)");

            // Keep alive until cancellation
            await Task.Delay(Timeout.Infinite, stoppingToken);
        }
        catch (OperationCanceledException)
        {
            _log.LogInformation("RedisSubscriptionService stopping");
        }
        catch (Exception ex)
        {
            _log.LogError(ex, "RedisSubscriptionService fatal error");
        }
    }

    private static Dictionary<string, string> ParsePayload(string message)
    {
        try
        {
            var doc = JsonDocument.Parse(message);
            var result = new Dictionary<string, string>();
            foreach (var prop in doc.RootElement.EnumerateObject())
            {
                result[prop.Name] = prop.Value.ValueKind == JsonValueKind.String
                    ? prop.Value.GetString() ?? ""
                    : prop.Value.GetRawText();
            }
            return result;
        }
        catch
        {
            return new Dictionary<string, string>();
        }
    }
}
