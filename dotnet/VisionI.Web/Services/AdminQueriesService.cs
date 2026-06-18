using VisionI.Web.Models;

namespace VisionI.Web.Services;

public sealed class AdminQueriesService
{
    private readonly ApiService _api;

    public List<TrackedQuery> Queries   { get; private set; } = new();
    public string QueryText             { get; set; } = "";
    public bool Busy                    { get; private set; }

    public event Action? OnChanged;

    public AdminQueriesService(ApiService api) => _api = api;

    private DateTime _lastLoaded = DateTime.MinValue;
    public async Task EnsureLoadedAsync()
    {
        if (DateTime.UtcNow - _lastLoaded < TimeSpan.FromMinutes(2)) return;
        _lastLoaded = DateTime.UtcNow;
        await LoadAsync();
    }

    public async Task LoadAsync()
    {
        var resp = await _api.GetAsync<QueriesResponse>("api/admin/queries");
        Queries = resp?.Queries ?? [];
        Notify();
    }

    public async Task AddAsync()
    {
        if (string.IsNullOrWhiteSpace(QueryText)) return;
        Busy = true; Notify();
        try
        {
            await _api.PostAsync<object>("api/admin/queries", new { query = QueryText.Trim() });
            QueryText = "";
            await LoadAsync();
        }
        finally { Busy = false; Notify(); }
    }

    public async Task RemoveAsync(int id)
    {
        Busy = true; Notify();
        try
        {
            await _api.DeleteAsync($"api/admin/queries/{id}");
            await LoadAsync();
        }
        finally { Busy = false; Notify(); }
    }

    public static string FormatTime(string? value)
        => DateTime.TryParse(value, out var parsed) ? parsed.ToUniversalTime().ToString("yyyy-MM-dd HH:mm'Z'") : "-";

    private void Notify() => OnChanged?.Invoke();
}
