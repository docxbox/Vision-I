using System.Text.Json;
using Microsoft.AspNetCore.Authorization;
using Microsoft.AspNetCore.Mvc;
using VisionI.API.Services;

namespace VisionI.API.Controllers;

[ApiController]
[Route("api/playbooks")]
[Authorize]
[Produces("application/json")]
public class PlaybooksController : ControllerBase
{
    private readonly IIntelligenceService _intelligence;

    public PlaybooksController(IIntelligenceService intelligence)
    {
        _intelligence = intelligence;
    }

    [HttpGet]
    public async Task<IActionResult> ListPlaybooks(CancellationToken ct = default)
    {
        var json = await _intelligence.GetCachedJsonAsync(
            "cache:playbooks:list",
            innerCt => _intelligence.GetPythonJsonAsync("/api/playbooks", innerCt),
            TimeSpan.FromMinutes(30),
            ct);

        if (json == null) return StatusCode(502, "Intelligence layer unavailable.");
        return Content(json, "application/json");
    }

    [HttpGet("{playbookId}")]
    public async Task<IActionResult> GetPlaybook(string playbookId, CancellationToken ct = default)
    {
        var cacheKey = $"cache:playbooks:item:{playbookId}";
        var json = await _intelligence.GetCachedJsonAsync(
            cacheKey,
            innerCt => _intelligence.GetPythonJsonAsync($"/api/playbooks/{Uri.EscapeDataString(playbookId)}", innerCt),
            TimeSpan.FromMinutes(30),
            ct);

        if (json == null) return NotFound();
        return Content(json, "application/json");
    }

    [HttpPost("match")]
    public async Task<IActionResult> MatchPlaybooks([FromBody] JsonElement body, CancellationToken ct = default)
    {
        var json = await _intelligence.PostPythonJsonAsync("/api/playbooks/match", body, ct);
        if (json == null) return StatusCode(502, "Intelligence layer unavailable.");
        return Content(json, "application/json");
    }

    [HttpPost("{playbookId}/execute")]
    [Authorize(Roles = "Analyst,Admin")]
    public async Task<IActionResult> ExecutePlaybook(
        string playbookId,
        [FromBody] JsonElement body,
        CancellationToken ct = default)
    {
        var json = await _intelligence.PostPythonJsonAsync($"/api/playbooks/{Uri.EscapeDataString(playbookId)}/execute", body, ct);
        if (json == null) return StatusCode(502, "Intelligence layer unavailable.");
        return Content(json, "application/json");
    }
}
