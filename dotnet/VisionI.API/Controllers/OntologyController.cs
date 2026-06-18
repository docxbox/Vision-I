using Microsoft.AspNetCore.Authorization;
using Microsoft.AspNetCore.Mvc;
using VisionI.API.Services;

namespace VisionI.API.Controllers;

[ApiController]
[Authorize]
[Produces("application/json")]
public class OntologyController : ControllerBase
{
    private readonly IIntelligenceService _intelligence;

    public OntologyController(IIntelligenceService intelligence)
    {
        _intelligence = intelligence;
    }

    [HttpGet("api/ontology/overview")]
    public async Task<IActionResult> GetOverview([FromQuery] int limit = 12, CancellationToken ct = default)
    {
        var json = await _intelligence.GetCachedJsonAsync(
            $"cache:ontology:overview:{limit}",
            async innerCt =>
            {
                var precomputed = await _intelligence.GetPrecomputedJsonAsync("precomputed:ontology:overview", innerCt);
                if (precomputed != null) return precomputed;
                return await _intelligence.GetPythonJsonAsync($"/ontology/overview?limit={limit}", innerCt);
            },
            TimeSpan.FromSeconds(30),
            ct);

        if (json == null) return StatusCode(502, "Intelligence layer unavailable.");
        return Content(json, "application/json");
    }

    [HttpGet("api/actors/{actorId}")]
    [HttpGet("api/ontology/actors/{actorId}")]
    public async Task<IActionResult> GetActor(string actorId, CancellationToken ct = default)
    {
        var json = await _intelligence.GetPythonJsonAsync($"/ontology/actors/{Uri.EscapeDataString(actorId)}", ct);
        if (json == null) return NotFound();
        return Content(json, "application/json");
    }

    [HttpGet("api/ontology/events/{eventId}")]
    public async Task<IActionResult> GetEvent(string eventId, CancellationToken ct = default)
    {
        var json = await _intelligence.GetPythonJsonAsync($"/ontology/events/{Uri.EscapeDataString(eventId)}", ct);
        if (json == null) return NotFound();
        return Content(json, "application/json");
    }

    [HttpGet("api/graph")]
    public async Task<IActionResult> GetGraph([FromQuery] int limit = 10, CancellationToken ct = default)
    {
        var json = await _intelligence.GetCachedJsonAsync(
            $"cache:ontology:graph:{limit}",
            async innerCt =>
            {
                var precomputed = await _intelligence.GetPrecomputedJsonAsync("precomputed:ontology:graph", innerCt);
                if (precomputed != null) return precomputed;
                return await _intelligence.GetPythonJsonAsync($"/ontology/graph?limit={limit}", innerCt);
            },
            TimeSpan.FromSeconds(30),
            ct);

        if (json == null) return StatusCode(502, "Intelligence layer unavailable.");
        return Content(json, "application/json");
    }

    [HttpGet("api/ontology/summary")]
    public async Task<IActionResult> GetOntologySummary(CancellationToken ct = default)
    {
        var precomputed = await _intelligence.GetPrecomputedJsonAsync("precomputed:ontology:summary", ct);
        if (precomputed != null)
            return Content(precomputed, "application/json");

        var json = await _intelligence.GetCachedJsonAsync(
            "cache:ontology:summary",
            innerCt => _intelligence.GetPythonJsonAsync("/ontology/summary", innerCt),
            TimeSpan.FromMinutes(5),
            ct);

        if (json == null) return StatusCode(502, "Intelligence layer unavailable.");
        return Content(json, "application/json");
    }

    private static readonly HashSet<string> WriteKeywords = new(StringComparer.OrdinalIgnoreCase)
    {
        "CREATE", "MERGE", "SET", "DELETE", "DETACH", "REMOVE", "DROP"
    };

    [HttpPost("api/ontology/cypher")]
    [Authorize(Roles = "Analyst,Admin")]
    public async Task<IActionResult> ExecuteCypher(
        [FromBody] CypherRequest body,
        CancellationToken ct = default)
    {
        if (string.IsNullOrWhiteSpace(body.Query))
            return BadRequest(new { error = "EMPTY_QUERY" });

        var tokens = body.Query.Split((char[]?)null, StringSplitOptions.RemoveEmptyEntries);
        var badToken = tokens.FirstOrDefault(t => WriteKeywords.Contains(t));
        if (badToken != null)
            return StatusCode(403, new { error = "WRITE_OPERATION_BLOCKED", token = badToken.ToUpperInvariant() });

        var json = await _intelligence.PostPythonJsonAsync("/ontology/cypher", new
        {
            query = body.Query,
            parameters = body.Parameters,
        }, ct);
        if (json == null) return StatusCode(502, "Intelligence layer unavailable.");
        return Content(json, "application/json");
    }

    public sealed record CypherRequest(string Query, Dictionary<string, object?>? Parameters = null);
}
