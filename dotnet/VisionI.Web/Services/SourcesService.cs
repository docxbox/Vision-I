using System.Text.Json;

namespace VisionI.Web.Services;

public sealed class SourcesService
{
    private readonly ApiService _api;

    public string Tab       { get; set; } = "cat";
    public string Query     { get; set; } = "";
    public JsonElement? Catalog { get; private set; }
    public JsonElement? Result  { get; private set; }
    public List<SourceCatalogRowVm> CatalogRows => CatalogItems().Select(item =>
    {
        var label = GetSourceLabel(item);
        var status = GetStatus(item);
        var category = item.TryGetProperty("category", out var categoryValue) && categoryValue.ValueKind == JsonValueKind.String
            ? categoryValue.GetString() ?? ""
            : "";
        var driver = status switch
        {
            "HEALTHY" => "coverage ready",
            "DEGRADED" => "stale or partial health",
            "UNKNOWN" => "health pending",
            "NOT CONFIGURED" => "credentials missing",
            _ => "connector down"
        };
        var action = status switch
        {
            "HEALTHY" => "use when needed",
            "DEGRADED" => "check recent source runs",
            "UNKNOWN" => "refresh or verify scheduler",
            "NOT CONFIGURED" => "fix provider setup",
            _ => "verify source health"
        };
        return new SourceCatalogRowVm(label, status, category, driver, action);
    }).ToList();

    public static readonly string[] AvailableSources =
        { "news", "reddit", "youtube", "rss", "hackernews", "gdelt", "usgs", "stocks", "opensky", "ais" };

    public event Action? OnChanged;

    public SourcesService(ApiService api) => _api = api;

    private DateTime _lastLoaded = DateTime.MinValue;
    public async Task EnsureLoadedAsync()
    {
        if (DateTime.UtcNow - _lastLoaded < TimeSpan.FromMinutes(2)) return;
        _lastLoaded = DateTime.UtcNow;
        await LoadAsync();
    }

    public async Task LoadAsync()
    {
        try { Catalog = await _api.GetAsync<JsonElement?>("api/sources/catalog"); }
        catch { Catalog = null; }
        Notify();
    }

    public async Task SearchAsync()
    {
        var p = Tab switch
        {
            "stocks"  => $"api/sources/{Tab}?tickers={Uri.EscapeDataString(Query)}",
            "opensky" => $"api/sources/{Tab}?{Query}",
            "usgs"    => $"api/sources/{Tab}?{Query}",
            _         => $"api/sources/{Tab}?query={Uri.EscapeDataString(Query)}"
        };
        try { Result = await _api.GetAsync<JsonElement?>(p); }
        catch { Result = null; }
        Notify();
    }

    public List<JsonElement> CatalogItems()
    {
        if (Catalog is null) return new();
        var root = Catalog.Value;
        if (root.ValueKind == JsonValueKind.Array) return root.EnumerateArray().ToList();
        if (root.TryGetProperty("sources", out var s) && s.ValueKind == JsonValueKind.Array) return s.EnumerateArray().ToList();
        if (root.TryGetProperty("data",    out var d) && d.ValueKind == JsonValueKind.Array) return d.EnumerateArray().ToList();
        return new();
    }

    public static string GetSourceLabel(JsonElement src)
    {
        if (src.TryGetProperty("label", out var l) && l.ValueKind == JsonValueKind.String) return l.GetString() ?? "-";
        if (src.TryGetProperty("name",  out var n) && n.ValueKind == JsonValueKind.String) return n.GetString() ?? "-";
        if (src.TryGetProperty("key",   out var k) && k.ValueKind == JsonValueKind.String) return k.GetString() ?? "-";
        return "-";
    }

    public static bool IsHealthy(JsonElement src)
    {
        if (src.TryGetProperty("healthy", out var h) && h.ValueKind is JsonValueKind.True or JsonValueKind.False)
            return h.GetBoolean();
        if (src.TryGetProperty("health", out var health) && health.ValueKind == JsonValueKind.Object &&
            health.TryGetProperty("status", out var status) && status.ValueKind == JsonValueKind.String)
        {
            var raw = status.GetString();
            return string.Equals(raw, "healthy", StringComparison.OrdinalIgnoreCase) ||
                   string.Equals(raw, "ok", StringComparison.OrdinalIgnoreCase);
        }
        return false;
    }

    /// <summary>Returns "HEALTHY" | "DEGRADED" | "NOT CONFIGURED" | "DOWN" | "UNKNOWN"</summary>
    public static string GetStatus(JsonElement src)
    {
        if (IsHealthy(src)) return "HEALTHY";
        if (src.TryGetProperty("health", out var health) && health.ValueKind == JsonValueKind.Object &&
            health.TryGetProperty("status", out var status) && status.ValueKind == JsonValueKind.String)
        {
            return (status.GetString() ?? "").ToLowerInvariant() switch
            {
                "degraded" => "DEGRADED",
                "stale" => "DEGRADED",
                "not_configured" => "NOT CONFIGURED",
                "credentials_missing" => "NOT CONFIGURED",
                "down" => "DOWN",
                "error" => "DOWN",
                "timeout" => "DOWN",
                "circuit_open" => "DOWN",
                "unknown" => "UNKNOWN",
                _ => "UNKNOWN"
            };
        }
        var requiresCreds = src.TryGetProperty("requires_credentials", out var rc) &&
                            rc.ValueKind == JsonValueKind.True;
        return requiresCreds ? "NOT CONFIGURED" : "UNKNOWN";
    }

    private void Notify() => OnChanged?.Invoke();
}

public sealed record SourceCatalogRowVm(
    string Label,
    string Status,
    string Category,
    string Driver,
    string Action);
