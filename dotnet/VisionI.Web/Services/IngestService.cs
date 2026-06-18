using System.Text.Json;

namespace VisionI.Web.Services;

public sealed class IngestService
{
    private readonly ApiService _api;

    public string Query         { get; set; } = "";
    public string Sources       { get; set; } = "";
    public JsonElement? Job     { get; private set; }
    public JsonElement? Jobs    { get; private set; }
    public bool Busy            { get; private set; }
    public IngestJobRowVm? LeadJob => JobRows().FirstOrDefault();

    public event Action? OnChanged;

    public IngestService(ApiService api) => _api = api;

    private DateTime _lastLoaded = DateTime.MinValue;
    public async Task EnsureLoadedAsync()
    {
        if (DateTime.UtcNow - _lastLoaded < TimeSpan.FromMinutes(2)) return;
        _lastLoaded = DateTime.UtcNow;
        await LoadJobsAsync();
    }

    public async Task LoadJobsAsync()
    {
        try { Jobs = await _api.GetAsync<JsonElement?>("api/admin/jobs?limit=30"); }
        catch { Jobs = null; }
        Notify();
    }

    public async Task TriggerAsync()
    {
        Busy = true; Notify();
        try
        {
            Job = await _api.PostAsync<JsonElement?>("api/ingest", new
            {
                query   = Query,
                sources = string.IsNullOrEmpty(Sources) ? null : Sources.Split(',')
            });
            await Task.Delay(1500);
            await LoadJobsAsync();
        }
        catch { }
        finally { Busy = false; Notify(); }
    }

    public async Task TriggerLiveAsync()
    {
        try { Job = await _api.PostAsync<JsonElement?>("api/admin/trigger-live", new { }); } catch { }
        await LoadJobsAsync();
    }

    public async Task PollAsync(string id)
    {
        try { Job = await _api.GetAsync<JsonElement?>($"api/ingest/{id}"); } catch { }
        Notify();
    }

    public static string JobCls(string s) => s.ToLower() switch
    {
        "complete" or "success" => "ok",
        "running"  or "pending" => "warn",
        "failed"   or "error"   => "danger",
        _ => ""
    };

    public List<IngestJobRowVm> JobRows()
    {
        if (Jobs is null)
            return new();

        var root = Jobs.Value;
        IEnumerable<JsonElement> rows = Enumerable.Empty<JsonElement>();
        if (root.ValueKind == JsonValueKind.Array)
            rows = root.EnumerateArray();
        else if (root.TryGetProperty("jobs", out var jobs) && jobs.ValueKind == JsonValueKind.Array)
            rows = jobs.EnumerateArray();
        else if (root.TryGetProperty("data", out var data) && data.ValueKind == JsonValueKind.Array)
            rows = data.EnumerateArray();

        return rows.Select(j => new IngestJobRowVm(
            ReadString(j, "job_id", "id") ?? "",
            ReadString(j, "status") ?? "unknown",
            ReadString(j, "started_at") ?? "-",
            j.TryGetProperty("sources", out var sources) ? sources.ToString() : "-",
            ReadInt(j, "count"),
            ReadString(j, "query"),
            ReadString(j, "recommended_action", "action")))
            .Where(j => !string.IsNullOrWhiteSpace(j.JobId))
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

    private void Notify() => OnChanged?.Invoke();
}

public sealed record IngestJobRowVm(
    string JobId,
    string Status,
    string StartedAt,
    string Sources,
    int Count,
    string? Query,
    string? Action);
