using Microsoft.AspNetCore.Authorization;
using Microsoft.AspNetCore.Mvc;
using Microsoft.AspNetCore.RateLimiting;
using VisionI.API.Services;

namespace VisionI.API.Controllers;

/// <summary>
/// Unified ontology object read-model gateway. One normalized shape for every
/// modeled object (event|actor|asset) plus its provenance. All surfaces (reports,
/// copilot, detail views) can read objects through here instead of per-feature
/// snapshot endpoints. Proxies the Python /objects layer with a short cache.
/// </summary>
[ApiController]
[Route("api/objects")]
[Authorize]
[Produces("application/json")]
public class ObjectsController : ControllerBase
{
    private readonly IIntelligenceService _intelligence;

    public ObjectsController(IIntelligenceService intelligence) => _intelligence = intelligence;

    [HttpGet("{type}/{id}")]
    [EnableRateLimiting("query")]
    public Task<IActionResult> GetObject(string type, string id, CancellationToken ct = default)
        => ProxyAsync(
            $"/objects/{Uri.EscapeDataString(type)}/{Uri.EscapeDataString(id)}",
            $"obj:v1:{type}:{id}", ct);

    [HttpGet("{type}/{id}/lineage")]
    [EnableRateLimiting("query")]
    public Task<IActionResult> GetLineage(string type, string id, CancellationToken ct = default)
        => ProxyAsync(
            $"/objects/{Uri.EscapeDataString(type)}/{Uri.EscapeDataString(id)}/lineage",
            $"obj:lin:v1:{type}:{id}", ct);

    /// <summary>Typed adjacency — all linked objects, for the explorer "drill anywhere".</summary>
    [HttpGet("{type}/{id}/neighbors")]
    [EnableRateLimiting("query")]
    public Task<IActionResult> GetNeighbors(string type, string id, [FromQuery] int limit = 80, CancellationToken ct = default)
        => ProxyAsync(
            $"/objects/{Uri.EscapeDataString(type)}/{Uri.EscapeDataString(id)}/neighbors?limit={Math.Clamp(limit, 1, 300)}",
            $"obj:nb:v1:{type}:{id}:{Math.Clamp(limit, 1, 300)}", ct);

    private async Task<IActionResult> ProxyAsync(string pythonPath, string cacheKey, CancellationToken ct)
    {
        var json = await _intelligence.GetCachedJsonAsync(
            cacheKey,
            innerCt => _intelligence.GetPythonJsonAsync(pythonPath, innerCt),
            TimeSpan.FromSeconds(60),
            ct);

        if (string.IsNullOrWhiteSpace(json))
            return NotFound();
        return Content(json, "application/json");
    }
}
