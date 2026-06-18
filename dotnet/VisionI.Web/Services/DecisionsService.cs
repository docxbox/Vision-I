using System.Text.Json;

namespace VisionI.Web.Services;

public sealed class DecisionsService
{
    private readonly ApiService _api;

    public JsonElement? Items       { get; private set; }
    public string CtxId             { get; set; } = "";
    public string Action            { get; set; } = "";
    public string Rationale         { get; set; } = "";
    public string Error             { get; private set; } = "";
    public bool Loading             { get; private set; } = true;
    private readonly Dictionary<string, string> _outcomes = new();

    public int DecisionCount => DecisionItems().Count;
    public int OutcomeCount => DecisionItems().Count(d => !string.IsNullOrWhiteSpace(GetString(d, "outcome")));
    public int OpenCount => DecisionItems().Count(d => string.IsNullOrWhiteSpace(GetString(d, "outcome")));
    public JsonElement? LatestDecision => DecisionItems().FirstOrDefault();
    public DecisionRowVm? LeadDecision => DecisionRows().FirstOrDefault();

    public event Action? OnChanged;

    public DecisionsService(ApiService api) => _api = api;

    private DateTime _lastLoaded = DateTime.MinValue;
    public async Task EnsureLoadedAsync()
    {
        if (DateTime.UtcNow - _lastLoaded < TimeSpan.FromMinutes(2)) return;
        _lastLoaded = DateTime.UtcNow;
        await LoadAsync();
    }

    public async Task LoadAsync()
    {
        Loading = true; Error = ""; Notify();
        try { Items = await _api.GetAsync<JsonElement?>("api/decisions?limit=100"); }
        catch (Exception ex) { Error = ex.Message; }
        finally { Loading = false; Notify(); }
    }

    public async Task CreateAsync()
    {
        try { await _api.PostAsync<JsonElement?>("api/decisions", new { context_id = CtxId, action = Action, rationale = Rationale }); }
        catch { }
        Action = ""; Rationale = "";
        await LoadAsync();
    }

    public async Task RecordOutcomeAsync(string id)
    {
        var txt = _outcomes.TryGetValue(id, out var v) ? v : "";
        try { await _api.PostAsync<JsonElement?>($"api/decisions/{id}/outcome", new { outcome = txt }); }
        catch { }
        await LoadAsync();
    }

    public string GetOutcome(string id) => _outcomes.TryGetValue(id, out var v) ? v : "";
    public void SetOutcome(string id, string v) { _outcomes[id] = v; }

    public static string Fmt(string? raw)
        => DateTime.TryParse(raw, out var d) ? d.ToUniversalTime().ToString("MM-dd HH:mm'Z'") : (raw ?? "-");

    public List<JsonElement> DecisionItems()
    {
        if (Items is null) return new();
        var root = Items.Value;
        if (root.ValueKind == JsonValueKind.Array) return root.EnumerateArray().ToList();
        if (root.TryGetProperty("decisions", out var d) && d.ValueKind == JsonValueKind.Array) return d.EnumerateArray().ToList();
        if (root.TryGetProperty("data",      out var data) && data.ValueKind == JsonValueKind.Array) return data.EnumerateArray().ToList();
        return new();
    }

    public List<DecisionRowVm> DecisionRows()
    {
        return DecisionItems().Select(item =>
        {
            var id = GetString(item, "decision_id", "id");
            var action = GetString(item, "action", "coa_text");
            var outcome = GetString(item, "outcome");
            var status = GetString(item, "status");
            var analyst = GetString(item, "analyst", "created_by");
            var contextId = GetString(item, "context_id", "event_id");
            var createdAt = GetString(item, "created_at", "timestamp");
            var rationale = GetString(item, "rationale");
            var driver = string.IsNullOrWhiteSpace(outcome) ? "open feedback loop" : "recorded outcome";
            var actionHint = string.IsNullOrWhiteSpace(outcome) ? "record outcome" : "compare precedent";
            return new DecisionRowVm(id, action, outcome, status, analyst, contextId, createdAt, rationale, driver, actionHint);
        }).ToList();
    }

    public static string GetString(JsonElement element, params string[] keys)
    {
        foreach (var key in keys)
        {
            if (element.TryGetProperty(key, out var value))
            {
                if (value.ValueKind == JsonValueKind.String)
                    return value.GetString() ?? "";

                if (value.ValueKind is JsonValueKind.Number or JsonValueKind.True or JsonValueKind.False)
                    return value.ToString();
            }
        }

        return "";
    }

    private void Notify() => OnChanged?.Invoke();
}

public sealed record DecisionRowVm(
    string Id,
    string Action,
    string Outcome,
    string Status,
    string Analyst,
    string ContextId,
    string CreatedAt,
    string Rationale,
    string Driver,
    string ActionHint);
