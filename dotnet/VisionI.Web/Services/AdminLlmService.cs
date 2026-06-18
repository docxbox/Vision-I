using VisionI.Web.Models;
using System.Text.Json;

namespace VisionI.Web.Services;

public sealed class AdminLlmService
{
    private readonly ApiService _api;

    public LlmProvidersResponse? Current { get; private set; }
    public JsonElement? Result { get; private set; }
    public string Provider { get; set; } = "openrouter";
    public string Model { get; set; } = "";
    public string BaseUrl { get; set; } = "";
    public string ApiKey { get; set; } = "";
    public bool IsEnabled { get; set; } = true;
    public bool IsDefault { get; set; } = true;
    public bool Busy { get; private set; }

    public LlmProviderOptionDto? SelectedProviderOption =>
        Current?.SupportedProviders.FirstOrDefault(p => string.Equals(p.Key, Provider, StringComparison.OrdinalIgnoreCase));

    public LlmProviderConfigDto? SelectedSavedConfig =>
        Current?.Providers.FirstOrDefault(p => string.Equals(p.Provider, Provider, StringComparison.OrdinalIgnoreCase));

    public bool IsOpenRouter => string.Equals(Provider, "openrouter", StringComparison.OrdinalIgnoreCase);

    public string[] EffectiveModels =>
        (IsOpenRouter ? SelectedProviderOption?.DefaultModel : Model)?
            .Split(',', StringSplitOptions.RemoveEmptyEntries | StringSplitOptions.TrimEntries)
        ?? Array.Empty<string>();

    public bool HasStoredKey => !string.IsNullOrWhiteSpace(SelectedSavedConfig?.ApiKeyMasked);

    public bool HasSavedDefault => Current?.Providers.Any(p => p.IsEnabled && p.IsDefault) == true;

    public bool RuntimeOnline => Current?.Runtime?.Available == true;

    public string RuntimeSummary =>
        RuntimeOnline
            ? $"JARVIS .NET runtime online via {Current?.Runtime?.Provider ?? "provider"}."
            : HasSavedDefault
                ? "Saved provider exists, but the .NET JARVIS runtime is not active yet. Check the API key or restart dotnet-api."
                : "No active provider is saved. Paste an OpenRouter key and click Save & Activate JARVIS.";

    public string SetupSummary =>
        SelectedProviderOption is null
            ? "Select a provider to load the correct model and connection defaults."
            : IsOpenRouter
                ? "OpenRouter is locked to free-only routes. Add or replace the API key; paid model overrides are ignored."
                : $"{SelectedProviderOption.Label} uses {SelectedProviderOption.DefaultModel} by default and {(SelectedProviderOption.RequiresApiKey ? "requires an API key." : "does not require an API key.")}";

    public event Action? OnChanged;

    public AdminLlmService(ApiService api) => _api = api;

    private DateTime _lastLoaded = DateTime.MinValue;
    public async Task EnsureLoadedAsync()
    {
        if (DateTime.UtcNow - _lastLoaded < TimeSpan.FromMinutes(2)) return;
        _lastLoaded = DateTime.UtcNow;
        await LoadAsync();
    }

    public async Task LoadAsync()
    {
        Current = await _api.GetAsync<LlmProvidersResponse>("api/admin/llm/providers");
        if (Current?.SupportedProviders.Any() == true)
        {
            var currentKey = Current.Runtime?.Provider;
            var defaultOption = Current.SupportedProviders.FirstOrDefault(p =>
                string.Equals(p.Key, currentKey, StringComparison.OrdinalIgnoreCase))
                ?? Current.SupportedProviders.FirstOrDefault(p =>
                    string.Equals(p.Key, "openrouter", StringComparison.OrdinalIgnoreCase))
                ?? Current.SupportedProviders.First();
            ApplyPreset(defaultOption.Key, preserveApiKey: true);
        }
        Notify();
    }

    public void ApplyPreset(string providerKey, bool preserveApiKey = false)
    {
        Provider = providerKey;
        var option = Current?.SupportedProviders.FirstOrDefault(p => string.Equals(p.Key, providerKey, StringComparison.OrdinalIgnoreCase));
        if (option is null)
        {
            Notify();
            return;
        }

        Model = IsOpenRouter ? option.DefaultModel : option.DefaultModel;
        BaseUrl = option.DefaultBaseUrl;
        if (!preserveApiKey || !option.RequiresApiKey)
            ApiKey = option.RequiresApiKey ? ApiKey : string.Empty;
        Notify();
    }

    public async Task TestAsync()
    {
        Busy = true;
        Notify();
        try
        {
            var (test, error) = await _api.PostWithErrorAsync<LlmTestResponse>(
                "api/admin/llm/providers/test",
                new
                {
                    provider = BuildDto().Provider,
                    model = BuildDto().Model,
                    base_url = BuildDto().BaseUrl,
                    api_key = BuildDto().ApiKey,
                    enabled = BuildDto().IsEnabled,
                });
            Result = error is not null
                ? JsonSerializer.SerializeToElement(new { status = "error", message = error })
                : test is null
                    ? JsonSerializer.SerializeToElement(new { status = "error", message = "No response from LLM test endpoint." })
                    : JsonSerializer.SerializeToElement(test);
        }
        catch (Exception ex)
        {
            Result = JsonSerializer.SerializeToElement(new
            {
                status = "error",
                message = ex.Message
            });
        }
        finally
        {
            Busy = false;
            Notify();
        }
    }

    public async Task ApplyAsync()
    {
        Busy = true;
        Notify();
        try
        {
            var (applied, error) = await _api.PostWithErrorAsync<JsonElement?>("api/admin/llm/providers", BuildDto());
            if (error is not null)
            {
                Result = JsonSerializer.SerializeToElement(new { status = "error", message = error });
            }
            else if (applied.HasValue)
            {
                Result = applied;
                await LoadAsync();
            }
            else
            {
                Result = JsonSerializer.SerializeToElement(new { status = "error", message = "No response from LLM save endpoint." });
            }
        }
        catch (Exception ex)
        {
            Result = JsonSerializer.SerializeToElement(new
            {
                status = "error",
                message = ex.Message
            });
        }
        finally
        {
            Busy = false;
            Notify();
        }
    }

    private UpsertLlmProviderDto BuildDto() => new()
    {
        Provider = Provider,
        Model = IsOpenRouter
            ? SelectedProviderOption?.DefaultModel ?? Model
            : Model,
        BaseUrl = string.IsNullOrWhiteSpace(BaseUrl) ? null : BaseUrl.Trim(),
        ApiKey = string.IsNullOrWhiteSpace(ApiKey) ? string.Empty : ApiKey.Trim(),
        IsEnabled = IsEnabled,
        IsDefault = IsDefault,
    };

    private void Notify() => OnChanged?.Invoke();
}
