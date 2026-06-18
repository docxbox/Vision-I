using System.Text.Json;

namespace VisionI.Web.Services;

public sealed class SentimentService
{
    private readonly ApiService _api;

    public string Tab       { get; set; } = "time";
    public string Days      { get; set; } = "3";
    public string Bucket    { get; set; } = "hour";
    public string Hours     { get; set; } = "48";
    public JsonElement? Timeline    { get; private set; }
    public JsonElement? Heat        { get; private set; }
    public bool Loading             { get; private set; }
    public string? Spike            { get; private set; }
    public List<(string label, string value, string cls)> Kpis { get; private set; } = new();
    public SentimentHeatRowVm? LeadHeatRow => HeatRowsData().OrderByDescending(r => r.Risk).FirstOrDefault();

    public event Action? OnChanged;

    public SentimentService(ApiService api) => _api = api;

    public async Task LoadAsync()
    {
        var t1 = LoadTimelineAsync();
        var t2 = Tab == "heat" ? LoadHeatAsync() : Task.CompletedTask;
        await Task.WhenAll(t1, t2);
    }

    public async Task LoadTimelineAsync()
    {
        Loading = true; Notify();
        try { Timeline = await _api.GetAsync<JsonElement?>($"api/sentiment/timeline?bucket={Bucket}&hours={Hours}"); }
        catch { Timeline = null; }
        finally { Loading = false; }
        Spike = DetectSpike();
        BuildKpis();
        Notify();
    }

    public async Task LoadHeatAsync()
    {
        try { Heat = await _api.GetAsync<JsonElement?>($"api/sentiment/country-heatmap?days_back={Days}"); }
        catch { Heat = null; }
        Notify();
    }

    public void BuildKpis()
    {
        Kpis.Clear();
        var rows = TimelineRows();
        if (rows.Count == 0) return;
        var latest = rows[^1];
        var first  = rows[0];
        var negNow  = Num(latest, "negative");
        var negThen = Num(first,  "negative");
        var posNow  = Num(latest, "positive");
        Kpis.Add(("NEG SENTIMENT", negNow.ToString("0.00"), negNow >= 0.5 ? "danger" : negNow >= 0.3 ? "warn" : "ok"));
        Kpis.Add(("POS SENTIMENT", posNow.ToString("0.00"), posNow >= 0.5 ? "ok" : "accent"));
        Kpis.Add(("BUCKETS", rows.Count.ToString(), "accent"));
        if (rows.Count > 1)
        {
            var delta = negNow - negThen;
            Kpis.Add(("NEG dTREND", $"{(delta >= 0 ? "+" : "")}{delta:0.00}", delta >= 0.15 ? "danger" : delta >= 0.05 ? "warn" : "ok"));
        }
    }

    public string? DetectSpike()
    {
        var rows = TimelineRows();
        if (rows.Count < 3) return null;
        var last3 = rows.TakeLast(3).ToList();
        var delta = Num(last3[^1], "negative") - Num(last3[0], "negative");
        return delta >= 0.25 ? $"Negative sentiment up {delta * 100:0}% in last {last3.Count} buckets" : null;
    }

    public List<JsonElement> TimelineRows()
    {
        if (Timeline is null) return new();
        var root = Timeline.Value;
        if (root.ValueKind == JsonValueKind.Array) return root.EnumerateArray().ToList();
        if (root.TryGetProperty("data", out var d) && d.ValueKind == JsonValueKind.Array) return d.EnumerateArray().ToList();
        return new();
    }

    public List<JsonElement> HeatRows()
    {
        if (Heat is null) return new();
        var root = Heat.Value;
        if (root.ValueKind == JsonValueKind.Array) return root.EnumerateArray().ToList();
        if (root.TryGetProperty("countries", out var c) && c.ValueKind == JsonValueKind.Array) return c.EnumerateArray().ToList();
        if (root.TryGetProperty("data",      out var d) && d.ValueKind == JsonValueKind.Array) return d.EnumerateArray().ToList();
        return new();
    }

    public List<SentimentHeatRowVm> HeatRowsData()
    {
        return HeatRows()
            .Select(c => new SentimentHeatRowVm(
                ReadString(c, "country") ?? "-",
                Num(c, "sentiment", "avg_score"),
                Num(c, "risk_score", "risk"),
                ReadInt(c, "count", "event_count"),
                ReadString(c, "driver", "reason"),
                ReadString(c, "recommended_action", "action")))
            .OrderByDescending(c => c.Risk)
            .ToList();
    }

    public List<SentimentTimelinePointVm> TimelineData()
    {
        return TimelineRows()
            .Select(r => new SentimentTimelinePointVm(
                ReadString(r, "bucket", "t") ?? "",
                Num(r, "positive"),
                Num(r, "neutral"),
                Num(r, "negative")))
            .ToList();
    }

    private static string? ReadString(JsonElement item, params string[] keys)
    {
        foreach (var key in keys)
            if (item.TryGetProperty(key, out var value) && value.ValueKind == JsonValueKind.String)
                return value.GetString();
        return null;
    }

    private static int ReadInt(JsonElement item, params string[] keys)
    {
        foreach (var key in keys)
            if (item.TryGetProperty(key, out var value) && value.ValueKind == JsonValueKind.Number)
                return value.GetInt32();
        return 0;
    }

    public static double Num(JsonElement e, params string[] keys)
    {
        foreach (var key in keys)
            if (e.TryGetProperty(key, out var v) && v.ValueKind == JsonValueKind.Number)
                return v.GetDouble();
        return 0;
    }

    private void Notify() => OnChanged?.Invoke();
}

public sealed record SentimentHeatRowVm(
    string Country,
    double Sentiment,
    double Risk,
    int Count,
    string? Driver,
    string? Action);

public sealed record SentimentTimelinePointVm(
    string Bucket,
    double Positive,
    double Neutral,
    double Negative);
