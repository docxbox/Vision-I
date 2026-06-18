using Microsoft.AspNetCore.Authorization;
using Microsoft.AspNetCore.Identity;
using Microsoft.AspNetCore.Mvc;
using Microsoft.EntityFrameworkCore;
using System.Security.Claims;
using VisionI.API.Infrastructure;
using VisionI.API.Models.Entities;
using VisionI.API.Models.Requests;
using VisionI.API.Models.Responses;
using System.ComponentModel.DataAnnotations;
using VisionI.API.Services;

namespace VisionI.API.Controllers;

[ApiController]
[Route("api/admin")]
[Authorize]
[Produces("application/json")]
public class AdminController : ControllerBase
{
    private const string OpenRouterFreeModelChain = NativeLlmService.OpenRouterFreeModelChain;

    private static readonly HashSet<string> ApiKeyOptionalProviders = new(StringComparer.OrdinalIgnoreCase)
    {
        "ollama",
    };

    private readonly UserManager<AppUser> _users;
    private readonly IIntelligenceService _intelligence;
    private readonly INativeLlmService _llm;
    private readonly AppDbContext _db;
    private readonly LlmConfigCryptoService _crypto;
    private readonly ILogger<AdminController> _log;

    public AdminController(
        UserManager<AppUser> users,
        IIntelligenceService intelligence,
        INativeLlmService llm,
        AppDbContext db,
        LlmConfigCryptoService crypto,
        ILogger<AdminController> log)
    {
        _users = users;
        _intelligence = intelligence;
        _llm = llm;
        _db = db;
        _crypto = crypto;
        _log = log;
    }

    /// <summary>Returns system health for the admin dashboard.</summary>
    [HttpGet("health")]
    [Authorize(Roles = "Admin")]
    public async Task<IActionResult> SystemHealth(CancellationToken ct = default)
    {
        var pythonHealth = await _intelligence.GetPythonDocumentAsync("/admin/health", ct);
        var dbOk = await _intelligence.CanConnectDbAsync(ct);

        return Ok(new
        {
            status = pythonHealth != null && dbOk ? "ok" : "degraded",
            db_available = dbOk,
            python = pythonHealth?.RootElement,
            timestamp = DateTime.UtcNow.ToString("O"),
        });
    }

    /// <summary>Returns all users with their current role.</summary>
    [HttpGet("users")]
    [Authorize(Roles = "Admin")]
    public async Task<IActionResult> GetUsers()
    {
        var users = await _users.Users.ToListAsync();
        var result = new List<object>();

        foreach (var user in users)
        {
            var roles = await _users.GetRolesAsync(user);
            result.Add(new UserResponse(
                UserId: user.Id,
                Email: user.Email ?? "",
                DisplayName: user.DisplayName,
                Role: roles.FirstOrDefault() ?? "Viewer",
                CreatedAt: user.CreatedAt,
                IsActive: user.IsActive
            ));
        }

        return Ok(new { total = result.Count, users = result });
    }

    /// <summary>Replaces a user's role.</summary>
    [HttpPatch("users/{userId}/role")]
    [Authorize(Roles = "Admin")]
    public async Task<IActionResult> AssignRole(
        string userId, [FromBody] AssignRoleRequest req)
    {
        var validRoles = new[] { "Viewer", "Analyst", "Admin" };
        if (!validRoles.Contains(req.Role))
            return BadRequest(new ApiError("INVALID_ROLE",
                $"Role must be one of: {string.Join(", ", validRoles)}"));

        var user = await _users.FindByIdAsync(userId);
        if (user == null) return NotFound(new ApiError("USER_NOT_FOUND", "User not found."));

        var currentRoles = await _users.GetRolesAsync(user);
        await _users.RemoveFromRolesAsync(user, currentRoles);
        await _users.AddToRoleAsync(user, req.Role);

        _log.LogInformation("Role changed: {UserId} → {Role}", userId, req.Role);

        var actorId = User.FindFirstValue(ClaimTypes.NameIdentifier) ?? "";
        _db.AuditLogs.Add(new VisionI.API.Models.Entities.AuditLog {
            UserId   = actorId,
            Action   = "role.change",
            Resource = $"user:{userId}",
            Detail   = System.Text.Json.JsonSerializer.Serialize(new { new_role = req.Role }),
            IpAddress = HttpContext.Connection.RemoteIpAddress?.ToString(),
        });
        await _db.SaveChangesAsync();

        return Ok(new { userId, role = req.Role, message = "Role updated successfully." });
    }

    /// <summary>Updates whether a user account is active.</summary>
    [HttpPatch("users/{userId}/status")]
    [Authorize(Roles = "Admin")]
    public async Task<IActionResult> SetUserStatus(
        string userId, [FromBody] bool isActive)
    {
        var user = await _users.FindByIdAsync(userId);
        if (user == null) return NotFound(new ApiError("USER_NOT_FOUND", "User not found."));

        // Do not allow self-deactivation.
        var selfId = User.FindFirstValue(ClaimTypes.NameIdentifier);
        if (userId == selfId && !isActive)
            return BadRequest(new ApiError("SELF_DEACTIVATION", "You cannot deactivate your own account."));

        user.IsActive = isActive;
        await _users.UpdateAsync(user);

        _log.LogInformation("User {UserId} active={IsActive}", userId, isActive);

        var actorId = User.FindFirstValue(ClaimTypes.NameIdentifier) ?? "";
        _db.AuditLogs.Add(new VisionI.API.Models.Entities.AuditLog {
            UserId   = actorId,
            Action   = isActive ? "user.restore" : "user.suspend",
            Resource = $"user:{userId}",
            IpAddress = HttpContext.Connection.RemoteIpAddress?.ToString(),
        });
        await _db.SaveChangesAsync();

        return Ok(new { userId, isActive });
    }

    /// <summary>Returns tracked ingestion queries.</summary>
    [HttpGet("queries")]
    [Authorize(Roles = "Admin")]
    public async Task<IActionResult> GetQueries(CancellationToken ct = default)
    {
        var result = await _intelligence.GetPythonDocumentAsync("/admin/queries", ct);
        if (result == null) return StatusCode(502, "Intelligence layer unavailable.");
        return Content(result.RootElement.GetRawText(), "application/json");
    }

    /// <summary>Adds a tracked query.</summary>
    [HttpPost("queries")]
    [Authorize(Roles = "Admin")]
    public async Task<IActionResult> AddQuery(
        [FromBody] AddQueryRequest req, CancellationToken ct = default)
    {
        var json = await _intelligence.PostPythonJsonAsync("/admin/queries", new { query = req.Query }, ct);
        if (json == null) return StatusCode(502, "Intelligence layer unavailable.");

        _log.LogInformation("Tracked query added: '{Query}'", req.Query);
        return new ContentResult
        {
            StatusCode = StatusCodes.Status201Created,
            Content = json,
            ContentType = "application/json"
        };
    }

    /// <summary>Removes a tracked query.</summary>
    [HttpDelete("queries/{queryId:int}")]
    [Authorize(Roles = "Admin")]
    public async Task<IActionResult> DeleteQuery(int queryId, CancellationToken ct = default)
    {
        var result = await _intelligence.DeleteTrackedQueryAsync(queryId, ct);
        if (result == null) return StatusCode(502, "Intelligence layer unavailable.");
        return Content(result.RootElement.GetRawText(), "application/json");
    }

    /// <summary>Returns recent ingest jobs.</summary>
    [HttpGet("jobs")]
    [Authorize(Roles = "Admin")]
    public async Task<IActionResult> GetJobs(
        [FromQuery] int limit = 20,
        [FromQuery] string? status = null,
        CancellationToken ct = default)
    {
        var json = await _intelligence.GetPythonJsonAsync($"/admin/jobs?limit={limit}{(string.IsNullOrWhiteSpace(status) ? "" : $"&status={Uri.EscapeDataString(status)}")}", ct);
        if (json == null) return StatusCode(502, "Intelligence layer unavailable.");
        return Content(json, "application/json");
    }

    /// <summary>Returns aggregated event stats.</summary>
    [HttpGet("stats")]
    [Authorize(Roles = "Admin")]
    public async Task<IActionResult> GetStats(CancellationToken ct = default)
    {
        var json = await _intelligence.GetPythonJsonAsync("/admin/stats", ct);
        if (json == null) return StatusCode(502, "Intelligence layer unavailable.");
        return Content(json, "application/json");
    }

    /// <summary>Returns dead-letter queue entries.</summary>
    [HttpGet("dlq")]
    [Authorize(Roles = "Admin")]
    public async Task<IActionResult> GetDeadLetterQueue([FromQuery] int limit = 50, CancellationToken ct = default)
    {
        var json = await _intelligence.GetPythonJsonAsync($"/admin/dlq?limit={limit}", ct);
        if (json == null) return StatusCode(502, "Intelligence layer unavailable.");
        return Content(json, "application/json");
    }

    /// <summary>Retries one dead-letter entry.</summary>
    [HttpPost("dlq/retry")]
    [Authorize(Roles = "Admin")]
    public async Task<IActionResult> RetryDeadLetterQueue([FromQuery] int index = 0, CancellationToken ct = default)
    {
        var json = await _intelligence.PostPythonJsonAsync($"/admin/dlq/retry?index={index}", new { }, ct);
        if (json == null) return StatusCode(502, "Intelligence layer unavailable.");
        return Content(json, "application/json");
    }

    /// <summary>Returns signal and correlation stats.</summary>
    [HttpGet("signals/stats")]
    [Authorize(Roles = "Admin")]
    public async Task<IActionResult> GetSignalStats(CancellationToken ct = default)
    {
        var json = await _intelligence.GetPythonJsonAsync("/admin/signals/stats", ct);
        if (json == null) return StatusCode(502, "Intelligence layer unavailable.");
        return Content(json, "application/json");
    }

    /// <summary>Queues a live ingest run.</summary>
    [HttpPost("trigger-live")]
    [Authorize(Roles = "Admin")]
    public async Task<IActionResult> TriggerLive(CancellationToken ct = default)
    {
        var json = await _intelligence.PostPythonJsonAsync("/admin/trigger-live", new { }, ct);
        if (json == null) return StatusCode(502, "Intelligence layer unavailable.");
        return Content(json, "application/json");
    }

    /// <summary>Returns audit log entries.</summary>
    [HttpGet("audit-log")]
    [Authorize(Roles = "Admin")]
    public async Task<IActionResult> GetAuditLog(
        [FromQuery][Range(1, 200)] int limit = 50,
        [FromQuery] int offset = 0,
        [FromQuery] string? userId = null,
        [FromQuery] string? action = null,
        CancellationToken ct = default)
    {
        var query = _db.AuditLogs.AsQueryable();

        if (!string.IsNullOrEmpty(userId))
            query = query.Where(a => a.UserId == userId);
        if (!string.IsNullOrEmpty(action))
            query = query.Where(a => a.Action.Contains(action));

        var total = await query.CountAsync(ct);
        var entries = await query
            .OrderByDescending(a => a.Timestamp)
            .Skip(offset)
            .Take(limit)
            .Select(a => new
            {
                id = a.Id,
                user_id = a.UserId,
                action = a.Action,
                resource = a.Resource,
                detail = a.Detail,
                ip_address = a.IpAddress,
                timestamp = a.Timestamp.ToString("O"),
            })
            .ToListAsync(ct);

        // Attach display names when possible.
        var userIds = entries.Select(e => e.user_id).Distinct().ToList();
        var userNames = await _users.Users
            .Where(u => userIds.Contains(u.Id))
            .ToDictionaryAsync(u => u.Id, u => u.DisplayName, ct);

        var enriched = entries.Select(e => new
        {
            e.id,
            e.user_id,
            user_name = userNames.GetValueOrDefault(e.user_id, "Unknown"),
            e.action,
            e.resource,
            e.detail,
            e.ip_address,
            e.timestamp,
        });

        return Ok(new { total, entries = enriched });
    }

    /// <summary>Returns the current pipeline status.</summary>
    [HttpGet("data-pipeline")]
    [Authorize(Roles = "Admin")]
    public async Task<IActionResult> GetDataPipeline(CancellationToken ct = default)
    {
        var healthTask   = _intelligence.GetPythonDocumentAsync("/admin/health", ct);
        var statsTask    = _intelligence.GetPythonDocumentAsync("/admin/stats", ct);
        var topologyTask = _intelligence.GetPythonDocumentAsync("/admin/pipeline-topology", ct);
        await Task.WhenAll(healthTask, statsTask, topologyTask);
        var health   = await healthTask;
        var stats    = await statsTask;
        var topology = await topologyTask;

        var dbOk = true;
        try { await _db.Database.CanConnectAsync(ct); }
        catch { dbOk = false; }

        // Build a simple stage summary for the UI.
        var stages = new[]
        {
            new {
                name = "Extractors",
                description = "Data collection from 10+ OSINT sources",
                status = health != null ? "operational" : "unavailable",
                icon = "download",
            },
            new {
                name = "NLP Pipeline",
                description = "NER, sentiment analysis, entity resolution, translation",
                status = health != null ? "operational" : "unavailable",
                icon = "cpu",
            },
            new {
                name = "PostgreSQL",
                description = "Primary event, entity, and narrative storage",
                status = dbOk ? "operational" : "down",
                icon = "database",
            },
            new {
                name = "Neo4j Graph",
                description = "Knowledge graph with entity relationships",
                status = health != null ? "operational" : "unavailable",
                icon = "share",
            },
            new {
                name = "Agent Swarm",
                description = "Autonomous intelligence analysis agents",
                status = health != null ? "operational" : "unavailable",
                icon = "users",
            },
            new {
                name = ".NET API",
                description = "Authentication, authorization, SignalR hub",
                status = "operational",
                icon = "shield",
            },
            new {
                name = "Blazor UI",
                description = "Interactive intelligence dashboard",
                status = "operational",
                icon = "monitor",
            },
        };

        return Ok(new
        {
            stages,
            stats = stats?.RootElement,
            topology = topology?.RootElement,
            python_reachable = health != null,
            db_available = dbOk,
        });
    }

    /// <summary>System configuration and runtime info (non-sensitive).</summary>
    [HttpGet("system-info")]
    [Authorize(Roles = "Admin")]
    public async Task<IActionResult> GetSystemInfo(CancellationToken ct = default)
    {
        var health = await _intelligence.GetPythonDocumentAsync("/health", ct);
        var userCount = await _users.Users.CountAsync(ct);
        var auditCount = await _db.AuditLogs.CountAsync(ct);

        string? pythonVersion = null;
        bool? neo4jOk = null;
        string? llmProvider = null;
        string? llmModel = null;

        if (health != null)
        {
            var root = health.RootElement;
            if (root.TryGetProperty("version", out var v)) pythonVersion = v.GetString();
            if (root.TryGetProperty("neo4j_available", out var n)) neo4jOk = n.GetBoolean();
            if (root.TryGetProperty("llm", out var llm))
            {
                if (llm.TryGetProperty("provider", out var p)) llmProvider = p.GetString();
                if (llm.TryGetProperty("model", out var m)) llmModel = m.GetString();
            }
        }

        return Ok(new
        {
            platform = "Vision-I Global Intelligence Platform",
            environment = builder_env(),
            dotnet_version = System.Runtime.InteropServices.RuntimeInformation.FrameworkDescription,
            python_version = pythonVersion,
            total_users = userCount,
            total_audit_entries = auditCount,
            neo4j_connected = neo4jOk ?? false,
            llm_provider = llmProvider ?? "none",
            llm_model = llmModel ?? "n/a",
            uptime = DateTime.UtcNow.ToString("O"),
        });
    }

    /// <summary>List encrypted LLM provider configs with masked secrets.</summary>
    [HttpGet("llm/providers")]
    [Authorize(Roles = "Admin")]
    public async Task<IActionResult> GetLlmProviders(CancellationToken ct = default)
    {
        var configs = await _db.LlmProviderConfigs
            .OrderByDescending(x => x.IsDefault)
            .ThenBy(x => x.Provider)
            .ToListAsync(ct);

        var items = configs.Select(x => new
        {
            id = x.Id,
            provider = x.Provider,
            model = x.Model,
            base_url = x.BaseUrl,
            is_enabled = x.IsEnabled,
            is_default = x.IsDefault,
            updated_at = x.UpdatedAt.ToString("O"),
            last_tested_at = x.LastTestedAt.HasValue ? x.LastTestedAt.Value.ToString("O") : null,
            last_test_succeeded = x.LastTestSucceeded,
            last_test_message = x.LastTestMessage,
            api_key_masked = LlmConfigCryptoService.Mask(_crypto.Decrypt(x.EncryptedApiKey)),
        }).ToList();

        var runtime = await _llm.GetRuntimeAsync(ct);

        return Ok(new
        {
            providers = items,
            runtime = new
            {
                provider = runtime.Provider,
                model = runtime.Model,
                models = runtime.Models,
                base_url = runtime.BaseUrl,
                available = runtime.Available,
                runtime_source = runtime.RuntimeSource,
                message = runtime.Message,
                supported_providers = BuildSupportedProviders(),
            },
            supported_providers = BuildSupportedProviders(),
        });
    }

    /// <summary>Create or update a provider config for the native .NET JARVIS runtime.</summary>
    [HttpPost("llm/providers")]
    [Authorize(Roles = "Admin")]
    public async Task<IActionResult> UpsertLlmProvider(
        [FromBody] UpsertLlmProviderRequest req,
        CancellationToken ct = default)
    {
        var userId = User.FindFirstValue(ClaimTypes.NameIdentifier) ?? "";
        var providerKey = NormalizeProviderKey(req.Provider);
        if (string.IsNullOrWhiteSpace(providerKey))
            return BadRequest(new ApiError("INVALID_PROVIDER", "Provider is required."));

        var existing = await _db.LlmProviderConfigs
            .FirstOrDefaultAsync(x => x.Provider == providerKey, ct);

        var requiresApiKey = !ApiKeyOptionalProviders.Contains(providerKey);
        var incomingApiKey = (req.ApiKey ?? string.Empty).Trim();
        if (requiresApiKey && string.IsNullOrWhiteSpace(incomingApiKey) && existing is null)
            return BadRequest(new ApiError("API_KEY_REQUIRED", $"{providerKey} requires an API key."));
        if (!ApiKeyOptionalProviders.Contains(providerKey) && !string.IsNullOrWhiteSpace(incomingApiKey) && incomingApiKey.Length < 8)
            return BadRequest(new ApiError("INVALID_API_KEY", "API key looks too short."));

        if (req.IsDefault)
        {
            var defaults = await _db.LlmProviderConfigs.Where(x => x.IsDefault).ToListAsync(ct);
            foreach (var cfg in defaults)
                cfg.IsDefault = false;
        }

        if (existing is null)
        {
            existing = new LlmProviderConfig { Provider = providerKey };
            _db.LlmProviderConfigs.Add(existing);
        }

        existing.Model = NormalizeLlmModel(providerKey, req.Model);
        existing.BaseUrl = NormalizeLlmBaseUrl(providerKey, req.BaseUrl);
        if (!string.IsNullOrWhiteSpace(incomingApiKey))
            existing.EncryptedApiKey = _crypto.Encrypt(incomingApiKey);
        else if (ApiKeyOptionalProviders.Contains(providerKey))
            existing.EncryptedApiKey = string.Empty;
        existing.IsEnabled = req.IsEnabled;
        existing.IsDefault = req.IsDefault;
        existing.UpdatedByUserId = userId;
        existing.UpdatedAt = DateTime.UtcNow;

        await _db.SaveChangesAsync(ct);

        var effectiveApiKey = !string.IsNullOrWhiteSpace(incomingApiKey)
            ? incomingApiKey
            : _crypto.Decrypt(existing.EncryptedApiKey);

        _db.AuditLogs.Add(new AuditLog
        {
            UserId = userId,
            Action = "llm.provider.upsert",
            Resource = $"llm:{existing.Provider}",
            Detail = System.Text.Json.JsonSerializer.Serialize(new
            {
                provider = existing.Provider,
                model = existing.Model,
                existing.IsEnabled,
                existing.IsDefault,
            }),
            IpAddress = HttpContext.Connection.RemoteIpAddress?.ToString(),
        });
        await _db.SaveChangesAsync(ct);

        var runtime = await _llm.GetRuntimeAsync(ct);
        return Ok(new
        {
            status = runtime.Available ? "applied" : "saved",
            message = runtime.Available
                ? ".NET JARVIS runtime is active."
                : runtime.Message ?? "Configuration saved, but runtime is not available.",
            id = existing.Id,
            provider = existing.Provider,
            model = existing.Model,
            base_url = existing.BaseUrl,
            is_enabled = existing.IsEnabled,
            is_default = existing.IsDefault,
            api_key_masked = LlmConfigCryptoService.Mask(effectiveApiKey),
            runtime = new
            {
                provider = runtime.Provider,
                model = runtime.Model,
                models = runtime.Models,
                base_url = runtime.BaseUrl,
                available = runtime.Available,
                runtime_source = runtime.RuntimeSource,
                message = runtime.Message,
            },
        });
    }

    /// <summary>Test provider connectivity without changing the stored default state.</summary>
    [HttpPost("llm/providers/test")]
    [Authorize(Roles = "Admin")]
    public async Task<IActionResult> TestLlmProvider(
        [FromBody] TestLlmProviderRequest req,
        CancellationToken ct = default)
    {
        var providerKey = NormalizeProviderKey(req.Provider);
        if (string.IsNullOrWhiteSpace(providerKey))
            return BadRequest(new ApiError("INVALID_PROVIDER", "Provider is required."));
        var apiKey = (req.ApiKey ?? string.Empty).Trim();
        var existing = await _db.LlmProviderConfigs.FirstOrDefaultAsync(x => x.Provider == providerKey, ct);
        if (string.IsNullOrWhiteSpace(apiKey) && existing is not null)
            apiKey = _crypto.Decrypt(existing.EncryptedApiKey);

        // Fall back to the provider's environment key (e.g. GROQ_API_KEY) so the admin
        // Test button works when the key is configured via env rather than saved in the DB.
        if (string.IsNullOrWhiteSpace(apiKey))
        {
            var envName = providerKey switch
            {
                "groq"       => "GROQ_API_KEY",
                "openrouter" => "OPENROUTER_API_KEY",
                "openai"     => "OPENAI_API_KEY",
                "claude"     => "ANTHROPIC_API_KEY",
                "gemini"     => "GEMINI_API_KEY",
                _            => null,
            };
            if (envName is not null)
                apiKey = (Environment.GetEnvironmentVariable(envName) ?? string.Empty).Trim();
        }

        var requiresApiKey = !ApiKeyOptionalProviders.Contains(providerKey);
        if (requiresApiKey && string.IsNullOrWhiteSpace(apiKey))
            return BadRequest(new ApiError("API_KEY_REQUIRED", $"{providerKey} requires an API key for testing."));
        if (requiresApiKey && apiKey.Length < 8)
            return BadRequest(new ApiError("INVALID_API_KEY", "API key looks too short."));
        var result = await _llm.TestAsync(
            providerKey,
            NormalizeLlmModel(providerKey, req.Model),
            NormalizeLlmBaseUrl(providerKey, req.BaseUrl),
            apiKey,
            req.Enabled,
            ct);

        if (existing is not null)
        {
            existing.LastTestedAt = DateTime.UtcNow;
            existing.LastTestSucceeded = result.Ok;
            existing.LastTestMessage = result.Detail;
            await _db.SaveChangesAsync(ct);
        }

        _db.AuditLogs.Add(new AuditLog
        {
            UserId = User.FindFirstValue(ClaimTypes.NameIdentifier) ?? "",
            Action = "llm.provider.test",
            Resource = $"llm:{providerKey}",
            Detail = System.Text.Json.JsonSerializer.Serialize(new { provider = providerKey, model = req.Model }),
            IpAddress = HttpContext.Connection.RemoteIpAddress?.ToString(),
        });
        await _db.SaveChangesAsync(ct);

        return Ok(new
        {
            status = result.Ok ? "tested" : "error",
            message = result.Detail,
            model_used = result.ModelUsed,
            latency_ms = result.LatencyMs,
            result = new
            {
                ok = result.Ok,
                detail = result.Detail,
                model_used = result.ModelUsed,
                latency_ms = result.LatencyMs,
            },
        });
    }

    private string builder_env()
    {
        var env = Environment.GetEnvironmentVariable("ASPNETCORE_ENVIRONMENT");
        return env ?? "Production";
    }

    private static string NormalizeProviderKey(string? provider)
    {
        var key = (provider ?? string.Empty).Trim().ToLowerInvariant();
        return key switch
        {
            "anthropic" => "claude",
            _ => key,
        };
    }

    private static string NormalizeLlmModel(string providerKey, string? requestedModel)
        => string.Equals(providerKey, "openrouter", StringComparison.OrdinalIgnoreCase)
            ? OpenRouterFreeModelChain
            : (requestedModel ?? string.Empty).Trim();

    private static string? NormalizeLlmBaseUrl(string providerKey, string? requestedBaseUrl)
        => string.IsNullOrWhiteSpace(requestedBaseUrl)
            ? (string.Equals(providerKey, "openrouter", StringComparison.OrdinalIgnoreCase)
                ? "https://openrouter.ai/api"
                : null)
            : requestedBaseUrl.Trim();

    private static object[] BuildSupportedProviders()
        => new object[]
        {
            new
            {
                key = "claude",
                label = "Anthropic Claude",
                aliases = new[] { "anthropic" },
                default_model = "claude-sonnet-4-20250514",
                default_base_url = "https://api.anthropic.com",
                requires_api_key = true,
                api_key_label = "Anthropic API key",
            },
            new
            {
                key = "openai",
                label = "OpenAI",
                aliases = Array.Empty<string>(),
                default_model = "gpt-4o-mini",
                default_base_url = "https://api.openai.com",
                requires_api_key = true,
                api_key_label = "OpenAI API key",
            },
            new
            {
                key = "gemini",
                label = "Google Gemini",
                aliases = Array.Empty<string>(),
                default_model = "gemini-2.0-flash",
                default_base_url = "https://generativelanguage.googleapis.com",
                requires_api_key = true,
                api_key_label = "Gemini API key",
            },
            new
            {
                key = "openrouter",
                label = "OpenRouter",
                aliases = Array.Empty<string>(),
                default_model = OpenRouterFreeModelChain,
                default_base_url = "https://openrouter.ai/api",
                requires_api_key = true,
                api_key_label = "OpenRouter API key",
            },
            new
            {
                key = "groq",
                label = "Groq",
                aliases = Array.Empty<string>(),
                default_model = "llama-3.3-70b-versatile",
                default_base_url = "https://api.groq.com/openai",
                requires_api_key = true,
                api_key_label = "Groq API key",
            },
            new
            {
                key = "ollama",
                label = "Ollama",
                aliases = Array.Empty<string>(),
                default_model = "llama3.2",
                default_base_url = "http://localhost:11434",
                requires_api_key = false,
                api_key_label = "Not required",
            },
        };

    /// <summary>Force-reset a user's password (Admin only).</summary>
    [HttpPost("users/{userId}/reset-password")]
    [Authorize(Roles = "Admin")]
    public async Task<IActionResult> ResetPassword(
        string userId, [FromBody] ResetPasswordRequest req)
    {
        var user = await _users.FindByIdAsync(userId);
        if (user == null) return NotFound(new ApiError("USER_NOT_FOUND", "User not found."));

        var token = await _users.GeneratePasswordResetTokenAsync(user);
        var result = await _users.ResetPasswordAsync(user, token, req.NewPassword);

        if (!result.Succeeded)
            return BadRequest(new ApiError("RESET_FAILED",
                string.Join("; ", result.Errors.Select(e => e.Description))));

        _log.LogInformation("Password reset for user {UserId} by admin", userId);

        var actorId = User.FindFirstValue(ClaimTypes.NameIdentifier) ?? "";
        _db.AuditLogs.Add(new AuditLog
        {
            UserId   = actorId,
            Action   = "user.password_reset",
            Resource = $"user:{userId}",
            IpAddress = HttpContext.Connection.RemoteIpAddress?.ToString(),
        });
        await _db.SaveChangesAsync();

        return Ok(new { userId, message = "Password reset successfully." });
    }

    /// <summary>Permanently delete a user account (Admin only).</summary>
    [HttpDelete("users/{userId}")]
    [Authorize(Roles = "Admin")]
    public async Task<IActionResult> DeleteUser(string userId, CancellationToken ct = default)
    {
        // Prevent admin from deleting themselves
        var selfId = User.FindFirstValue(ClaimTypes.NameIdentifier);
        if (userId == selfId)
            return BadRequest(new ApiError("SELF_DELETE", "You cannot delete your own account."));

        var user = await _users.FindByIdAsync(userId);
        if (user == null) return NotFound(new ApiError("USER_NOT_FOUND", "User not found."));

        var result = await _users.DeleteAsync(user);
        if (!result.Succeeded)
            return StatusCode(500, new ApiError("DELETE_FAILED", "Failed to delete user."));

        _log.LogInformation("User {UserId} ({Email}) deleted by admin", userId, user.Email);

        var actorId = User.FindFirstValue(ClaimTypes.NameIdentifier) ?? "";
        _db.AuditLogs.Add(new AuditLog
        {
            UserId   = actorId,
            Action   = "user.delete",
            Resource = $"user:{userId}",
            Detail   = System.Text.Json.JsonSerializer.Serialize(new { email = user.Email }),
            IpAddress = HttpContext.Connection.RemoteIpAddress?.ToString(),
        });
        await _db.SaveChangesAsync();

        return Ok(new { userId, message = "User deleted." });
    }
}

