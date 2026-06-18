using Microsoft.AspNetCore.Authorization;
using Microsoft.AspNetCore.Mvc;
using VisionI.API.Services;

namespace VisionI.API.Controllers;

[ApiController]
[Authorize]
[Produces("application/json")]
[Route("api/decisions")]
public class DecisionsController : ControllerBase
{
    private readonly IIntelligenceService _intelligence;

    public DecisionsController(IIntelligenceService intelligence)
    {
        _intelligence = intelligence;
    }

    [HttpPost]
    [Authorize(Roles = "Analyst,Admin")]
    public async Task<IActionResult> CreateDecision([FromBody] object body, CancellationToken ct = default)
    {
        var json = await _intelligence.PostPythonJsonAsync("/decisions", body, ct);
        if (json == null) return StatusCode(502, "Decision layer unavailable.");
        return Content(json, "application/json");
    }

    [HttpGet]
    public async Task<IActionResult> ListDecisions([FromQuery] int limit = 50, CancellationToken ct = default)
    {
        var json = await _intelligence.GetPythonJsonAsync($"/decisions?limit={limit}", ct);
        if (json == null) return StatusCode(502, "Decision layer unavailable.");
        return Content(json, "application/json");
    }

    [HttpGet("{id}")]
    public async Task<IActionResult> GetDecision(string id, CancellationToken ct = default)
    {
        var json = await _intelligence.GetPythonJsonAsync($"/decisions/{Uri.EscapeDataString(id)}", ct);
        if (json == null) return StatusCode(502, "Decision layer unavailable.");
        return Content(json, "application/json");
    }

    [HttpPost("{id}/outcome")]
    [Authorize(Roles = "Analyst,Admin")]
    public async Task<IActionResult> RecordOutcome(string id, [FromBody] object body, CancellationToken ct = default)
    {
        var json = await _intelligence.PostPythonJsonAsync($"/decisions/{Uri.EscapeDataString(id)}/outcome", body, ct);
        if (json == null) return StatusCode(502, "Decision layer unavailable.");
        return Content(json, "application/json");
    }
}
