using Microsoft.AspNetCore.Authorization;
using Microsoft.AspNetCore.SignalR;

namespace VisionI.API.Hubs;

using VisionI.API.Models.Realtime;

/// <summary>
/// SignalR hub — pushes live intelligence updates to connected frontend clients.
///
/// Clients connect to /hubs/events and join groups based on their subscription.
/// The hub itself is thin — messages are broadcast from background services
/// and controllers via IHubContext&lt;EventHub&gt;.
///
/// Messages pushed to clients:
///   NewEvent         { event }           — new ingested event
///   SentimentUpdate  { entityId, score } — entity sentiment changed
///   IngestComplete   { jobId, total, sourceCounts } — job finished
///   LiveUpdate       { events[] }        — periodic live stream tick
///   MissionStarted   { missionId, query }           — agent mission launched
///   MissionUpdate    { missionId, status, stage }    — mission stage changed
///   AgentStatusChange { agentId, status }            — agent state transition
/// </summary>
[Authorize]
public class EventHub : Hub
{
    private readonly ILogger<EventHub> _log;

    public EventHub(ILogger<EventHub> log) => _log = log;

    public override async Task OnConnectedAsync()
    {
        // Join both groups for backwards compatibility and future-proof grouping.
        await Groups.AddToGroupAsync(Context.ConnectionId, RealtimeGroups.All);
        await Groups.AddToGroupAsync(Context.ConnectionId, RealtimeGroups.IntelligenceStream);
        _log.LogInformation("SignalR client connected: {ConnectionId}", Context.ConnectionId);
        await base.OnConnectedAsync();
    }

    public override async Task OnDisconnectedAsync(Exception? exception)
    {
        _log.LogInformation("SignalR client disconnected: {ConnectionId}", Context.ConnectionId);
        await base.OnDisconnectedAsync(exception);
    }

    /// <summary>
    /// Called by the client to subscribe to a specific source group
    /// (e.g. "usgs", "newsapi", "opensky").
    /// </summary>
    public async Task Subscribe(string group)
    {
        await Groups.AddToGroupAsync(Context.ConnectionId, group);
        _log.LogDebug("{ConnectionId} subscribed to group '{Group}'", Context.ConnectionId, group);
    }

    /// <summary>
    /// Called by the client to unsubscribe from a source group.
    /// </summary>
    public async Task Unsubscribe(string group)
    {
        await Groups.RemoveFromGroupAsync(Context.ConnectionId, group);
    }
}