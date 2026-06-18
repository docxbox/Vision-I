using Microsoft.AspNetCore.Authorization;
using Microsoft.AspNetCore.Mvc;
using VisionI.API.Services;

namespace VisionI.API.Controllers;

[ApiController]
[Authorize]
[Produces("application/json")]
[Route("api/operations")]
public class OperationsController : ControllerBase
{
    private readonly IIntelligenceService _intelligence;

    public OperationsController(IIntelligenceService intelligence)
    {
        _intelligence = intelligence;
    }

    [HttpGet("overview")]
    public async Task<IActionResult> GetOverview([FromQuery] int limit = 8, CancellationToken ct = default)
    {
        var json = await _intelligence.GetCachedJsonAsync(
            $"cache:operations:overview:{limit}",
            async innerCt =>
            {
                var precomputed = await _intelligence.GetPrecomputedJsonAsync("precomputed:operations:overview", innerCt);
                if (precomputed != null) return precomputed;
                return await _intelligence.GetPythonJsonAsync($"/ontology/operations/overview?limit={limit}", innerCt);
            },
            TimeSpan.FromSeconds(20),
            ct);

        if (json == null) return StatusCode(502, "Decision layer unavailable.");
        return Content(json, "application/json");
    }

    [HttpPost("{alertId}/simulate")]
    [Authorize(Roles = "Analyst,Admin")]
    public async Task<IActionResult> SimulateCOA(string alertId, CancellationToken ct = default)
    {
        var json = await _intelligence.PostPythonJsonAsync($"/alerts/{Uri.EscapeDataString(alertId)}/simulate", new { }, ct);
        if (json == null) return StatusCode(502, "Flight wargaming layer offline.");
        return Content(json, "application/json");
    }
}
