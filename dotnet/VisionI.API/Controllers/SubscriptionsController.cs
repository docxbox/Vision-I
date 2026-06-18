using Microsoft.AspNetCore.Authorization;
using Microsoft.AspNetCore.Mvc;
using VisionI.API.Services;

namespace VisionI.API.Controllers;

[ApiController]
[Route("api/subscriptions")]
[Authorize]
public class SubscriptionsController : ControllerBase
{
    private readonly PythonApiClient _python;
    private readonly ILogger<SubscriptionsController> _log;

    public SubscriptionsController(PythonApiClient python, ILogger<SubscriptionsController> log)
    {
        _python = python;
        _log = log;
    }

    [HttpGet]
    public async Task<IActionResult> List([FromQuery(Name = "user_id")] string userId, CancellationToken ct)
    {
        var json = await _python.GetAsync($"/subscriptions?user_id={Uri.EscapeDataString(userId)}", ct);
        if (json is null)
            return StatusCode(502, new { error = "SUBSCRIPTIONS_UNAVAILABLE" });

        return Content(json.RootElement.GetRawText(), "application/json");
    }

    [HttpPost]
    public async Task<IActionResult> Create([FromQuery(Name = "user_id")] string userId, [FromBody] object body, CancellationToken ct)
    {
        var json = await _python.PostAsync($"/subscriptions?user_id={Uri.EscapeDataString(userId)}", body, ct);
        if (json is null)
            return StatusCode(502, new { error = "SUBSCRIPTION_CREATE_FAILED" });

        return Content(json.RootElement.GetRawText(), "application/json");
    }

    [HttpDelete("{subscriptionId}")]
    public async Task<IActionResult> Delete(string subscriptionId, [FromQuery(Name = "user_id")] string userId, CancellationToken ct)
    {
        var ok = await _python.DeleteAsync($"/subscriptions/{Uri.EscapeDataString(subscriptionId)}?user_id={Uri.EscapeDataString(userId)}", ct);
        if (!ok)
        {
            _log.LogWarning("Subscription delete failed for {SubscriptionId}", subscriptionId);
            return StatusCode(502, new { error = "SUBSCRIPTION_DELETE_FAILED" });
        }

        return NoContent();
    }
}
