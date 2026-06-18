using System.Text.Json;

namespace VisionI.Web.Services;

public sealed class AdminUsersService
{
    private readonly ApiService _api;

    public JsonElement? Doc     { get; private set; }
    public bool Loading         { get; private set; } = true;

    public event Action? OnChanged;

    public AdminUsersService(ApiService api) => _api = api;

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
        Doc = await _api.GetAsync<JsonElement?>("api/admin/users");
        Loading = false; Notify();
    }

    public async Task ChangeRoleAsync(string id, string role)
    {
        await _api.PatchAsync<object>($"api/admin/users/{id}/role", new { role });
        await LoadAsync();
    }

    public async Task ToggleAsync(string id, bool active)
    {
        await _api.PatchAsync<object>($"api/admin/users/{id}/status", new { isActive = active });
        await LoadAsync();
    }

    public async Task ResetPasswordAsync(string id)
    {
        await _api.PostAsync<object>($"api/admin/users/{id}/reset-password", new { });
    }

    public async Task DeleteAsync(string id)
    {
        await _api.DeleteAsync($"api/admin/users/{id}");
        await LoadAsync();
    }

    private void Notify() => OnChanged?.Invoke();
}
