using System.Security.Claims;
using System.Text.Json;
using Microsoft.AspNetCore.Authorization;
using Microsoft.AspNetCore.Mvc;
using Microsoft.EntityFrameworkCore;
using VisionI.API.Infrastructure;
using VisionI.API.Models;
using VisionI.API.Models.Entities;
using VisionI.API.Models.Requests;
using VisionI.API.Repositories;
using VisionI.API.Services;

namespace VisionI.API.Controllers;

[ApiController]
[Route("api/workspaces")]
[Authorize]
[Produces("application/json")]
public class WorkspacesController : ControllerBase
{
    private readonly IWorkspaceRepository _repo;
    private readonly IWorkspaceComposerService _composer;
    private readonly AppDbContext _db;

    public WorkspacesController(IWorkspaceRepository repo, IWorkspaceComposerService composer, AppDbContext db)
    {
        _repo = repo;
        _composer = composer;
        _db = db;
    }

    // ── Management ──────────────────────────────────────────────────────────

    [HttpGet]
    [Authorize(Roles = "Viewer,Analyst,Admin")]
    public async Task<IActionResult> List(CancellationToken ct)
    {
        await EnsureDataDrivenWorkspacesAsync(ct);
        // Object-level RBAC: hide other users' private workspaces.
        var items = await _repo.ListVisibleAsync(CurrentUserId, IsAdmin, ct);
        return Ok(new { total = items.Count, items });
    }

    [HttpGet("{slug}")]
    [Authorize(Roles = "Viewer,Analyst,Admin")]
    public async Task<IActionResult> Get(string slug, CancellationToken ct)
    {
        var ws = await _repo.GetBySlugAsync(slug, ct);
        if (ws is null) return NotFound();
        if (!CanAccess(ws)) return NotFound();   // object-level RBAC: hide private workspaces
        return Ok(MapDetail(ws));
    }

    [HttpPost]
    [Authorize(Roles = "Analyst,Admin")]
    public async Task<IActionResult> Create([FromBody] CreateWorkspaceRequest req, CancellationToken ct)
    {
        if (await _repo.ExistsAsync(req.Slug, ct))
            return Conflict(new { error = "Slug already exists" });

        if ((req.Queries?.Count(q => q.IsActive) ?? 0) == 0)
            return BadRequest(new { error = "At least one active query is required" });

        if ((req.SourceProfiles?.Count(s => s.IsEnabled) ?? 0) == 0)
            return BadRequest(new { error = "At least one enabled source profile is required" });

        var workspace = new Workspace
        {
            Slug = req.Slug,
            Title = req.Title,
            Description = req.Description,
            Classification = req.Classification,
            DefaultWindowHours = req.DefaultWindowHours,
            Visibility = req.Visibility ?? "private",
            CreatedBy = User.FindFirstValue(ClaimTypes.NameIdentifier) ?? User.Identity?.Name ?? "unknown",
            GeoFilters = req.GeoFilters?.Select(f => new WorkspaceGeoFilter
            {
                FilterType = f.FilterType, Name = f.Name,
                MinLat = f.MinLat, MaxLat = f.MaxLat, MinLon = f.MinLon, MaxLon = f.MaxLon,
                GeoJson = f.GeoJson,
            }).ToList() ?? [],
            Queries = req.Queries?.Select(q => new WorkspaceQuery
            {
                Query = q.Query, Priority = q.Priority, IsActive = q.IsActive,
            }).ToList() ?? [],
            Entities = req.Entities?.Select(e => new WorkspaceEntity
            {
                EntityKey = e.EntityKey, DisplayName = e.DisplayName,
                EntityType = e.EntityType, IsPrimary = e.IsPrimary, Notes = e.Notes,
            }).ToList() ?? [],
            SourceProfiles = req.SourceProfiles?.Select(s => new WorkspaceSourceProfile
            {
                SourceName = s.SourceName, IsEnabled = s.IsEnabled, SettingsJson = s.SettingsJson,
            }).ToList() ?? [],
        };

        var created = await _repo.CreateAsync(workspace, ct);
        _db.AuditLogs.Add(new AuditLog {
            UserId    = User.FindFirstValue(ClaimTypes.NameIdentifier) ?? "",
            Action    = "workspace.create",
            Resource  = $"workspace:{created.Slug}",
            IpAddress = HttpContext.Connection.RemoteIpAddress?.ToString(),
        });
        await _db.SaveChangesAsync(ct);
        return CreatedAtAction(nameof(Get), new { slug = created.Slug }, MapDetail(created));
    }

    [HttpPut("{slug}")]
    [Authorize(Roles = "Analyst,Admin")]
    public async Task<IActionResult> Update(string slug, [FromBody] UpdateWorkspaceRequest req, CancellationToken ct)
    {
        var current = await _repo.GetBySlugAsync(slug, ct);
        if (current is null) return NotFound();

        // Validate effective post-update state, not just the incoming partial payload.
        // If a collection is omitted the existing rows survive unchanged, so use those for the check.
        var effectiveQueryCount   = req.Queries        is not null ? req.Queries.Count        : current.Queries.Count;
        var effectiveActiveQuery  = req.Queries        is not null ? req.Queries.Any(q => q.IsActive)        : current.Queries.Any(q => q.IsActive);
        var effectiveSourceCount  = req.SourceProfiles is not null ? req.SourceProfiles.Count  : current.SourceProfiles.Count;
        var effectiveEnabledSource= req.SourceProfiles is not null ? req.SourceProfiles.Any(s => s.IsEnabled) : current.SourceProfiles.Any(s => s.IsEnabled);

        if (effectiveQueryCount > 0 && !effectiveActiveQuery)
            return BadRequest(new { error = "At least one active query is required" });

        if (effectiveQueryCount > 0 && !effectiveEnabledSource)
            return BadRequest(new { error = "At least one enabled source profile is required" });

        var updated = await _repo.UpdateAsync(slug, req, ct);
        if (updated is null) return NotFound();
        return Ok(MapDetail(updated));
    }

    [HttpDelete("{slug}")]
    [Authorize(Roles = "Admin")]
    public async Task<IActionResult> Delete(string slug, CancellationToken ct)
    {
        var deleted = await _repo.DeleteAsync(slug, ct);
        if (deleted)
        {
            _db.AuditLogs.Add(new AuditLog {
                UserId    = User.FindFirstValue(ClaimTypes.NameIdentifier) ?? "",
                Action    = "workspace.delete",
                Resource  = $"workspace:{slug}",
                IpAddress = HttpContext.Connection.RemoteIpAddress?.ToString(),
            });
            await _db.SaveChangesAsync(ct);
        }
        return deleted ? NoContent() : NotFound();
    }

    // ── Analyst payloads ────────────────────────────────────────────────────

    [HttpGet("{slug}/overview")]
    [Authorize(Roles = "Viewer,Analyst,Admin")]
    public async Task<IActionResult> Overview(string slug, CancellationToken ct)
    {
        var result = await _composer.GetOverviewAsync(slug, ct);
        return result is null ? NotFound() : Ok(result);
    }

    [HttpGet("{slug}/map")]
    [Authorize(Roles = "Viewer,Analyst,Admin")]
    public async Task<IActionResult> Map(string slug, CancellationToken ct)
    {
        var result = await _composer.GetMapAsync(slug, ct);
        return result is null ? NotFound() : Ok(result);
    }

    [HttpGet("{slug}/developments")]
    [Authorize(Roles = "Viewer,Analyst,Admin")]
    public async Task<IActionResult> Developments(string slug, CancellationToken ct)
    {
        var result = await _composer.GetDevelopmentsAsync(slug, ct);
        return result is null ? NotFound() : Ok(result);
    }

    [HttpGet("{slug}/entities")]
    [Authorize(Roles = "Viewer,Analyst,Admin")]
    public async Task<IActionResult> Entities(string slug, CancellationToken ct)
    {
        var result = await _composer.GetEntitiesAsync(slug, ct);
        return result is null ? NotFound() : Ok(result);
    }

    [HttpGet("{slug}/assets")]
    [Authorize(Roles = "Viewer,Analyst,Admin")]
    public async Task<IActionResult> Assets(string slug, CancellationToken ct)
    {
        var result = await _composer.GetAssetsAsync(slug, ct);
        return result is null ? NotFound() : Ok(result);
    }

    [HttpGet("{slug}/sentiment")]
    [Authorize(Roles = "Viewer,Analyst,Admin")]
    public async Task<IActionResult> Sentiment(string slug, CancellationToken ct)
    {
        var result = await _composer.GetSentimentAsync(slug, ct);
        return result is null ? NotFound() : Ok(result);
    }

    [HttpGet("{slug}/correlation")]
    [Authorize(Roles = "Viewer,Analyst,Admin")]
    public async Task<IActionResult> Correlation(string slug, CancellationToken ct)
    {
        var result = await _composer.GetCorrelationAsync(slug, ct);
        return result is null ? NotFound() : Ok(result);
    }

    [HttpGet("{slug}/actions")]
    [Authorize(Roles = "Viewer,Analyst,Admin")]
    public async Task<IActionResult> Actions(string slug, CancellationToken ct)
    {
        var result = await _composer.GetActionsAsync(slug, ct);
        return result is null ? NotFound() : Ok(result);
    }

    [HttpPost("{slug}/refresh")]
    [Authorize(Roles = "Analyst,Admin")]
    public async Task<IActionResult> Refresh(string slug, CancellationToken ct)
    {
        if (!await _repo.ExistsAsync(slug, ct)) return NotFound();
        await _composer.RefreshAsync(slug, ct);
        return Accepted(new { slug, refreshed = true });
    }

    [HttpPost("{slug}/decisions")]
    [Authorize(Roles = "Analyst,Admin")]
    public async Task<IActionResult> LogDecision(
        string slug, [FromBody] WorkspaceDecisionRequest req, CancellationToken ct)
    {
        var ws = await _repo.GetBySlugAsync(slug, ct);
        if (ws is null) return NotFound();
        if (!CanAccess(ws)) return NotFound();   // object-level RBAC: hide private workspaces

        var analyst  = User.FindFirstValue(ClaimTypes.Name) ?? "analyst";
        var contextJson = JsonSerializer.Serialize(new
        {
            action_title   = req.ActionTitle,
            action_type    = req.ActionType,
            outcome        = req.Outcome,
            analyst_note   = req.AnalystNote,
            confidence     = req.Confidence,
            evidence_count = req.EvidenceEventIds?.Count ?? 0,
            evidence_ids   = req.EvidenceEventIds,
            workspace_slug = slug,
            logged_at      = DateTime.UtcNow.ToString("O")
        });

        var ctx = new WorkspaceDecisionContext
        {
            WorkspaceId    = ws.Id,
            EventId        = req.EventId ?? "",
            RelevanceScore = req.Confidence,
            ContextJson    = contextJson,
        };
        await _repo.SaveDecisionContextAsync(ctx, ct);

        return Ok(new WorkspaceDecisionResultDto(
            ContextId:     ctx.Id,
            WorkspaceSlug: slug,
            ActionTitle:   req.ActionTitle,
            Outcome:       req.Outcome,
            CreatedAt:     ctx.CreatedAt
        ));
    }

    [HttpGet("{slug}/decisions")]
    [Authorize(Roles = "Viewer,Analyst,Admin")]
    public async Task<IActionResult> GetDecisions(
        string slug,
        [FromQuery] int limit = 50,
        [FromQuery] int offset = 0,
        CancellationToken ct = default)
    {
        if (limit is < 1 or > 200) limit = 50;
        if (offset < 0) offset = 0;
        var ws = await _repo.GetBySlugAsync(slug, ct);
        if (ws is null) return NotFound();
        if (!CanAccess(ws)) return NotFound();   // object-level RBAC: hide private workspaces
        var items = await _repo.GetDecisionContextsAsync(ws.Id, limit, offset, ct);
        return Ok(new { total = items.Count, limit, offset, items });
    }

    // ── Saved Queries (per-workspace search shortcuts) ──────────────────────

    [HttpGet("{slug}/queries")]
    [Authorize(Roles = "Viewer,Analyst,Admin")]
    public async Task<IActionResult> GetQueries(string slug, CancellationToken ct = default)
    {
        var ws = await _repo.GetBySlugAsync(slug, ct);
        if (ws is null) return NotFound();
        if (!CanAccess(ws)) return NotFound();   // object-level RBAC: hide private workspaces
        var queries = ws.Queries
            .Where(q => q.IsActive)
            .OrderBy(q => q.Priority)
            .Select(q => new { id = q.Id, query = q.Query, is_active = q.IsActive,
                                priority = q.Priority, created_at = q.CreatedAt })
            .ToList();
        return Ok(new { queries });
    }

    [HttpPost("{slug}/queries")]
    [Authorize(Roles = "Analyst,Admin")]
    public async Task<IActionResult> AddQuery(string slug, [FromBody] AddWorkspaceQueryRequest req, CancellationToken ct = default)
    {
        if (string.IsNullOrWhiteSpace(req.Query))
            return BadRequest(new { error = "query must not be empty" });
        var ws = await _repo.GetBySlugAsync(slug, ct);
        if (ws is null) return NotFound();
        if (!CanAccess(ws)) return NotFound();   // object-level RBAC: hide private workspaces
        var newQuery = await _repo.AddQueryAsync(ws.Id, req.Query, ct);
        return StatusCode(201, new { id = newQuery!.Id, query = newQuery.Query });
    }

    [HttpDelete("{slug}/queries/{queryId}")]
    [Authorize(Roles = "Analyst,Admin")]
    public async Task<IActionResult> RemoveQuery(string slug, Guid queryId, CancellationToken ct = default)
    {
        var ws = await _repo.GetBySlugAsync(slug, ct);
        if (ws is null) return NotFound();
        if (!CanAccess(ws)) return NotFound();   // object-level RBAC: hide private workspaces
        var ok = await _repo.DeactivateQueryAsync(queryId, ct);
        return ok ? NoContent() : NotFound();
    }

    // ── Workspace Tasks ──────────────────────────────────────────────────────

    [HttpGet("{slug}/tasks")]
    [Authorize(Roles = "Viewer,Analyst,Admin")]
    public async Task<IActionResult> GetTasks(string slug, CancellationToken ct = default)
    {
        var ws = await _repo.GetBySlugAsync(slug, ct);
        if (ws is null) return NotFound();
        if (!CanAccess(ws)) return NotFound();   // object-level RBAC: hide private workspaces
        var tasks = await _db.WorkspaceTasks
            .Where(t => t.WorkspaceId == ws.Id)
            .OrderByDescending(t => t.CreatedAt)
            .Select(t => new
            {
                id = t.Id, title = t.Title, description = t.Description,
                status = t.Status, priority = t.Priority,
                assignee_user_id = t.AssigneeUserId, assignee_display_name = t.AssigneeDisplayName,
                created_by = t.CreatedByUserId, created_at = t.CreatedAt, completed_at = t.CompletedAt,
            })
            .ToListAsync(ct);
        return Ok(new { tasks });
    }

    [HttpPost("{slug}/tasks")]
    [Authorize(Roles = "Analyst,Admin")]
    public async Task<IActionResult> CreateTask(string slug, [FromBody] CreateWorkspaceTaskRequest req, CancellationToken ct = default)
    {
        var ws = await _repo.GetBySlugAsync(slug, ct);
        if (ws is null) return NotFound();
        if (!CanAccess(ws)) return NotFound();   // object-level RBAC: hide private workspaces
        var priority = NormalizePriority(req.Priority);
        if (priority is null) return BadRequest(new { error = "priority must be low, medium, high, or critical" });

        string? assigneeDisplayName = null;
        if (!string.IsNullOrWhiteSpace(req.AssigneeUserId))
        {
            var assignee = await _db.Users.FindAsync([req.AssigneeUserId], ct);
            assigneeDisplayName = assignee?.UserName;
        }

        var task = new WorkspaceTask
        {
            Id = Guid.NewGuid(),
            WorkspaceId = ws.Id,
            Title = req.Title.Trim(),
            Description = req.Description?.Trim(),
            Status = "open",
            Priority = priority,
            CreatedByUserId = User.FindFirstValue(ClaimTypes.NameIdentifier),
            AssigneeUserId = req.AssigneeUserId,
            AssigneeDisplayName = assigneeDisplayName,
            CreatedAt = DateTime.UtcNow,
            UpdatedAt = DateTime.UtcNow,
        };
        _db.WorkspaceTasks.Add(task);
        await _db.SaveChangesAsync(ct);
        return StatusCode(201, new { id = task.Id, title = task.Title, status = task.Status });
    }

    [HttpPatch("{slug}/tasks/{taskId}")]
    [Authorize(Roles = "Analyst,Admin")]
    public async Task<IActionResult> UpdateTask(string slug, Guid taskId, [FromBody] UpdateWorkspaceTaskRequest req, CancellationToken ct = default)
    {
        var ws = await _repo.GetBySlugAsync(slug, ct);
        if (ws is null) return NotFound();
        if (!CanAccess(ws)) return NotFound();   // object-level RBAC: hide private workspaces
        var task = await _db.WorkspaceTasks.FirstOrDefaultAsync(t => t.Id == taskId && t.WorkspaceId == ws.Id, ct);
        if (task is null) return NotFound();

        if (req.Title is not null)       task.Title = req.Title.Trim();
        if (req.Description is not null) task.Description = req.Description.Trim();
        if (req.Priority is not null)
        {
            var priority = NormalizePriority(req.Priority);
            if (priority is null) return BadRequest(new { error = "priority must be low, medium, high, or critical" });
            task.Priority = priority;
        }
        if (req.Status is not null)
        {
            var status = NormalizeStatus(req.Status);
            if (status is null) return BadRequest(new { error = "status must be open, in_progress, done, or cancelled" });
            task.Status = status;
            if (status == "done" && task.CompletedAt is null)
                task.CompletedAt = DateTime.UtcNow;
        }
        if (req.AssigneeUserId is not null)
        {
            task.AssigneeUserId = req.AssigneeUserId;
            var assignee = await _db.Users.FindAsync([req.AssigneeUserId], ct);
            task.AssigneeDisplayName = assignee?.UserName;
        }
        task.UpdatedAt = DateTime.UtcNow;
        await _db.SaveChangesAsync(ct);
        return Ok(new { id = task.Id, status = task.Status });
    }

    [HttpDelete("{slug}/tasks/{taskId}")]
    [Authorize(Roles = "Analyst,Admin")]
    public async Task<IActionResult> DeleteTask(string slug, Guid taskId, CancellationToken ct = default)
    {
        var ws = await _repo.GetBySlugAsync(slug, ct);
        if (ws is null) return NotFound();
        if (!CanAccess(ws)) return NotFound();   // object-level RBAC: hide private workspaces
        var task = await _db.WorkspaceTasks.FirstOrDefaultAsync(t => t.Id == taskId && t.WorkspaceId == ws.Id, ct);
        if (task is null) return NotFound();
        _db.WorkspaceTasks.Remove(task);
        await _db.SaveChangesAsync(ct);
        return NoContent();
    }

    // ── Evidence board ──────────────────────────────────────────────────────

    [HttpGet("{slug}/evidence")]
    [Authorize(Roles = "Viewer,Analyst,Admin")]
    public async Task<IActionResult> GetEvidence(string slug, CancellationToken ct = default)
    {
        var ws = await _repo.GetBySlugAsync(slug, ct);
        if (ws is null) return NotFound();
        if (!CanAccess(ws)) return NotFound();   // object-level RBAC: hide private workspaces
        var items = await _db.WorkspaceEvidence
            .Where(x => x.WorkspaceId == ws.Id)
            .OrderByDescending(x => x.CreatedAt)
            .Select(x => new
            {
                Id = x.Id, ItemType = x.ItemType, ItemId = x.ItemId,
                Title = x.Title, Source = x.Source, Note = x.Note,
                PinnedBy = x.PinnedByDisplayName, CreatedAt = x.CreatedAt,
            })
            .ToListAsync(ct);
        return Ok(new { Total = items.Count, Items = items });
    }

    [HttpPost("{slug}/evidence")]
    [Authorize(Roles = "Analyst,Admin")]
    public async Task<IActionResult> PinEvidence(string slug, [FromBody] PinEvidenceRequest req, CancellationToken ct = default)
    {
        var ws = await _repo.GetBySlugAsync(slug, ct);
        if (ws is null) return NotFound();
        if (!CanAccess(ws)) return NotFound();   // object-level RBAC: hide private workspaces

        var itemType = NormalizeEvidenceType(req.ItemType);
        if (itemType is null) return BadRequest(new { error = "itemType must be event, asset, entity, signal, or narrative" });
        if (string.IsNullOrWhiteSpace(req.ItemId)) return BadRequest(new { error = "itemId is required" });

        // Idempotent pin: if it's already on the board, return the existing row.
        var existing = await _db.WorkspaceEvidence
            .FirstOrDefaultAsync(x => x.WorkspaceId == ws.Id && x.ItemType == itemType && x.ItemId == req.ItemId, ct);
        if (existing is not null)
            return Ok(new { id = existing.Id, already_pinned = true });

        var ev = new WorkspaceEvidence
        {
            Id = Guid.NewGuid(),
            WorkspaceId = ws.Id,
            ItemType = itemType,
            ItemId = req.ItemId.Trim(),
            Title = (req.Title ?? req.ItemId).Trim(),
            Source = req.Source?.Trim(),
            Note = req.Note?.Trim(),
            PinnedByUserId = User.FindFirstValue(ClaimTypes.NameIdentifier),
            PinnedByDisplayName = User.FindFirstValue(ClaimTypes.Name) ?? User.Identity?.Name,
            CreatedAt = DateTime.UtcNow,
        };
        _db.WorkspaceEvidence.Add(ev);
        await _db.SaveChangesAsync(ct);
        return StatusCode(201, new { id = ev.Id, already_pinned = false });
    }

    [HttpDelete("{slug}/evidence/{evidenceId}")]
    [Authorize(Roles = "Analyst,Admin")]
    public async Task<IActionResult> UnpinEvidence(string slug, Guid evidenceId, CancellationToken ct = default)
    {
        var ws = await _repo.GetBySlugAsync(slug, ct);
        if (ws is null) return NotFound();
        if (!CanAccess(ws)) return NotFound();   // object-level RBAC: hide private workspaces
        var ev = await _db.WorkspaceEvidence.FirstOrDefaultAsync(x => x.Id == evidenceId && x.WorkspaceId == ws.Id, ct);
        if (ev is null) return NotFound();
        _db.WorkspaceEvidence.Remove(ev);
        await _db.SaveChangesAsync(ct);
        return NoContent();
    }

    // ── Object-level access control ─────────────────────────────────────────

    private string? CurrentUserId => User.FindFirstValue(ClaimTypes.NameIdentifier);
    private bool IsAdmin => User.IsInRole("Admin");

    /// <summary>
    /// A workspace is accessible if you're Admin, it's not private, it's yours, or it's a
    /// shared/seed workspace (system/null creator).
    /// </summary>
    private bool CanAccess(Workspace ws)
        => IsAdmin
           || !string.Equals(ws.Visibility, "private", StringComparison.OrdinalIgnoreCase)
           || string.IsNullOrEmpty(ws.CreatedBy)
           || string.Equals(ws.CreatedBy, "system", StringComparison.OrdinalIgnoreCase)
           || string.Equals(ws.CreatedBy, CurrentUserId, StringComparison.Ordinal);

    private static string? NormalizeEvidenceType(string? value)
    {
        var t = (value ?? "").Trim().ToLowerInvariant();
        return t is "event" or "asset" or "entity" or "signal" or "narrative" ? t : null;
    }

    // ── Helpers ─────────────────────────────────────────────────────────────

    private static WorkspaceDetailDto MapDetail(Workspace ws) => new(
        ws.Id, ws.Slug, ws.Title, ws.Description, ws.Status, ws.Classification,
        ws.DefaultWindowHours, ws.CreatedAt, ws.UpdatedAt,
        ws.GeoFilters.Select(f => new WorkspaceGeoFilterDto(
            f.Id, f.FilterType, f.Name, f.MinLat, f.MaxLat, f.MinLon, f.MaxLon)).ToList(),
        ws.Queries.Select(q => new WorkspaceQueryDto(q.Id, q.Query, q.Priority, q.IsActive)).ToList(),
        ws.Entities.Select(e => new WorkspaceEntityRefDto(
            e.Id, e.EntityKey, e.EntityType, e.DisplayName, e.IsPrimary)).ToList(),
        ws.SourceProfiles.Select(s => new WorkspaceSourceProfileDto(
            s.Id, s.SourceName, s.IsEnabled)).ToList()
    );

    private static string? NormalizePriority(string? value)
    {
        var p = (value ?? "medium").Trim().ToLowerInvariant();
        return p is "low" or "medium" or "high" or "critical" ? p : null;
    }

    private static string? NormalizeStatus(string? value)
    {
        var s = (value ?? "").Trim().ToLowerInvariant();
        return s is "open" or "in_progress" or "done" or "cancelled" ? s : null;
    }

    private async Task EnsureDataDrivenWorkspacesAsync(CancellationToken ct)
    {
        if (await _db.Workspaces.CountAsync(ct) >= 4)
            return;

        var seeds = new[]
        {
            new
            {
                Slug = "global-aviation-anomalies",
                Title = "Global Aviation Anomalies",
                Description = "Tracks aircraft, airport, reroute, and GPS interference signals across the live event stream.",
                Classification = "WATCHLIST",
                Query = "flight OR aircraft OR airport OR reroute OR gps",
                Sources = new[] { "opensky", "gdelt", "rss" },
                Entities = new[] { ("aviation", "domain"), ("gps", "signal") }
            },
            new
            {
                Slug = "indo-pacific-maritime",
                Title = "Indo-Pacific Maritime",
                Description = "Monitors maritime, naval, Taiwan Strait, South China Sea, and regional trade disruption signals.",
                Classification = "ELEVATED",
                Query = "taiwan OR china OR maritime OR vessel OR south china sea",
                Sources = new[] { "ais", "gdelt", "news" },
                Entities = new[] { ("China", "actor"), ("Taiwan", "region") }
            },
            new
            {
                Slug = "cyber-and-infrastructure",
                Title = "Cyber & Critical Infrastructure",
                Description = "Combines CISA KEV, technology news, market stress, and infrastructure disruption reporting.",
                Classification = "ACTIVE",
                Query = "cyber OR cisa OR vulnerability OR outage OR infrastructure",
                Sources = new[] { "cisa_kev", "hackernews", "gdelt" },
                Entities = new[] { ("CISA", "source"), ("infrastructure", "domain") }
            },
            new
            {
                Slug = "climate-hazards-and-health",
                Title = "Climate, Hazards & Health",
                Description = "Watches earthquakes, fires, severe weather, and public-health alerts that can cascade into operations.",
                Classification = "MONITORING",
                Query = "earthquake OR wildfire OR weather OR who OR health",
                Sources = new[] { "usgs", "firms", "nws", "who" },
                Entities = new[] { ("hazards", "domain"), ("health", "domain") }
            }
        };

        foreach (var seed in seeds)
        {
            if (await _db.Workspaces.AnyAsync(w => w.Slug == seed.Slug, ct))
                continue;

            var workspace = new Workspace
            {
                Id = Guid.NewGuid(),
                Slug = seed.Slug,
                Title = seed.Title,
                Description = seed.Description,
                Status = "active",
                Classification = seed.Classification,
                DefaultWindowHours = 24,
                Visibility = "team",
                CreatedBy = "system",
                CreatedAt = DateTime.UtcNow,
                UpdatedAt = DateTime.UtcNow,
                Queries =
                [
                    new WorkspaceQuery
                    {
                        Id = Guid.NewGuid(),
                        Query = seed.Query,
                        Priority = 50,
                        IsActive = true,
                        CreatedAt = DateTime.UtcNow,
                        UpdatedAt = DateTime.UtcNow
                    }
                ],
                SourceProfiles = seed.Sources.Select(source => new WorkspaceSourceProfile
                {
                    Id = Guid.NewGuid(),
                    SourceName = source,
                    IsEnabled = true,
                    CreatedAt = DateTime.UtcNow
                }).ToList(),
                Entities = seed.Entities.Select(entity => new WorkspaceEntity
                {
                    Id = Guid.NewGuid(),
                    EntityKey = entity.Item1.ToLowerInvariant(),
                    DisplayName = entity.Item1,
                    EntityType = entity.Item2,
                    IsPrimary = true,
                    CreatedAt = DateTime.UtcNow
                }).ToList()
            };

            foreach (var q in workspace.Queries)
                q.WorkspaceId = workspace.Id;
            foreach (var s in workspace.SourceProfiles)
                s.WorkspaceId = workspace.Id;
            foreach (var e in workspace.Entities)
                e.WorkspaceId = workspace.Id;

            _db.Workspaces.Add(workspace);
        }

        await _db.SaveChangesAsync(ct);
    }
}
