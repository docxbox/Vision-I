using Microsoft.AspNetCore.Authorization;
using Microsoft.AspNetCore.Mvc;
using Microsoft.EntityFrameworkCore;
using VisionI.API.Infrastructure;
using VisionI.API.Models.Requests;
using VisionI.API.Models.Responses;
using VisionI.API.Services;

namespace VisionI.API.Controllers;

/// <summary>
/// Analyst Copilot - LLM-powered reasoning over ontology objects.
/// </summary>
[ApiController]
[Authorize]
[Produces("application/json")]
[Route("api/copilot")]
public class CopilotController : ControllerBase
{
    private readonly IIntelligenceService _intelligence;
    private readonly INativeLlmService _llm;
    private readonly ILogger<CopilotController> _logger;

    public CopilotController(
        IIntelligenceService intelligence,
        INativeLlmService llm,
        ILogger<CopilotController> logger)
    {
        _intelligence = intelligence;
        _llm = llm;
        _logger = logger;
    }

    [HttpGet("summary")]
    public async Task<IActionResult> GetSummary(
        [FromQuery] int window_hours = 6,
        CancellationToken ct = default)
    {
        var precomputed = await _intelligence.GetPrecomputedJsonAsync("precomputed:copilot_summary", ct);
        if (precomputed != null)
        {
            var cachedPayload = _intelligence.DeserializeJson<CopilotSummaryResponse>(precomputed);
            if (cachedPayload != null)
                return Content(_intelligence.SerializeJson(cachedPayload), "application/json");
        }

        var json = await _intelligence.GetCachedJsonAsync(
            $"cache:copilot:summary:{window_hours}",
            innerCt => _intelligence.GetPythonJsonAsync($"/copilot/summary?window_hours={window_hours}", innerCt),
            TimeSpan.FromMinutes(2),
            ct);

        if (json == null) return StatusCode(502, "Copilot unavailable.");
        var payload = _intelligence.DeserializeJson<CopilotSummaryResponse>(json);
        return payload is null ? StatusCode(502, "Invalid copilot summary payload.") : Content(_intelligence.SerializeJson(payload), "application/json");
    }

    [HttpPost("ask")]
    public async Task<IActionResult> Ask([FromBody] CopilotAskRequest body, CancellationToken ct = default)
    {
        var prompt = BuildCopilotPrompt(body);
        var result = await _llm.CompleteAsync(
            prompt,
            CopilotActionSystemPrompt,
            maxTokens: 1100,
            temperature: 0.2,
            ct);

        string answer;
        IReadOnlyList<CopilotActionResponse> actions = Array.Empty<CopilotActionResponse>();
        if (result.Ok)
            (answer, actions) = ParseCopilotEnvelope(result.Text);
        else
            answer = $"JARVIS .NET runtime is online, but the LLM did not return a usable answer: {result.Error}";

        var payload = new CopilotAnswerResponse(
            body.Question,
            answer,
            new CopilotContextSummaryResponse(
                body.EventId,
                !string.IsNullOrWhiteSpace(body.EventId),
                0,
                0,
                0,
                string.IsNullOrWhiteSpace(body.ActorId) ? 0 : 1,
                0),
            result.Ok,
            result.ModelUsed,
            actions);
        return Content(_intelligence.SerializeJson(payload), "application/json");
    }

    private const string CopilotActionSystemPrompt = """
        You are JARVIS, the native operator copilot for Vision-I (a Palantir Gotham-style intelligence platform).
        Ground every answer in the provided page context, event IDs, actor IDs, workspace slug, source counts, and visible data.
        Be direct and operational; be honest about missing evidence and name the pipeline/provider to inspect when data is thin.
        Do NOT invent source data, IDs, system health, or API state.

        Beyond answering, you PROPOSE platform actions the operator can run with one click. Only propose actions that are
        relevant to the question and grounded in IDs/slugs that appear in the context — never fabricate an ID. Propose at
        most 4 actions; omit the array if none apply.

        Allowed action types and their params (params values are strings):
        - navigate        {"path": "/map" | "/threatboard" | "/alerts" | "/workspaces" | "/operations" | "/reports" | ...}
        - open_event      {"eventId": "<id from context>"}
        - open_entity     {"entityId": "<id>"}
        - open_object     {"type": "event|actor|location|organization|theme", "id": "<id>"}  (opens the graph explorer to drill linked objects)
        - open_workspace  {"slug": "<workspace slug>"}
        - search          {"query": "<text>"}
        - focus_map       {"region": "<place name>"}
        - pin_evidence    {"slug": "<workspace slug>", "itemType": "event|asset|entity", "itemId": "<id>", "title": "<short>"}
        - create_task     {"slug": "<workspace slug>", "title": "<task>", "priority": "low|medium|high|critical"}
        - open_report     {"slug": "<workspace slug>"}     (omit slug for the global report studio)
        - ack_alert       {"alertId": "<id>"}

        Respond with ONLY a JSON object, no markdown fences:
        {"answer": "<concise operator answer: what it is, why it matters, evidence, next action>",
         "actions": [{"type": "...", "label": "<short button text>", "params": { ... }}]}
        """;

    private static (string Answer, IReadOnlyList<CopilotActionResponse> Actions) ParseCopilotEnvelope(string raw)
    {
        var text = (raw ?? string.Empty).Trim();
        // Strip code fences if the model wrapped the JSON.
        if (text.StartsWith("```"))
        {
            var firstNl = text.IndexOf('\n');
            if (firstNl >= 0) text = text[(firstNl + 1)..];
            if (text.EndsWith("```")) text = text[..^3];
            text = text.Trim();
        }
        // Isolate the outermost JSON object.
        var start = text.IndexOf('{');
        var end = text.LastIndexOf('}');
        if (start < 0 || end <= start)
            return (text, Array.Empty<CopilotActionResponse>());

        try
        {
            using var doc = System.Text.Json.JsonDocument.Parse(text[start..(end + 1)]);
            var root = doc.RootElement;
            var answer = root.TryGetProperty("answer", out var a) && a.ValueKind == System.Text.Json.JsonValueKind.String
                ? a.GetString() ?? ""
                : text;

            var actions = new List<CopilotActionResponse>();
            if (root.TryGetProperty("actions", out var arr) && arr.ValueKind == System.Text.Json.JsonValueKind.Array)
            {
                foreach (var item in arr.EnumerateArray())
                {
                    if (item.ValueKind != System.Text.Json.JsonValueKind.Object) continue;
                    var type = item.TryGetProperty("type", out var t) ? t.GetString() : null;
                    if (string.IsNullOrWhiteSpace(type) || !AllowedActionTypes.Contains(type)) continue;
                    var label = item.TryGetProperty("label", out var l) ? l.GetString() ?? type : type;
                    var pars = new Dictionary<string, string>();
                    if (item.TryGetProperty("params", out var p) && p.ValueKind == System.Text.Json.JsonValueKind.Object)
                        foreach (var prop in p.EnumerateObject())
                            pars[prop.Name] = prop.Value.ValueKind == System.Text.Json.JsonValueKind.String
                                ? prop.Value.GetString() ?? ""
                                : prop.Value.ToString();
                    actions.Add(new CopilotActionResponse(type!, label, pars));
                    if (actions.Count >= 4) break;
                }
            }
            return (string.IsNullOrWhiteSpace(answer) ? text : answer, actions);
        }
        catch
        {
            return (text, Array.Empty<CopilotActionResponse>());
        }
    }

    private static readonly HashSet<string> AllowedActionTypes = new(StringComparer.OrdinalIgnoreCase)
    {
        "navigate", "open_event", "open_entity", "open_object", "open_workspace", "search",
        "focus_map", "pin_evidence", "create_task", "open_report", "ack_alert",
    };

    private static string BuildCopilotPrompt(CopilotAskRequest body)
    {
        var history = body.History is { Length: > 0 }
            ? System.Text.Json.JsonSerializer.Serialize(body.History.TakeLast(10))
            : "[]";

        return $"""
        Analyst: {body.Analyst}
        Question: {body.Question}

        Active object ids:
        - event_id: {body.EventId ?? "none"}
        - actor_id: {body.ActorId ?? "none"}
        - narrative_id: {body.NarrativeId ?? "none"}

        Page/system context:
        {body.Context ?? "No additional context supplied."}

        Recent chat history:
        {history}

        Produce a concise operator-grade response with:
        1. what changed or what the answer is,
        2. why it matters,
        3. what evidence is connected,
        4. the next best action.
        """;
    }

    [HttpPost("explain/{eventId}")]
    public async Task<IActionResult> Explain(string eventId, CancellationToken ct = default)
    {
        var payload = await _intelligence.PostPythonModelAsync<CopilotExplainResponse>($"/copilot/explain/{Uri.EscapeDataString(eventId)}", new { }, ct);
        if (payload == null) return StatusCode(502, "Copilot unavailable.");
        return Content(_intelligence.SerializeJson(payload), "application/json");
    }

    [HttpGet("similar/{eventId}")]
    public async Task<IActionResult> Similar(string eventId, [FromQuery] int limit = 5, CancellationToken ct = default)
    {
        var payload = await _intelligence.GetPythonModelAsync<CopilotSimilarResponse>($"/copilot/similar/{Uri.EscapeDataString(eventId)}?limit={limit}", ct);
        if (payload == null)
            return Content(_intelligence.SerializeJson(new CopilotSimilarResponse(eventId, "", new(), 0, "No similar past decisions found.")), "application/json");
        return Content(_intelligence.SerializeJson(payload), "application/json");
    }

    [HttpGet("recommend/{eventId}")]
    public async Task<IActionResult> Recommend(string eventId, CancellationToken ct = default)
    {
        var payload = await _intelligence.GetPythonModelAsync<CopilotRecommendationResponse>($"/copilot/recommend/{Uri.EscapeDataString(eventId)}", ct);
        if (payload == null) return StatusCode(502, "Copilot unavailable.");
        return Content(_intelligence.SerializeJson(payload), "application/json");
    }
}
