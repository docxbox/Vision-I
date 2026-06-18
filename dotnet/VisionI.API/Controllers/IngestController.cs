using Microsoft.AspNetCore.Authorization;
using Microsoft.AspNetCore.Mvc;
using Microsoft.AspNetCore.SignalR;
using VisionI.API.Hubs;
using VisionI.API.Models.Requests;
using VisionI.API.Services;

namespace VisionI.API.Controllers;

[ApiController]
[Route("api/ingest")]
[Authorize]
[Produces("application/json")]
public class IngestController : ControllerBase
{
    private readonly IIntelligenceService _intelligence;
    private readonly IHubContext<EventHub> _hub;
    private readonly ILogger<IngestController> _log;

    public IngestController(
        IIntelligenceService intelligence,
        IHubContext<EventHub> hub,
        ILogger<IngestController> log)
    {
        _intelligence = intelligence;
        _hub = hub;
        _log = log;
    }

    /// <summary>
    /// Trigger an ingestion run. Returns a job_id immediately.
    /// Poll GET /api/ingest/{jobId} until status == "done".
    /// Requires Analyst or Admin role.
    /// </summary>
    [HttpPost]
    [Authorize(Roles = "Analyst,Admin")]
    public async Task<IActionResult> TriggerIngest(
        [FromBody] IngestRequest req,
        CancellationToken ct = default)
    {
        _log.LogInformation("Ingest triggered: query={Query} limit={Limit}", req.Query, req.Limit);

        var json = await _intelligence.PostPythonJsonAsync("/ingest", new
        {
            query = req.Query,
            limit = req.Limit,
            enrich = req.Enrich,
            sources = req.Sources,
        }, ct);

        if (json == null)
            return StatusCode(502, "Intelligence layer unavailable.");

        return Accepted(json);
    }

    /// <summary>Poll ingestion job status. When status == "done", events are available via /api/events.</summary>
    [HttpGet("{jobId}")]
    [Authorize(Roles = "Analyst,Admin")]
    public async Task<IActionResult> GetJobStatus(string jobId, CancellationToken ct = default)
    {
        var result = await _intelligence.GetPythonDocumentAsync($"/ingest/{Uri.EscapeDataString(jobId)}", ct);
        if (result == null) return NotFound();

        if (result.RootElement.TryGetProperty("status", out var statusProp)
            && statusProp.GetString() == "done")
        {
            var total = result.RootElement.TryGetProperty("total_events", out var tp) ? tp.GetInt32() : 0;

            await _hub.Clients.Group("all").SendAsync("IngestComplete", new
            {
                jobId,
                total,
                query = result.RootElement.TryGetProperty("query", out var qp) ? qp.GetString() : "",
            }, ct);
        }

        return Content(result.RootElement.GetRawText(), "application/json");
    }
}
