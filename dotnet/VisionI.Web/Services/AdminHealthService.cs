using System.Text.Json;

namespace VisionI.Web.Services;

public sealed class AdminHealthService
{
    private readonly ApiService _api;

    public JsonElement? Health      { get; private set; }
    public JsonElement? Topology    { get; private set; }

    public event Action? OnChanged;

    public AdminHealthService(ApiService api) => _api = api;

    private DateTime _lastLoaded = DateTime.MinValue;
    public async Task EnsureLoadedAsync()
    {
        if (DateTime.UtcNow - _lastLoaded < TimeSpan.FromMinutes(2)) return;
        _lastLoaded = DateTime.UtcNow;
        await LoadAsync();
    }

    public async Task LoadAsync()
    {
        Health   = await _api.GetAsync<JsonElement?>("api/admin/health");
        Topology = await _api.GetAsync<JsonElement?>("api/admin/data-pipeline");
        Notify();
    }

    public List<SummaryCard> SummaryCards()
    {
        if (Health is null) return [];
        var cards = new List<SummaryCard>
        {
            new("Overall",  ReadStr(Health.Value, "status", "unknown").ToUpperInvariant(), ToneFor(ReadStr(Health.Value, "status", "unknown")), ReadStr(Health.Value, "timestamp", "")),
            new("Data Store", ReadBool(Health.Value, "db_available")    ? "READY" : "DOWN",  ReadBool(Health.Value, "db_available")    ? "ok" : "danger", ""),
            new("Knowledge",  ReadBool(Health.Value, "neo4j_available") ? "READY" : "DOWN",  ReadBool(Health.Value, "neo4j_available") ? "ok" : "danger", ""),
        };
        if (Health.Value.TryGetProperty("llm", out var llm) && llm.ValueKind == JsonValueKind.Object)
            cards.Add(new("AI Runtime", ReadBool(llm, "available") ? "ONLINE" : "OFFLINE", ReadBool(llm, "available") ? "ok" : "danger",
                $"{ReadStr(llm, "provider", "none")} | {ReadStr(llm, "model", "n/a")}"));
        return cards;
    }

    public List<ServiceStatus> ServiceStatuses()
    {
        if (Topology is null || !Topology.Value.TryGetProperty("services", out var svcs) || svcs.ValueKind != JsonValueKind.Object)
            return [];
        return svcs.EnumerateObject()
            .Select(p => new ServiceStatus(p.Name.Replace("_", " ").ToUpperInvariant(), p.Value.ValueKind == JsonValueKind.True))
            .ToList();
    }

    public List<SchedulerJobRow> SchedulerJobs()
    {
        if (Health is null || !Health.Value.TryGetProperty("scheduler", out var sched) || sched.ValueKind != JsonValueKind.Object ||
            !sched.TryGetProperty("jobs", out var jobs) || jobs.ValueKind != JsonValueKind.Array)
            return [];
        return jobs.EnumerateArray()
            .Select(j => new SchedulerJobRow(ReadStr(j, "id", "-"), ReadStr(j, "name", "-"), ReadStr(j, "next_run", "-")))
            .ToList();
    }

    public List<SourceRow> SourceRows()
    {
        if (Health is null || !Health.Value.TryGetProperty("sources", out var srcs) || srcs.ValueKind != JsonValueKind.Object)
            return [];
        return srcs.EnumerateObject().Select(p =>
        {
            var detail  = p.Value.TryGetProperty("detail", out var dv) ? dv.ToString() : "-";
            var circuit = p.Value.TryGetProperty("circuit_breaker", out var cb) && cb.ValueKind == JsonValueKind.Object ? ReadStr(cb, "state", "closed") : "closed";
            var status  = ReadStr(p.Value, "status", "unknown");
            return new SourceRow(p.Name, status, detail, circuit, ToneFor(status));
        }).ToList();
    }

    public static string ReadStr(JsonElement el, string prop, string fallback)
        => el.TryGetProperty(prop, out var v) && v.ValueKind == JsonValueKind.String ? v.GetString() ?? fallback : fallback;

    public static bool ReadBool(JsonElement el, string prop)
        => el.TryGetProperty(prop, out var v) && v.ValueKind == JsonValueKind.True;

    public static string ToneFor(string status) => status.ToLower() switch
    {
        "healthy" or "ok" or "ready" => "ok",
        "degraded" or "warn"         => "warn",
        _                            => "danger"
    };

    private void Notify() => OnChanged?.Invoke();
}

public sealed record SummaryCard(string Label, string Value, string Tone, string Detail);
public sealed record ServiceStatus(string Label, bool Enabled);
public sealed record SchedulerJobRow(string Id, string Name, string NextRun);
public sealed record SourceRow(string Name, string Status, string Detail, string Circuit, string Tone);
