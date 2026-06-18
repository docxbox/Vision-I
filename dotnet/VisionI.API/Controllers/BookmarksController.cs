using Microsoft.AspNetCore.Authorization;
using Microsoft.AspNetCore.Mvc;
using VisionI.API.Services;

namespace VisionI.API.Controllers;

[ApiController]
[Route("api/bookmarks")]
[Authorize]
public class BookmarksController : ControllerBase
{
    private readonly PythonApiClient _python;
    private readonly ILogger<BookmarksController> _log;

    public BookmarksController(PythonApiClient python, ILogger<BookmarksController> log)
    {
        _python = python;
        _log = log;
    }

    [HttpGet]
    public async Task<IActionResult> List([FromQuery(Name = "user_id")] string userId, CancellationToken ct)
    {
        var json = await _python.GetAsync($"/bookmarks?user_id={Uri.EscapeDataString(userId)}", ct);
        if (json is null)
            return StatusCode(502, new { error = "BOOKMARKS_UNAVAILABLE" });

        return Content(json.RootElement.GetRawText(), "application/json");
    }

    [HttpPost]
    public async Task<IActionResult> Create([FromQuery(Name = "user_id")] string userId, [FromBody] object body, CancellationToken ct)
    {
        var json = await _python.PostAsync($"/bookmarks?user_id={Uri.EscapeDataString(userId)}", body, ct);
        if (json is null)
            return StatusCode(502, new { error = "BOOKMARK_CREATE_FAILED" });

        return Content(json.RootElement.GetRawText(), "application/json");
    }

    [HttpDelete("{bookmarkId}")]
    public async Task<IActionResult> Delete(string bookmarkId, [FromQuery(Name = "user_id")] string userId, CancellationToken ct)
    {
        var ok = await _python.DeleteAsync($"/bookmarks/{Uri.EscapeDataString(bookmarkId)}?user_id={Uri.EscapeDataString(userId)}", ct);
        if (!ok)
        {
            _log.LogWarning("Bookmark delete failed for {BookmarkId}", bookmarkId);
            return StatusCode(502, new { error = "BOOKMARK_DELETE_FAILED" });
        }

        return NoContent();
    }
}
