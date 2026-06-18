using System.Text.Json;

namespace VisionI.Web.Services;

public sealed class AdminJobsService
{
    private readonly ApiService _api;

    public JsonElement? Items { get; private set; }
    public List<AdminJobRowVm> Rows => ParseRows();
    public AdminJobRowVm? Selected { get; private set; }
    public bool Retrying { get; private set; }
    public string RetryMsg { get; private set; } = "";

    public event Action? OnChanged;

    public AdminJobsService(ApiService api) => _api = api;

    private DateTime _lastLoaded = DateTime.MinValue;
    public async Task EnsureLoadedAsync()
    {
        if (DateTime.UtcNow - _lastLoaded < TimeSpan.FromMinutes(2)) return;
        _lastLoaded = DateTime.UtcNow;
        await LoadAsync();
    }

    public async Task LoadAsync()
    {
        Items = await _api.GetAsync<JsonElement?>("api/admin/jobs?limit=100");
        Notify();
    }

    public void Select(AdminJobRowVm row)
    {
        Selected = Selected?.JobId == row.JobId ? null : row;
        RetryMsg = "";
        Notify();
    }

    public async Task RetryJobAsync(AdminJobRowVm row)
    {
        Retrying = true;
        RetryMsg = "";
        Notify();
        try
        {
            var sources = row.Sources is "-" or "" ? null
                : row.Sources.Trim('[', ']', '"').Split(',')
                    .Select(s => s.Trim().Trim('"'))
                    .Where(s => !string.IsNullOrWhiteSpace(s))
                    .ToArray();
            var result = await _api.PostAsync<JsonElement?>("api/ingest", new
            {
                query   = string.IsNullOrWhiteSpace(row.Query) ? "retry" : row.Query,
                sources
            });
            RetryMsg = result.HasValue ? "RETRY TRIGGERED" : "RETRY FAILED — no response";
            await LoadAsync();
        }
        catch (Exception ex) { RetryMsg = $"RETRY FAILED: {ex.Message}"; }
        finally { Retrying = false; Notify(); }
    }

    public static string Cls(string s) => s.ToLower() switch
    {
        "complete" or "success" or "completed" => "ok",
        "running"  or "pending" => "warn",
        "failed"   or "error"   => "err",
        _ => ""
    };

    private void Notify() => OnChanged?.Invoke();

    private List<AdminJobRowVm> ParseRows()
    {
        if (Items is null || Items.Value.ValueKind != JsonValueKind.Array)
            return new();

        return Items.Value.EnumerateArray().Select(j =>
        {
            var status = j.TryGetProperty("status", out var sv) ? sv.GetString() ?? "" : "";
            return new AdminJobRowVm(
                j.TryGetProperty("job_id", out var i) ? i.GetString() ?? "-" : "-",
                status,
                j.TryGetProperty("sources", out var s) ? s.ToString() : "-",
                j.TryGetProperty("count", out var c) ? c.ToString() : "-",
                j.TryGetProperty("started_at", out var st) ? st.GetString() ?? "-" : "-",
                j.TryGetProperty("duration_ms", out var d) ? $"{d}ms" : "-",
                j.TryGetProperty("query", out var q) ? q.GetString() ?? "" : "",
                j.TryGetProperty("error", out var e) ? e.GetString() ?? "" : "");
        }).ToList();
    }
}

public sealed record AdminJobRowVm(
    string JobId,
    string Status,
    string Sources,
    string Count,
    string StartedAt,
    string Duration,
    string Query,
    string Error);
