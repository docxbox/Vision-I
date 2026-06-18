using Microsoft.AspNetCore.Authorization;
using Microsoft.AspNetCore.Mvc;
using VisionI.API.Services;
using System.Collections.Generic;

namespace VisionI.API.Controllers;

/// <summary>
/// Restricted read proxy for Python paths that have no dedicated .NET controller.
/// Uses an explicit allowlist — anything not in the list returns 404.
/// All callers should prefer dedicated typed endpoints; this proxy is a fallback only.
/// </summary>
[ApiController]
[Route("api/intel")]
[Authorize]
public class PythonReadProxyController : ControllerBase
{
    /// <summary>
    /// Exact path prefixes (no query string) allowed through this proxy.
    /// Add new entries here only when adding a new Python endpoint that has no
    /// dedicated .NET controller; remove entries when a typed controller is created.
    /// </summary>
    private static readonly HashSet<string> AllowedPrefixes = new(StringComparer.OrdinalIgnoreCase)
    {
        "/intelligence/escalation",
        "/intelligence/bot-scores",
        "/intelligence/credibility",
        "/intelligence/community-graph",
        "/intelligence/causality",
        "/intelligence/unrest-watch",
        "/copilot/summary",
        "/copilot/similar",
        "/copilot/recommend",
        "/copilot/explain",
        "/copilot/ask",
        "/influence/actors",
        "/influence/propaganda",
        "/influence/herd",
    };

    private readonly IIntelligenceService _intelligence;
    private readonly ILogger<PythonReadProxyController> _log;

    public PythonReadProxyController(IIntelligenceService intelligence, ILogger<PythonReadProxyController> log)
    {
        _intelligence = intelligence;
        _log = log;
    }

    [HttpGet("{**path}")]
    public async Task<IActionResult> ProxyRead(string? path, CancellationToken ct = default)
    {
        if (string.IsNullOrWhiteSpace(path))
            return BadRequest(new { error = "MISSING_PATH" });

        var cleaned = "/" + path.TrimStart('/');

        if (!AllowedPrefixes.Any(p => cleaned.StartsWith(p, StringComparison.OrdinalIgnoreCase)))
        {
            _log.LogWarning("Intel proxy blocked non-allowlisted path: {Path}", cleaned);
            return NotFound(new { error = "PATH_NOT_ALLOWED", path = cleaned });
        }

        var query = Request.QueryString.HasValue ? Request.QueryString.Value : string.Empty;
        var upstreamPath = $"{cleaned}{query}";

        var json = await _intelligence.GetPythonJsonAsync(upstreamPath, ct);
        if (json == null)
        {
            _log.LogWarning("Intel read proxy failed for {Path}", upstreamPath);
            return StatusCode(502, new { error = "INTELLIGENCE_LAYER_UNAVAILABLE", path = upstreamPath });
        }

        return Content(json, "application/json");
    }
}
