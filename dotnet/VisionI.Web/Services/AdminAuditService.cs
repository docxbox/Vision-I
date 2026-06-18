using System.Text.Json;

namespace VisionI.Web.Services;

public sealed class AdminAuditService
{
    private readonly ApiService _api;

    public JsonElement? Data   { get; private set; }
    public bool         Loading { get; private set; }
    public string       ActionFilter { get; set; } = "";
    public string       UserFilter   { get; set; } = "";

    public event Action? OnChanged;

    public AdminAuditService(ApiService api) => _api = api;

    public async Task LoadAsync(int limit = 100, int offset = 0)
    {
        Loading = true;
        Notify();
        try
        {
            var q = $"api/admin/audit-log?limit={limit}&offset={offset}";
            if (!string.IsNullOrWhiteSpace(ActionFilter))
                q += $"&action={Uri.EscapeDataString(ActionFilter.Trim())}";
            if (!string.IsNullOrWhiteSpace(UserFilter))
                q += $"&userId={Uri.EscapeDataString(UserFilter.Trim())}";
            Data = await _api.GetAsync<JsonElement?>(q);
        }
        catch { Data = null; }
        finally { Loading = false; Notify(); }
    }

    public int Total => Data is { } d && d.TryGetProperty("total", out var t)
        ? t.GetInt32() : 0;

    public List<JsonElement> Entries()
    {
        if (Data is not { } d) return new();
        if (d.TryGetProperty("entries", out var arr) && arr.ValueKind == JsonValueKind.Array)
            return arr.EnumerateArray().ToList();
        return new();
    }

    private void Notify() => OnChanged?.Invoke();
}
