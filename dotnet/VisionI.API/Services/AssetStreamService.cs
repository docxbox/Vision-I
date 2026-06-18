using Microsoft.AspNetCore.SignalR;
using System.Text.Json;
using VisionI.API.Hubs;
using VisionI.API.Models.Realtime;
using VisionI.API.Infrastructure;

namespace VisionI.API.Services;

/// <summary>
/// Background service that polls the Python backend for the latest physical assets
/// every 5 seconds and streams them directly to the frontend via SignalR.
/// </summary>
public class AssetStreamService : BackgroundService
{
    private readonly IHubContext<EventHub> _hub;
    private readonly IIntelligenceClient _python;
    private readonly RedisCacheService _cache;
    private readonly ILogger<AssetStreamService> _log;
    private readonly TimeSpan _pollInterval = TimeSpan.FromSeconds(5);

    public AssetStreamService(
        IHubContext<EventHub> hub,
        IIntelligenceClient python,
        RedisCacheService cache,
        ILogger<AssetStreamService> log)
    {
        _hub = hub;
        _python = python;
        _cache = cache;
        _log = log;
    }

    protected override async Task ExecuteAsync(CancellationToken stoppingToken)
    {
        _log.LogInformation("AssetStreamService tracking active — broadcasting via SignalR every 5s.");

        while (!stoppingToken.IsCancellationRequested)
        {
            try
            {
                var response = await _python.GetAsync("/assets?limit=500", stoppingToken);
                if (response != null)
                {
                    var env = new RealtimeEnvelope(
                        V: 1,
                        Type: RealtimeEventType.AssetStreamUpdate,
                        Ts: DateTime.UtcNow.ToString("O"),
                        Data: response.RootElement.Clone());

                    await _hub.Clients.Group(RealtimeGroups.IntelligenceStream)
                        .SendAsync(RealtimeMethods.RealtimeEvent, env, stoppingToken);

                    // Legacy
                    await _hub.Clients.Group(RealtimeGroups.All)
                        .SendAsync("AssetStreamUpdate", env.Data, stoppingToken);

                    var raw = env.Data.GetRawText();
                    await _cache.SetAsync("snapshot:assets:latest", raw, TimeSpan.FromMinutes(10), stoppingToken);
                    await _cache.SetAsync(
                        $"snapshot:assets:{DateTime.UtcNow:yyyyMMddHHmm}",
                        raw,
                        TimeSpan.FromHours(6),
                        stoppingToken);
                }
            }
            catch (Exception ex)
            {
                _log.LogDebug("AssetStream fetch skipped due to error: {Msg}", ex.Message);
            }

            await Task.Delay(_pollInterval, stoppingToken);
        }
    }
}
