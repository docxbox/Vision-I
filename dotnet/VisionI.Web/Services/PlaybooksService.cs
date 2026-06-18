using System.Text.Json;

namespace VisionI.Web.Services;

public sealed class PlaybooksService
{
    private readonly ApiService _api;

    public JsonElement? Items   { get; private set; }
    public JsonElement? Detail  { get; private set; }
    public string Error         { get; private set; } = "";
    public bool Loading         { get; private set; } = true;
    public int PlaybookCount => PlaybookItems().Count;
    public List<PlaybookRowVm> PlaybookRows => PlaybookItems().Select(item =>
    {
        var id = GetString(item, "id", "playbook_id");
        var name = GetString(item, "name", "title");
        var objective = GetString(item, "objective", "description");
        var requiresApproval = item.TryGetProperty("requires_approval", out var requires) &&
                               requires.ValueKind == JsonValueKind.True;
        var stepCount = item.TryGetProperty("steps", out var steps) && steps.ValueKind == JsonValueKind.Array
            ? steps.GetArrayLength()
            : 0;
        var driver = requiresApproval ? "human approval" : "ready execution";
        var action = requiresApproval ? "review fit" : "execute when matched";
        return new PlaybookRowVm(id, string.IsNullOrWhiteSpace(name) ? "Playbook" : name, objective, stepCount, driver, action, requiresApproval);
    }).ToList();

    public event Action? OnChanged;

    public PlaybooksService(ApiService api) => _api = api;

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
        try { Items = await _api.GetAsync<JsonElement?>("api/playbooks"); }
        catch (Exception ex) { Error = ex.Message; }
        finally { Loading = false; Notify(); }
    }

    public async Task ViewAsync(string id)
    {
        try { Detail = await _api.GetAsync<JsonElement?>($"api/playbooks/{id}"); } catch { }
        Notify();
    }

    public async Task ExecuteAsync(string id)
    {
        try { Detail = await _api.PostAsync<JsonElement?>($"api/playbooks/{id}/execute", new { context = new { } }); } catch { }
        Notify();
    }

    public List<JsonElement> PlaybookItems()
    {
        if (Items is null) return new();
        var root = Items.Value;
        if (root.ValueKind == JsonValueKind.Array) return root.EnumerateArray().ToList();
        if (root.TryGetProperty("playbooks", out var p) && p.ValueKind == JsonValueKind.Array) return p.EnumerateArray().ToList();
        if (root.TryGetProperty("data",      out var d) && d.ValueKind == JsonValueKind.Array) return d.EnumerateArray().ToList();
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

    private void Notify() => OnChanged?.Invoke();
}

public sealed record PlaybookRowVm(
    string Id,
    string Name,
    string Objective,
    int StepCount,
    string Driver,
    string Action,
    bool RequiresApproval);
