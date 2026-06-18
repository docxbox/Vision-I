using System.Text.Json;

namespace VisionI.Web.Services;

public sealed class AdminStatsService
{
    private readonly ApiService _api;

    public JsonElement? Stats   { get; private set; }
    public JsonElement? Signals { get; private set; }

    public event Action? OnChanged;

    public AdminStatsService(ApiService api) => _api = api;

    private DateTime _lastLoaded = DateTime.MinValue;
    public async Task EnsureLoadedAsync()
    {
        if (DateTime.UtcNow - _lastLoaded < TimeSpan.FromMinutes(2)) return;
        _lastLoaded = DateTime.UtcNow;
        await LoadAsync();
    }

    public async Task LoadAsync()
    {
        Stats   = await _api.GetAsync<JsonElement?>("api/admin/stats");
        Signals = await _api.GetAsync<JsonElement?>("api/admin/signals/stats");
        Notify();
    }

    private void Notify() => OnChanged?.Invoke();
}
