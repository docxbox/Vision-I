using System.Text.Json;
using Microsoft.AspNetCore.Authorization;
using Microsoft.AspNetCore.Mvc;
using Microsoft.AspNetCore.SignalR;
using VisionI.API.Hubs;
using VisionI.API.Services;

namespace VisionI.API.Controllers;

/// <summary>
/// Agent Swarm proxy - forwards requests to the Python /agents endpoints
/// and broadcasts mission events via SignalR.
/// </summary>
[ApiController]
[Route("api/agents")]
[Authorize]
[Produces("application/json")]
public class AgentsController : ControllerBase
{
    private readonly IIntelligenceService _intelligence;
    private readonly IHubContext<EventHub> _hub;
    private readonly ILogger<AgentsController> _log;

    public AgentsController(
        IIntelligenceService intelligence,
        IHubContext<EventHub> hub,
        ILogger<AgentsController> log)
    {
        _intelligence = intelligence;
        _hub = hub;
        _log = log;
    }

    [HttpGet]
    public async Task<IActionResult> ListAgents(CancellationToken ct)
    {
        var json = await _intelligence.GetPythonJsonAsync("/agents", ct);
        if (json is null) return StatusCode(502, new { error = "Intelligence layer unavailable" });
        return Content(json, "application/json");
    }

    [HttpGet("llm-status")]
    public async Task<IActionResult> GetLlmStatus(CancellationToken ct)
    {
        var json = await _intelligence.GetPythonJsonAsync("/agents/llm-status", ct);
        if (json is null) return StatusCode(502, new { error = "Intelligence layer unavailable" });
        return Content(json, "application/json");
    }

    [HttpGet("{agentId}")]
    public async Task<IActionResult> GetAgent(string agentId, CancellationToken ct)
    {
        var json = await _intelligence.GetPythonJsonAsync($"/agents/{Uri.EscapeDataString(agentId)}", ct);
        if (json is null) return StatusCode(502, new { error = "Intelligence layer unavailable" });
        return Content(json, "application/json");
    }

    [HttpPost("mission")]
    [Authorize(Roles = "Analyst,Admin")]
    public async Task<IActionResult> StartMission(
        [FromBody] JsonElement body, CancellationToken ct)
    {
        var result = await _intelligence.PostPythonDocumentAsync("/agents/mission", body, ct);
        if (result is null) return StatusCode(502, new { error = "Intelligence layer unavailable" });

        try
        {
            var missionId = result.RootElement.TryGetProperty("mission_id", out var mid) ? mid.GetString() ?? "" : "";
            var query = result.RootElement.TryGetProperty("query", out var q) ? q.GetString() ?? "" : "";

            await _hub.Clients.Group("all").SendAsync("MissionStarted", new { missionId, query }, ct);
        }
        catch (Exception ex)
        {
            _log.LogWarning(ex, "Failed to broadcast MissionStarted");
        }

        return new ContentResult
        {
            StatusCode = StatusCodes.Status202Accepted,
            Content = result.RootElement.GetRawText(),
            ContentType = "application/json"
        };
    }

    [HttpGet("missions")]
    public async Task<IActionResult> ListMissions(int limit = 20, CancellationToken ct = default)
    {
        var json = await _intelligence.GetPythonJsonAsync($"/agents/missions?limit={limit}", ct);
        if (json is null) return StatusCode(502, new { error = "Intelligence layer unavailable" });
        return Content(json, "application/json");
    }

    [HttpGet("mission/{missionId}")]
    public async Task<IActionResult> GetMission(string missionId, CancellationToken ct)
    {
        var json = await _intelligence.GetPythonJsonAsync($"/agents/mission/{Uri.EscapeDataString(missionId)}", ct);
        if (json is null) return StatusCode(502, new { error = "Intelligence layer unavailable" });
        return Content(json, "application/json");
    }

    [HttpGet("log")]
    public async Task<IActionResult> GetLog(
        string? missionId = null, int limit = 100, CancellationToken ct = default)
    {
        var path = $"/agents/log?limit={limit}";
        if (!string.IsNullOrEmpty(missionId))
            path += $"&mission_id={Uri.EscapeDataString(missionId)}";

        var json = await _intelligence.GetPythonJsonAsync(path, ct);
        if (json is null) return StatusCode(502, new { error = "Intelligence layer unavailable" });
        return Content(json, "application/json");
    }
}
