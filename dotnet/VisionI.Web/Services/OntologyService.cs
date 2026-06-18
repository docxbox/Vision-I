using System.Text.Json;

namespace VisionI.Web.Services;

public sealed class OntologyService
{
    private readonly ApiService _api;

    public string Tab               { get; set; } = "overview";
    public JsonElement? Overview    { get; private set; }
    public JsonElement? Ops         { get; private set; }
    public JsonElement? Graph       { get; private set; }
    public string Error             { get; private set; } = "";
    public OntologySituationVm? LeadSituation => SituationRows().FirstOrDefault();

    public event Action? OnChanged;

    public OntologyService(ApiService api) => _api = api;

    public async Task LoadAsync()
    {
        Error = "";
        try { Overview = await _api.GetAsync<JsonElement?>("api/ontology/overview"); }
        catch (Exception ex) { Error = ex.Message; }
        Notify();
    }

    public async Task LoadOpsAsync()
    {
        try { Ops = await _api.GetAsync<JsonElement?>("api/operations/overview"); } catch { Ops = null; }
        Notify();
    }

    public async Task LoadGraphAsync()
    {
        try { Graph = await _api.GetAsync<JsonElement?>("api/graph"); } catch { Graph = null; }
        Notify();
    }

    public List<JsonElement> OverviewItems()
    {
        if (Overview is null) return new();
        var root = Overview.Value;
        if (root.ValueKind == JsonValueKind.Array) return root.EnumerateArray().ToList();
        if (root.TryGetProperty("situations", out var s) && s.ValueKind == JsonValueKind.Array) return s.EnumerateArray().ToList();
        if (root.TryGetProperty("items",      out var i) && i.ValueKind == JsonValueKind.Array) return i.EnumerateArray().ToList();
        if (root.TryGetProperty("data",       out var d) && d.ValueKind == JsonValueKind.Array) return d.EnumerateArray().ToList();
        return new();
    }

    public List<OntologySituationVm> SituationRows()
    {
        return OverviewItems()
            .Select(o => new OntologySituationVm(
                ReadString(o, "title", "name") ?? "Situation",
                ReadDouble(o, "risk_score"),
                ReadString(o, "priority") ?? "-",
                ReadString(o, "summary", "description"),
                ReadString(o, "recommended_action", "action"),
                ReadString(o, "driver", "reason")))
            .ToList();
    }

    private static string? ReadString(JsonElement item, params string[] keys)
    {
        foreach (var key in keys)
            if (item.TryGetProperty(key, out var value) && value.ValueKind == JsonValueKind.String)
                return value.GetString();
        return null;
    }

    private static double ReadDouble(JsonElement item, params string[] keys)
    {
        foreach (var key in keys)
            if (item.TryGetProperty(key, out var value) && value.ValueKind == JsonValueKind.Number)
                return value.GetDouble();
        return 0;
    }

    private void Notify() => OnChanged?.Invoke();
}

public sealed record OntologySituationVm(
    string Title,
    double RiskScore,
    string Priority,
    string? Summary,
    string? Action,
    string? Driver);
