using Microsoft.AspNetCore.Authorization;
using Microsoft.AspNetCore.SignalR;

namespace VisionI.API.Hubs;

/// <summary>
/// Dedicated SignalR hub for the Vision-I intelligence dashboard.
/// Handles: NewEvent, AssetUpdate, AlertRaised, InsightGenerated, AgentStatusChanged
/// </summary>
[Authorize]
public class VisionHub : Hub
{
    private readonly ILogger<VisionHub> _log;

    public VisionHub(ILogger<VisionHub> log) => _log = log;

    public override async Task OnConnectedAsync()
    {
        await Groups.AddToGroupAsync(Context.ConnectionId, "intelligence_stream");
        _log.LogInformation("SignalR client connected to VisionHub: {ConnectionId}", Context.ConnectionId);
        await base.OnConnectedAsync();
    }

    public override async Task OnDisconnectedAsync(Exception? exception)
    {
        _log.LogInformation("SignalR client disconnected from VisionHub: {ConnectionId}", Context.ConnectionId);
        await base.OnDisconnectedAsync(exception);
    }

    public async Task Subscribe(string group)
    {
        await Groups.AddToGroupAsync(Context.ConnectionId, group);
    }

    public async Task Unsubscribe(string group)
    {
        await Groups.RemoveFromGroupAsync(Context.ConnectionId, group);
    }
}
