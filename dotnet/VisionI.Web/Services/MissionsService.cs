using System.Text.Json;

namespace VisionI.Web.Services;

public sealed class MissionsService
{
    private readonly ApiService _api;

    public string Objective         { get; set; } = "";
    public string Context           { get; set; } = "";
    public bool Busy                { get; private set; }
    public bool Loading             { get; private set; } = true;
    public JsonElement? Missions    { get; private set; }
    public JsonElement? LastResult  { get; private set; }
    public JsonElement? Viewing     { get; private set; }
    public MissionRowVm? LeadMission => MissionRows().FirstOrDefault();

    public event Action? OnChanged;

    public MissionsService(ApiService api) => _api = api;

    private DateTime _lastLoaded = DateTime.MinValue;
    public async Task EnsureLoadedAsync()
    {
        if (DateTime.UtcNow - _lastLoaded < TimeSpan.FromMinutes(2)) return;
        _lastLoaded = DateTime.UtcNow;
        await LoadAsync();
    }

    public async Task LoadAsync()
    {
        Loading = true; Notify();
        try { Missions = await _api.GetAsync<JsonElement?>("api/agents/missions"); }
        catch { Missions = null; }
        finally { Loading = false; Notify(); }
    }

    public async Task StartAsync()
    {
        Busy = true; Notify();
        try
        {
            LastResult = await _api.PostAsync<JsonElement?>("api/agents/mission",
                new { objective = Objective, context = Context });
            Objective = "";
            await LoadAsync();
        }
        catch { }
        finally { Busy = false; Notify(); }
    }

    public async Task ViewAsync(string id)
    {
        try { Viewing = await _api.GetAsync<JsonElement?>($"api/agents/mission/{id}"); }
        catch { Viewing = null; }
        Notify();
    }

    public static string StatusCls(JsonElement m)
    {
        var s = m.TryGetProperty("status", out var sv) ? sv.GetString() ?? "" : "";
        return s.ToLower() switch { "complete" or "success" => "ok", "running" or "pending" => "warn", "failed" or "error" => "danger", _ => "" };
    }

    public static string StatusClsFromRow(string status)
        => status.ToLower() switch { "complete" or "success" => "ok", "running" or "pending" => "warn", "failed" or "error" => "danger", _ => "" };

    public List<JsonElement> MissionItems()
    {
        if (Missions is null) return new();
        var root = Missions.Value;
        if (root.ValueKind == JsonValueKind.Array) return root.EnumerateArray().ToList();
        if (root.TryGetProperty("missions", out var m) && m.ValueKind == JsonValueKind.Array) return m.EnumerateArray().ToList();
        if (root.TryGetProperty("data",     out var d) && d.ValueKind == JsonValueKind.Array) return d.EnumerateArray().ToList();
        return new();
    }

    public List<MissionRowVm> MissionRows()
    {
        return MissionItems()
            .Select(m => new MissionRowVm(
                ReadString(m, "mission_id", "id") ?? "",
                ReadString(m, "objective") ?? "-",
                ReadString(m, "status") ?? "unknown",
                ReadString(m, "created_at") ?? "-",
                ReadDouble(m, "confidence"),
                ReadString(m, "recommended_action", "next_action"),
                ReadString(m, "driver", "reason")))
            .Where(m => !string.IsNullOrWhiteSpace(m.MissionId))
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

public sealed record MissionRowVm(
    string MissionId,
    string Objective,
    string Status,
    string CreatedAt,
    double Confidence,
    string? Action,
    string? Driver);
