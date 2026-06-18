using System.Text.Json;

namespace VisionI.Web.Services;

public sealed class StreamsService : IDisposable
{
    private readonly ApiService _api;
    private System.Timers.Timer? _timer;

    public JsonElement? Items   { get; private set; }
    public bool AutoRefresh     { get; private set; } = true;
    public bool IsWarming =>
        Items is { } root &&
        root.TryGetProperty("_served_from", out var sf) &&
        sf.GetString() == "degraded";
    public List<StreamEventRowVm> StreamRows => StreamItems().Select(item =>
    {
        var source = GetString(item, "source");
        var title = GetString(item, "title", "content");
        var category = GetString(item, "category");
        var risk = ParseDouble(GetString(item, "risk_score"));
        var sentiment = ParseDouble(GetString(item, "sentiment"));
        var driver = risk >= 0.7 ? "high-risk ingest"
            : sentiment <= -0.15 ? "negative sentiment"
            : !string.IsNullOrWhiteSpace(category) ? $"{category} flow"
            : "fresh source activity";
        var action = risk >= 0.7 ? "open live events"
            : sentiment <= -0.15 ? "watch source"
            : "monitor";
        return new StreamEventRowVm(source, title, category, risk, sentiment, driver, action, GetString(item, "timestamp"));
    }).ToList();

    public event Action? OnChanged;

    public StreamsService(ApiService api) => _api = api;

    private DateTime _lastLoaded = DateTime.MinValue;
    public async Task EnsureLoadedAsync()
    {
        if (DateTime.UtcNow - _lastLoaded < TimeSpan.FromMinutes(2)) return;
        _lastLoaded = DateTime.UtcNow;
        await LoadAsync();
    }

    public async Task LoadAsync()
    {
        try { Items = await _api.GetAsync<JsonElement?>("api/streams/live"); }
        catch { Items = null; }
        Notify();
    }

    public void StartTimer()
    {
        _timer = new System.Timers.Timer(10_000) { AutoReset = true };
        _timer.Elapsed += async (_, _) => { if (AutoRefresh) await LoadAsync(); };
        _timer.Start();
    }

    public void ToggleAuto()
    {
        AutoRefresh = !AutoRefresh;
        Notify();
    }

    public List<JsonElement> StreamItems()
    {
        if (Items is null) return new();
        var root = Items.Value;
        if (root.ValueKind == JsonValueKind.Array) return root.EnumerateArray().ToList();
        if (root.TryGetProperty("events", out var e) && e.ValueKind == JsonValueKind.Array) return e.EnumerateArray().ToList();
        if (root.TryGetProperty("data",   out var d) && d.ValueKind == JsonValueKind.Array) return d.EnumerateArray().ToList();
        return new();
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

    private static double ParseDouble(string raw)
        => double.TryParse(raw, out var parsed) ? parsed : 0;

    public void Dispose() => _timer?.Dispose();

    private void Notify() => OnChanged?.Invoke();
}

public sealed record StreamEventRowVm(
    string Source,
    string Title,
    string Category,
    double Risk,
    double Sentiment,
    string Driver,
    string Action,
    string Timestamp);
