using System.Text.Json;

namespace VisionI.Web.Services;

public sealed class AdminDlqService
{
    private readonly ApiService _api;

    public JsonElement? Items   { get; private set; }
    public JsonElement? Last    { get; private set; }

    public event Action? OnChanged;

    public AdminDlqService(ApiService api) => _api = api;

    private DateTime _lastLoaded = DateTime.MinValue;
    public async Task EnsureLoadedAsync()
    {
        if (DateTime.UtcNow - _lastLoaded < TimeSpan.FromMinutes(2)) return;
        _lastLoaded = DateTime.UtcNow;
        await LoadAsync();
    }

    public async Task LoadAsync()
    {
        Items = await _api.GetAsync<JsonElement?>("api/admin/dlq?limit=50");
        Notify();
    }

    public async Task RetryAsync(int idx)
    {
        Last = await _api.PostAsync<JsonElement?>($"api/admin/dlq/retry?index={idx}", new { });
        await LoadAsync();
    }

    private void Notify() => OnChanged?.Invoke();
}
