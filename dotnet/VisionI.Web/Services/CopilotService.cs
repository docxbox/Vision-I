using System.Text.Json;

using Microsoft.AspNetCore.Components;
using VisionI.Web.Models;

namespace VisionI.Web.Services;

public sealed class ChatMessage
{
    public string Role    { get; init; } = "user";   // "user" | "assistant" | "system"
    public string Content { get; init; } = "";
    public DateTime Ts    { get; init; } = DateTime.UtcNow;
    public bool IsError   { get; init; }
    public IReadOnlyList<CopilotAction>? Actions { get; init; }
}

/// <summary>A platform action the operator copilot proposes; executed via ExecuteActionAsync.</summary>
public sealed record CopilotAction(string Type, string Label, Dictionary<string, string>? Params)
{
    public string P(string key) => Params is not null && Params.TryGetValue(key, out var v) ? v : "";
    public bool IsMutation => Type is "pin_evidence" or "create_task" or "ack_alert";
}

/// <summary>
/// Scoped ViewModel service for the JARVIS / Copilot page.
/// Manages conversation history and all API calls.
/// PDF export is triggered from the page via IJSRuntime.
/// </summary>
public sealed class CopilotService
{
    private readonly ApiService  _api;
    private readonly ToastService _toast;
    private readonly ILogger<CopilotService> _log;
    private readonly NavigationManager _nav;
    private readonly ViStateService _state;

    public List<ChatMessage> History { get; } = new();
    public bool Busy { get; private set; }
    public string Input { get; set; } = "";
    public string EventIdInput { get; set; } = "";
    public CopilotExplainDto? LastExplain { get; private set; }
    public CopilotSimilarDto? LastSimilar { get; private set; }
    public CopilotRecommendDto? LastRecommend { get; private set; }
    public CopilotContextSummary? LastEvidence =>
        LastRecommend?.Evidence ?? LastExplain?.Evidence;
    public string WhatChangedSummary
    {
        get
        {
            var latest = History.LastOrDefault();
            return latest is null
                ? "JARVIS is online and awaiting an analyst prompt."
                : $"The latest exchange in this session is from {(latest.Role == "assistant" ? "JARVIS" : "the analyst")}.";
        }
    }

    public string WhyItMattersSummary =>
        LastEvidence is null
            ? "JARVIS is most useful when it is grounded in a specific event, precedent, and linked evidence."
            : $"The current copilot context includes {LastEvidence.AlertCount} alerts, {LastEvidence.NarrativeCount} narratives, {LastEvidence.ActorCount} actors, and {LastEvidence.PastDecisionsCount} prior decisions.";

    public string WhatIsConnectedSummary =>
        LastEvidence is null
            ? $"{History.Count} messages are in the current session, and quick actions here are grounded in events, signals, narratives, alerts, and graph data."
            : $"{History.Count} session messages, {LastEvidence.SimilarEventCount} similar events, and {LastEvidence.PastDecisionsCount} past decisions are connected to the current deep-dive.";

    public string RecommendedActionSummary =>
        string.IsNullOrWhiteSpace(LastRecommend?.PrimaryRecommendation)
            ? "Use event deep-dive for a specific incident, and use quick actions when you need a fast operational brief across the wider picture."
            : LastRecommend!.PrimaryRecommendation;

    public string SurfaceLabel => DescribeCurrentSurface().Label;
    public string SurfaceScope => DescribeCurrentSurface().Scope;
    public int EffectiveAssetCount =>
        // Prefer true totals (cheap /assets/counts); _state.Assets is now a light sample.
        _state.Counts.Aircraft + _state.Counts.Vessels is var total && total > 0
            ? total
            : _state.Assets.Count;
    public string LiveCountSummary =>
        $"Events {_state.Events.Count:N0} - Assets {EffectiveAssetCount:N0} - Alerts {_state.Alerts.Count:N0} - Flights {_state.Counts.Aircraft:N0} - Ships {_state.Counts.Vessels:N0}";

    public IReadOnlyList<(string Label, string Value)> LiveMetrics => new[]
    {
        ("Events", _state.Events.Count.ToString("N0")),
        ("Assets", EffectiveAssetCount.ToString("N0")),
        ("Alerts", _state.Alerts.Count.ToString("N0")),
        ("Flights", _state.Counts.Aircraft.ToString("N0")),
        ("Ships", _state.Counts.Vessels.ToString("N0"))
    };

    public IReadOnlyList<string> NativeQuickPrompts => DescribeCurrentSurface().Prompts;

    public event Action? OnChanged;
    private const string PlatformContext =
            "You are JARVIS, the intelligence assistant for Vision-I, a multi-source OSINT " +
        "platform that ingests news, RSS feeds, social media, seismic/aircraft/maritime " +
        "data, and runs NLP analysis, sentiment scoring, narrative detection, anomaly " +
        "scanning, and influence mapping. You have live access to all events, entities, " +
        "alerts, narratives, signals, and the Neo4j knowledge graph. When asked for a " +
        "report, structure your answer with clear headings and bullet points. When asked " +
        "for a PDF report, tell the user to click the 'Save PDF' button.";

    public CopilotService(
        ApiService api,
        ToastService toast,
        ILogger<CopilotService> log,
        NavigationManager nav,
        ViStateService state)
    {
        _api   = api;
        _toast = toast;
        _log   = log;
        _nav   = nav;
        _state = state;

        History.Add(new ChatMessage
        {
            Role    = "assistant",
            Content = "JARVIS online. I am attached to the current Vision-I surface and will ground answers in the active page, live counts, recent events, assets, alerts, and graph evidence. Ask for a brief, investigation plan, or operator handoff."
        });
    }

    public async Task AskAsync(string? question = null)
    {
        var q = (question ?? Input).Trim();
        if (string.IsNullOrWhiteSpace(q) || Busy) return;

        Input = "";
        History.Add(new ChatMessage { Role = "user", Content = q });
        Busy = true;
        OnChanged?.Invoke();

        try
        {
            var payload = new
            {
                question = q,
                context  = BuildNativeContext(q),
                history  = History
                    .Where(m => m.Role != "system")
                    .TakeLast(10)
                    .Select(m => new { role = m.Role, content = m.Content })
                    .ToList()
            };

            var result = await _api.PostAsync<JsonElement?>("api/copilot/ask", payload, TimeSpan.FromSeconds(90));

            var (answer, actions) = ParseAnswer(result);
            History.Add(new ChatMessage { Role = "assistant", Content = answer, Actions = actions });
        }
        catch (Exception ex)
        {
            _log.LogError(ex, "CopilotService.AskAsync failed");
            History.Add(new ChatMessage
            {
                Role    = "assistant",
            Content = "Connection error. Verify LLM configuration in Admin > LLM Runtime.",
                IsError = true
            });
        }
        finally { Busy = false; OnChanged?.Invoke(); }
    }

    public async Task TacticalSummaryAsync()
    {
        History.Add(new ChatMessage { Role = "user", Content = "Generate a tactical intelligence summary." });
        Busy = true;
        OnChanged?.Invoke();
        try
        {
            var result = await _api.GetAsync<JsonElement?>("api/copilot/summary", TimeSpan.FromSeconds(60));
            var text = ExtractText(result) ?? "No summary available.";
            History.Add(new ChatMessage { Role = "assistant", Content = text });
        }
        catch (Exception ex)
        {
            _log.LogError(ex, "TacticalSummaryAsync failed");
            History.Add(new ChatMessage { Role = "assistant", Content = "Failed to fetch tactical summary.", IsError = true });
        }
        finally { Busy = false; OnChanged?.Invoke(); }
    }

    public async Task ExplainEventAsync(string eventId)
    {
        if (string.IsNullOrWhiteSpace(eventId)) return;
        History.Add(new ChatMessage { Role = "user", Content = $"Explain event: {eventId}" });
        Busy = true;
        OnChanged?.Invoke();
        try
        {
            var result = await _api.CopilotExplainAsync(eventId.Trim());
            LastExplain = result;
            EventIdInput = eventId.Trim();
            var text = result is null
                ? "Event not found."
                : ExtractExplain(result) ?? JsonSerializer.Serialize(result);
            History.Add(new ChatMessage { Role = "assistant", Content = text });
        }
        catch (Exception ex)
        {
            _log.LogError(ex, "ExplainEventAsync failed for {Id}", eventId);
            History.Add(new ChatMessage { Role = "assistant", Content = "Could not explain event.", IsError = true });
        }
        finally { Busy = false; OnChanged?.Invoke(); }
    }

    public async Task SimilarEventsAsync(string eventId)
    {
        if (string.IsNullOrWhiteSpace(eventId)) return;
        History.Add(new ChatMessage { Role = "user", Content = $"Find similar events to: {eventId}" });
        Busy = true;
        OnChanged?.Invoke();
        try
        {
            var result = await _api.CopilotSimilarAsync(eventId.Trim(), 5);
            LastSimilar = result;
            EventIdInput = eventId.Trim();
            var text = result is null ? "No similar events found." : FormatSimilar(result);
            History.Add(new ChatMessage { Role = "assistant", Content = text });
        }
        catch
        {
            History.Add(new ChatMessage { Role = "assistant", Content = "Similar events lookup failed.", IsError = true });
        }
        finally { Busy = false; OnChanged?.Invoke(); }
    }

    public async Task RecommendEventAsync(string eventId)
    {
        if (string.IsNullOrWhiteSpace(eventId)) return;
        History.Add(new ChatMessage { Role = "user", Content = $"Recommend next action for event: {eventId}" });
        Busy = true;
        OnChanged?.Invoke();
        try
        {
            var result = await _api.CopilotRecommendAsync(eventId.Trim());
            LastRecommend = result;
            EventIdInput = eventId.Trim();
            var text = result is null ? "No recommendation available." : FormatRecommendation(result);
            History.Add(new ChatMessage { Role = "assistant", Content = text });
        }
        catch (Exception ex)
        {
            _log.LogError(ex, "RecommendEventAsync failed for {Id}", eventId);
            History.Add(new ChatMessage { Role = "assistant", Content = "Could not build a recommendation.", IsError = true });
        }
        finally { Busy = false; OnChanged?.Invoke(); }
    }

    public async Task ThreatReportAsync()
        => await AskAsync("Generate a detailed threat report for the current Vision-I surface. Cover top active threats, key actors, affected regions, narrative activity, anomaly alerts, and recommended actions.");

    public async Task SentimentReportAsync()
        => await AskAsync("Summarise sentiment for the current Vision-I surface across all available sources. Include top negative narratives, sentiment trends, and countries or entities with the highest negative event concentration.");

    public async Task InfluenceReportAsync()
        => await AskAsync("Identify the most influential actors connected to the current Vision-I surface. Report influence scores, relationships, narrative leverage, and potential information operation indicators.");

    public async Task BriefCurrentSurfaceAsync()
        => await AskAsync("Brief the current page like an operator handoff: what changed, why it matters, what is connected, and the next recommended action.");

    public async Task InvestigateCurrentSurfaceAsync()
        => await AskAsync("Build an investigation plan for the current page. Include source checks, entities to verify, map/asset pivots, graph pivots, and what would confirm or falsify the situation.");

    public async Task ReportCurrentSurfaceAsync()
        => await AskAsync("Draft a report outline for the current page with executive summary, evidence, timeline, entities, assets, source credibility, and recommended action sections.");

    public async Task<string?> AskRawAsync(string prompt)
    {
        try
        {
            var payload = new { question = prompt, context = BuildNativeContext(prompt), history = Array.Empty<object>() };
            var result = await _api.PostAsync<JsonElement?>("api/copilot/ask", payload, TimeSpan.FromSeconds(90));
            return ExtractText(result);
        }
        catch (Exception ex)
        {
            _log.LogError(ex, "CopilotService.AskRawAsync failed");
            return null;
        }
    }

    public void ClearHistory()
    {
        History.Clear();
        LastExplain = null;
        LastSimilar = null;
        LastRecommend = null;
        EventIdInput = "";
        History.Add(new ChatMessage
        {
            Role    = "assistant",
            Content = "Conversation cleared. JARVIS is still attached to the current Vision-I surface. Ask for a brief, investigation plan, or report outline."
        });
        OnChanged?.Invoke();
    }

    private (string Answer, IReadOnlyList<CopilotAction>? Actions) ParseAnswer(JsonElement? el)
    {
        var answer = ExtractText(el) ?? "No response from JARVIS. Check LLM configuration in Admin panel.";
        if (el is null) return (answer, null);
        var root = el.Value;
        if (root.ValueKind != JsonValueKind.Object ||
            !root.TryGetProperty("actions", out var arr) ||
            arr.ValueKind != JsonValueKind.Array)
            return (answer, null);

        var actions = new List<CopilotAction>();
        foreach (var item in arr.EnumerateArray())
        {
            if (item.ValueKind != JsonValueKind.Object) continue;
            var type = item.TryGetProperty("type", out var t) ? t.GetString() : null;
            if (string.IsNullOrWhiteSpace(type)) continue;
            var label = item.TryGetProperty("label", out var l) ? l.GetString() ?? type : type;
            Dictionary<string, string>? pars = null;
            if (item.TryGetProperty("params", out var p) && p.ValueKind == JsonValueKind.Object)
            {
                pars = new();
                foreach (var prop in p.EnumerateObject())
                    pars[prop.Name] = prop.Value.ValueKind == JsonValueKind.String
                        ? prop.Value.GetString() ?? ""
                        : prop.Value.ToString();
            }
            actions.Add(new CopilotAction(type!, label, pars));
        }
        return (answer, actions.Count > 0 ? actions : null);
    }

    /// <summary>Executes an operator action the copilot proposed (navigation or a grounded mutation).</summary>
    public async Task ExecuteActionAsync(CopilotAction action)
    {
        try
        {
            switch (action.Type)
            {
                case "navigate":       Go(action.P("path")); break;
                case "open_event":     Go($"/events/{Enc(action.P("eventId"))}"); break;
                case "open_entity":    Go($"/entities/{Enc(action.P("entityId"))}"); break;
                case "open_object":    Go($"/explore/{Enc(Default(action.P("type"), "event"))}/{Enc(action.P("id"))}"); break;
                case "open_workspace": Go($"/workspaces/{Enc(action.P("slug"))}"); break;
                case "open_report":
                    var slug = action.P("slug");
                    Go(string.IsNullOrWhiteSpace(slug) ? "/reports" : $"/workspaces/{Enc(slug)}");
                    break;
                case "search":    Go($"/investigations?q={Uri.EscapeDataString(action.P("query"))}"); break;
                case "focus_map": Go($"/map?region={Uri.EscapeDataString(action.P("region"))}"); break;

                case "pin_evidence":
                    await _api.PostAsync<JsonElement?>(
                        $"api/workspaces/{Enc(action.P("slug"))}/evidence",
                        new { itemType = action.P("itemType"), itemId = action.P("itemId"), title = action.P("title") });
                    _toast.ShowSuccess($"Pinned to {action.P("slug")} evidence board.");
                    break;
                case "create_task":
                    await _api.PostAsync<JsonElement?>(
                        $"api/workspaces/{Enc(action.P("slug"))}/tasks",
                        new { title = action.P("title"), priority = Default(action.P("priority"), "medium") });
                    _toast.ShowSuccess("Task created.");
                    break;
                case "ack_alert":
                    await _api.AckAlertAsync(action.P("alertId"));
                    _toast.ShowSuccess("Alert acknowledged.");
                    break;

                default:
                    _toast.ShowError($"Unknown copilot action: {action.Type}");
                    break;
            }
        }
        catch (Exception ex)
        {
            _log.LogWarning(ex, "Copilot action {Type} failed", action.Type);
            _toast.ShowError($"Action failed: {ex.Message}");
        }
        OnChanged?.Invoke();
    }

    private void Go(string path)
    {
        if (!string.IsNullOrWhiteSpace(path))
            _nav.NavigateTo(path);
    }

    private static string Enc(string v) => Uri.EscapeDataString(v ?? "");
    private static string Default(string v, string fallback) => string.IsNullOrWhiteSpace(v) ? fallback : v;

    private string BuildNativeContext(string question)
    {
        var surface = DescribeCurrentSurface();
        var recentEvents = _state.Events
            .Where(e => !string.IsNullOrWhiteSpace(e.Title))
            .Take(6)
            .Select(e => $"- {Trim(e.Title!, 110)} | source={e.Source ?? "unknown"} | risk={(e.RiskScore ?? 0):0.00} | time={e.Timestamp ?? "n/a"}")
            .ToList();

        var recentAssets = _state.Assets
            .Where(a => !string.IsNullOrWhiteSpace(a.AssetId))
            .Take(6)
            .Select(a => $"- {a.AssetType ?? "asset"} {a.Callsign ?? a.AssetId} | lat={Fmt(a.LastLat, "0.00")} lon={Fmt(a.LastLon, "0.00")} speed={Fmt(a.LastSpeed, "0")} | seen={a.LastSeen ?? "n/a"}")
            .ToList();

        var lines = new List<string>
        {
            PlatformContext,
            "",
            "Native Vision-I session context:",
            $"- Current surface: {surface.Label}",
            $"- Scope: {surface.Scope}",
            $"- Route: {CurrentRelativePath()}",
            $"- Analyst question: {question}",
            $"- Live counts: {LiveCountSummary}",
            $"- State updated: {_state.LastUpdated:yyyy-MM-dd HH:mm:ss} UTC",
            $"- Current JARVIS insight: {(_state.JarvisInsight.Length > 0 ? _state.JarvisInsight : "none cached")}",
            "",
            "Recent prioritized events visible to the platform:",
        };

        lines.AddRange(recentEvents.Any() ? recentEvents : new[] { "- No event cache is loaded in this circuit yet." });
        lines.Add("");
        lines.Add("Recent prioritized assets visible to the platform:");
        lines.AddRange(recentAssets.Any() ? recentAssets : new[] { "- No asset cache is loaded in this circuit yet." });
        lines.Add("");
        lines.Add("Response rules:");
        lines.Add("- Behave as a native Vision-I operator assistant, not a generic chatbot.");
        lines.Add("- If the route is a workspace, answer in terms of that workspace's developments, entities, assets, map, actions, searches, and tasks.");
        lines.Add("- If data is missing, say which pipeline or provider should be checked instead of pretending it is calm.");
        lines.Add("- Keep outputs operational: concise headings, evidence, uncertainty, and next actions.");

        return string.Join("\n", lines);
    }

    private (string Label, string Scope, IReadOnlyList<string> Prompts) DescribeCurrentSurface()
    {
        var path = CurrentRelativePath();
        var clean = path.Split('?', '#')[0].Trim('/');
        var parts = clean.Split('/', StringSplitOptions.RemoveEmptyEntries);

        string label;
        string scope;

        if (parts.Length >= 2 && parts[0].Equals("workspaces", StringComparison.OrdinalIgnoreCase))
        {
            label = "Workspace";
            scope = $"workspace:{Decode(parts[1])}";
        }
        else if (parts.Length >= 2 && parts[0].Equals("events", StringComparison.OrdinalIgnoreCase))
        {
            label = "Event Detail";
            scope = $"event:{Decode(parts[1])}";
        }
        else if (parts.Length >= 2 && parts[0].Equals("entities", StringComparison.OrdinalIgnoreCase))
        {
            label = "Entity Profile";
            scope = $"entity:{Decode(parts[1])}";
        }
        else if (parts.Length >= 2 && parts[0].Equals("assets", StringComparison.OrdinalIgnoreCase))
        {
            label = "Asset Detail";
            scope = $"asset:{Decode(parts[1])}";
        }
        else
        {
            label = clean switch
            {
                "" or "overview" => "System Overview",
                "map" => "Intelligence Map",
                "investigations" => "Investigations Feed",
                "timeline" => "Event Timeline",
                "graph" => "Relationship Graph",
                "domain-intel" => "Airspace & Electromagnetic",
                "threat-board" => "Threat Board",
                "reports" => "Reports",
                _ => string.IsNullOrWhiteSpace(clean) ? "Vision-I" : Decode(clean)
            };
            scope = clean switch
            {
                "" or "overview" => "global operating picture",
                "investigations" => "live event feed",
                "map" => "geospatial view",
                "timeline" => "time replay",
                "graph" => "relationship network",
                "domain-intel" => "airspace layer",
                "threat-board" => "risk zones",
                "reports" => "report builder",
                _ => string.IsNullOrWhiteSpace(clean) ? "global" : Decode(clean)
            };
        }

        var prompts = new[]
        {
            "Brief current page",
            "What changed?",
            "Investigation plan",
            "Report outline"
        };

        return (label, scope, prompts);
    }

    private string CurrentRelativePath()
        => _nav.ToBaseRelativePath(_nav.Uri);

    private static string Decode(string value)
        => Uri.UnescapeDataString(value).Replace('-', ' ');

    private static string Trim(string value, int max)
        => value.Length <= max ? value : value[..Math.Max(0, max - 3)] + "...";

    private static string Fmt(double? value, string format)
        => value.HasValue ? value.Value.ToString(format) : "n/a";

    private static string? ExtractText(JsonElement? el)
    {
        if (el is null) return null;
        var root = el.Value;
        if (root.ValueKind == JsonValueKind.String) return root.GetString();
        foreach (var key in new[] { "answer", "response", "text", "content", "message", "result", "summary", "briefing" })
            if (root.TryGetProperty(key, out var p) && p.ValueKind == JsonValueKind.String)
                return p.GetString();
        return root.GetRawText();
    }

    private static string? ExtractExplain(object? result)
    {
        if (result is null) return null;
        var json = JsonSerializer.Serialize(result);
        using var doc = JsonDocument.Parse(json);
        var root = doc.RootElement;
        foreach (var key in new[] { "briefing", "summary", "explanation", "answer", "text" })
            if (root.TryGetProperty(key, out var p) && p.ValueKind == JsonValueKind.String)
                return p.GetString();
        return json;
    }

    private static string FormatSimilar(CopilotSimilarDto dto)
    {
        if (!dto.SimilarDecisions.Any())
            return "No similar decisions found for this event yet.";

        var lines = new List<string>
        {
            $"Similar event review for {dto.EventId}",
            dto.Insight,
        };

        foreach (var decision in dto.SimilarDecisions.Take(4))
        {
            lines.Add($"- {decision.Status.ToUpperInvariant()} | {decision.CoaText} | {decision.Analyst}");
        }

        return string.Join("\n", lines);
    }

    private static string FormatRecommendation(CopilotRecommendDto dto)
    {
        var lines = new List<string>
        {
            dto.PrimaryRecommendation,
            $"Confidence: {dto.Confidence.ToUpperInvariant()} | Risk: {dto.RiskScore:0.00}",
        };

        if (!string.IsNullOrWhiteSpace(dto.HistoricalPrecedent))
            lines.Add(dto.HistoricalPrecedent);
        if (!string.IsNullOrWhiteSpace(dto.Reasoning))
            lines.Add(dto.Reasoning);
        if (!string.IsNullOrWhiteSpace(dto.AiRecommendation))
            lines.Add(dto.AiRecommendation);

        return string.Join("\n", lines);
    }
}

