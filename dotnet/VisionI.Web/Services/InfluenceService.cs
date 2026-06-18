using System.Text.Json;

namespace VisionI.Web.Services;

public sealed class InfluenceService
{
    private readonly ApiService _api;

    public string Tab           { get; set; } = "actors";
    public JsonElement? Actors  { get; private set; }
    public JsonElement? Prop    { get; private set; }
    public JsonElement? Herd    { get; private set; }
    public string Error         { get; private set; } = "";
    public InfluenceActorVm? LeadActor => ActorRows().FirstOrDefault();
    public InfluenceSummaryVm? LeadPropaganda => PropagandaRows().FirstOrDefault();
    public InfluenceSummaryVm? LeadHerd => HerdRows().FirstOrDefault();

    public event Action? OnChanged;

    public InfluenceService(ApiService api) => _api = api;

    public async Task LoadAsync()
    {
        Error = "";
        try { Actors = await _api.GetAsync<JsonElement?>("api/influence/actors"); }
        catch (Exception ex) { Error = ex.Message; }
        Notify();
    }

    public async Task LoadPropAsync()
    {
        try { Prop = await _api.GetAsync<JsonElement?>("api/influence/propaganda"); } catch { }
        Notify();
    }

    public async Task LoadHerdAsync()
    {
        try { Herd = await _api.GetAsync<JsonElement?>("api/influence/herd"); } catch { }
        Notify();
    }

    public List<JsonElement> ActorItems()
    {
        if (Actors is null) return new();
        var root = Actors.Value;
        if (root.ValueKind == JsonValueKind.Array) return root.EnumerateArray().ToList();
        if (root.TryGetProperty("actors", out var a) && a.ValueKind == JsonValueKind.Array) return a.EnumerateArray().ToList();
        if (root.TryGetProperty("data",   out var d) && d.ValueKind == JsonValueKind.Array) return d.EnumerateArray().ToList();
        return new();
    }

    public List<InfluenceActorVm> ActorRows()
    {
        return ActorItems()
            .Select(a => new InfluenceActorVm(
                ReadString(a, "name", "actor") ?? "-",
                ReadDouble(a, "score", "influence_score"),
                ReadInt(a, "mentions", "mention_count"),
                ReadInt(a, "amplified", "amplification_count"),
                ReadString(a, "primary_region", "region"),
                ReadString(a, "driver", "reason")))
            .Where(a => !string.IsNullOrWhiteSpace(a.Name))
            .OrderByDescending(a => a.Score)
            .ToList();
    }

    public List<InfluenceSummaryVm> PropagandaRows()
    {
        return ParseSummaryRows(Prop, "campaigns", "signals", "items");
    }

    public List<InfluenceSummaryVm> HerdRows()
    {
        return ParseSummaryRows(Herd, "clusters", "herds", "items");
    }

    private static List<InfluenceSummaryVm> ParseSummaryRows(JsonElement? payload, params string[] keys)
    {
        if (payload is null)
            return new();

        var root = payload.Value;
        if (root.ValueKind == JsonValueKind.Array)
            return root.EnumerateArray().Select(ToSummary).ToList();

        foreach (var key in keys)
        {
            if (root.TryGetProperty(key, out var value) && value.ValueKind == JsonValueKind.Array)
                return value.EnumerateArray().Select(ToSummary).ToList();
        }

        return new();
    }

    private static InfluenceSummaryVm ToSummary(JsonElement item)
    {
        return new InfluenceSummaryVm(
            ReadString(item, "title", "name", "topic", "label") ?? "Signal",
            ReadDouble(item, "score", "risk_score", "strength"),
            ReadString(item, "driver", "reason", "type") ?? "mixed pressure",
            ReadString(item, "region", "top_region"),
            ReadString(item, "recommended_action", "action"),
            ReadString(item, "summary", "description", "briefing"));
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

    private static int ReadInt(JsonElement item, params string[] keys)
    {
        foreach (var key in keys)
            if (item.TryGetProperty(key, out var value) && value.ValueKind == JsonValueKind.Number)
                return value.GetInt32();
        return 0;
    }

    private void Notify() => OnChanged?.Invoke();
}

public sealed record InfluenceActorVm(
    string Name,
    double Score,
    int Mentions,
    int Amplified,
    string? Region,
    string? Driver);

public sealed record InfluenceSummaryVm(
    string Title,
    double Score,
    string Driver,
    string? Region,
    string? Action,
    string? Summary);
