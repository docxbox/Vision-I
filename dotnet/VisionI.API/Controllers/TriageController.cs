using System.Security.Claims;
using Microsoft.AspNetCore.Authorization;
using Microsoft.AspNetCore.Mvc;
using VisionI.API.Models.Requests;
using VisionI.API.Services;

namespace VisionI.API.Controllers;

[ApiController]
[Route("api/triage")]
[Authorize]
[Produces("application/json")]
public class TriageController : ControllerBase
{
    private readonly ITriageService _triage;

    public TriageController(ITriageService triage)
    {
        _triage = triage;
    }

    [HttpGet("candidates")]
    public async Task<IActionResult> GetCandidates(
        [FromQuery] int limit = 25,
        [FromQuery] string? source = null,
        [FromQuery] string? query = null,
        CancellationToken ct = default)
    {
        var items = await _triage.GetCandidatesAsync(limit, source, query, ct);
        return Ok(new { total = items.Count, items });
    }

    [HttpGet("queue")]
    public async Task<IActionResult> GetQueue(
        [FromQuery] string? eventId = null,
        [FromQuery] string? status = null,
        [FromQuery] bool mine = false,
        [FromQuery] int limit = 100,
        [FromQuery] int offset = 0,
        CancellationToken ct = default)
    {
        var analystUserId = mine ? User.FindFirstValue(ClaimTypes.NameIdentifier) : null;
        var items = await _triage.GetQueueAsync(eventId, status, analystUserId, limit, offset, ct);
        return Ok(new { total = items.Count, items });
    }

    [HttpGet("{eventId}")]
    public async Task<IActionResult> GetRecord(string eventId, CancellationToken ct = default)
    {
        var item = await _triage.GetRecordAsync(eventId, ct);
        return item is null ? NotFound() : Ok(item);
    }

    [HttpGet("summary")]
    public async Task<IActionResult> GetSummary(CancellationToken ct = default)
    {
        return Ok(await _triage.GetSummaryAsync(ct));
    }

    [HttpPost("{eventId}")]
    [Authorize(Roles = "Analyst,Admin")]
    public async Task<IActionResult> Upsert(
        string eventId,
        [FromBody] UpsertTriageRequest request,
        CancellationToken ct = default)
    {
        if (!string.Equals(eventId, request.EventId, StringComparison.OrdinalIgnoreCase))
            return BadRequest(new { error = "EVENT_ID_MISMATCH" });

        var userId = User.FindFirstValue(ClaimTypes.NameIdentifier);
        var analystName = User.FindFirstValue(ClaimTypes.Name) ?? User.Identity?.Name;
        var result = await _triage.UpsertAsync(request, userId, analystName, ct);
        if (result == null)
            return BadRequest(new { error = "INVALID_TRIAGE_REQUEST" });

        return Ok(result);
    }
}
