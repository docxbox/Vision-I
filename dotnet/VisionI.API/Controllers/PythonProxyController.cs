using System.Net.Http.Headers;
using Microsoft.AspNetCore.Authorization;
using Microsoft.AspNetCore.Mvc;
using VisionI.API.Services;

namespace VisionI.API.Controllers;

/// <summary>
/// Admin-only passthrough to the Python internal API for full surface coverage.
///
/// This is NOT intended for general frontend use. The .NET API should expose stable
/// business endpoints (and use caching, auth, and validation). This route exists to:
/// - unblock parity with new Python endpoints without waiting for .NET controller work
/// - support operational debugging in production (admin-only)
/// </summary>
[ApiController]
[Route("api/python")]
[Authorize(Roles = "Admin")]
public class PythonProxyController : ControllerBase
{
    private static readonly HashSet<string> HopByHopHeaders = new(StringComparer.OrdinalIgnoreCase)
    {
        "Connection",
        "Keep-Alive",
        "Proxy-Authenticate",
        "Proxy-Authorization",
        "TE",
        "Trailer",
        "Transfer-Encoding",
        "Upgrade",
        "Host",
    };

    private readonly HttpClient _http;
    private readonly ILogger<PythonProxyController> _log;

    public PythonProxyController(IHttpClientFactory httpFactory, ILogger<PythonProxyController> log)
    {
        // Reuse the same HttpClient configuration (BaseUrl + X-Internal-Key header)
        // as the typed PythonApiClient.
        _http = httpFactory.CreateClient(nameof(PythonApiClient));
        _log = log;
    }

    [AcceptVerbs("GET", "POST", "PUT", "PATCH", "DELETE")]
    [Route("{**path}")]
    public async Task<IActionResult> Proxy(string path, CancellationToken ct)
    {
        var qs = Request.QueryString.HasValue ? Request.QueryString.Value : "";
        var target = "/" + (path ?? "").TrimStart('/') + qs;

        using var msg = new HttpRequestMessage(new HttpMethod(Request.Method), target);

        // Forward body for write methods (and for GET if present).
        if (Request.ContentLength is > 0)
        {
            msg.Content = new StreamContent(Request.Body);
            if (!string.IsNullOrWhiteSpace(Request.ContentType))
                msg.Content.Headers.ContentType = MediaTypeHeaderValue.Parse(Request.ContentType);
        }

        // Forward selected headers (excluding hop-by-hop).
        foreach (var (key, value) in Request.Headers)
        {
            if (HopByHopHeaders.Contains(key)) continue;
            if (key.Equals("Authorization", StringComparison.OrdinalIgnoreCase)) continue; // never forward user JWT to python

            // Content-* headers go to content if possible
            if (key.StartsWith("Content-", StringComparison.OrdinalIgnoreCase))
            {
                if (msg.Content != null)
                    msg.Content.Headers.TryAddWithoutValidation(key, (IEnumerable<string>)value);
                continue;
            }

            msg.Headers.TryAddWithoutValidation(key, (IEnumerable<string>)value);
        }

        try
        {
            using var resp = await _http.SendAsync(msg, HttpCompletionOption.ResponseHeadersRead, ct);
            var bytes = await resp.Content.ReadAsByteArrayAsync(ct);

            // Copy content-type through; default to json for convenience.
            var contentType = resp.Content.Headers.ContentType?.ToString() ?? "application/json";
            Response.StatusCode = (int)resp.StatusCode;

            // Copy safe response headers
            foreach (var h in resp.Headers)
            {
                if (HopByHopHeaders.Contains(h.Key)) continue;
                Response.Headers[h.Key] = h.Value.ToArray();
            }
            foreach (var h in resp.Content.Headers)
            {
                if (HopByHopHeaders.Contains(h.Key)) continue;
                if (h.Key.Equals("Content-Type", StringComparison.OrdinalIgnoreCase)) continue;
                Response.Headers[h.Key] = h.Value.ToArray();
            }

            return File(bytes, contentType);
        }
        catch (TaskCanceledException) when (ct.IsCancellationRequested)
        {
            return StatusCode(499);
        }
        catch (Exception ex)
        {
            _log.LogWarning(ex, "Python proxy failed for {Path}", target);
            return StatusCode(502, new { error = "PYTHON_PROXY_FAILED", path = target });
        }
    }
}

