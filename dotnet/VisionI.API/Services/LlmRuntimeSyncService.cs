using Microsoft.EntityFrameworkCore;
using VisionI.API.Infrastructure;

namespace VisionI.API.Services;

/// <summary>
/// Rehydrates the Python LLM runtime from the encrypted admin config after API restarts.
/// </summary>
public sealed class LlmRuntimeSyncService : BackgroundService
{
    private const string OpenRouterFreeModelChain =
        "openai/gpt-oss-20b:free,openai/gpt-oss-120b:free,qwen/qwen3-next-80b-a3b-instruct:free,deepseek/deepseek-v4-flash:free,meta-llama/llama-3.3-70b-instruct:free";

    private readonly IServiceScopeFactory _scopeFactory;
    private readonly ILogger<LlmRuntimeSyncService> _log;

    public LlmRuntimeSyncService(IServiceScopeFactory scopeFactory, ILogger<LlmRuntimeSyncService> log)
    {
        _scopeFactory = scopeFactory;
        _log = log;
    }

    protected override async Task ExecuteAsync(CancellationToken stoppingToken)
    {
        await Task.Delay(TimeSpan.FromSeconds(8), stoppingToken);

        for (var attempt = 1; attempt <= 5 && !stoppingToken.IsCancellationRequested; attempt++)
        {
            try
            {
                using var scope = _scopeFactory.CreateScope();
                var db = scope.ServiceProvider.GetRequiredService<AppDbContext>();
                var crypto = scope.ServiceProvider.GetRequiredService<LlmConfigCryptoService>();
                var intelligence = scope.ServiceProvider.GetRequiredService<IIntelligenceService>();
                var envOpenRouterKey = Environment.GetEnvironmentVariable("OPENROUTER_API_KEY");

                if (!string.IsNullOrWhiteSpace(envOpenRouterKey))
                {
                    var envSyncResult = await intelligence.PostPythonDocumentAsync("/admin/llm/runtime", new
                    {
                        provider = "openrouter",
                        model = OpenRouterFreeModelChain,
                        base_url = "https://openrouter.ai/api",
                        api_key = envOpenRouterKey.Trim(),
                        enabled = true,
                    }, stoppingToken);

                    if (envSyncResult is null)
                        throw new InvalidOperationException("Python runtime returned no env LLM sync response.");

                    _log.LogInformation("LLM runtime synced from environment provider=openrouter.");
                    return;
                }

                var config = await db.LlmProviderConfigs
                    .Where(x => x.IsEnabled)
                    .OrderByDescending(x => x.IsDefault)
                    .ThenByDescending(x => x.UpdatedAt)
                    .FirstOrDefaultAsync(stoppingToken);

                if (config is null)
                {
                    _log.LogInformation("LLM runtime sync skipped: no enabled admin LLM config.");
                    return;
                }

                var apiKey = crypto.Decrypt(config.EncryptedApiKey);
                if (!string.Equals(config.Provider, "ollama", StringComparison.OrdinalIgnoreCase)
                    && string.IsNullOrWhiteSpace(apiKey))
                {
                    _log.LogWarning("LLM runtime sync skipped: provider {Provider} has no stored API key.", config.Provider);
                    return;
                }

                var model = string.Equals(config.Provider, "openrouter", StringComparison.OrdinalIgnoreCase)
                    ? OpenRouterFreeModelChain
                    : config.Model;
                var baseUrl = string.Equals(config.Provider, "openrouter", StringComparison.OrdinalIgnoreCase)
                    ? "https://openrouter.ai/api"
                    : config.BaseUrl;

                var result = await intelligence.PostPythonDocumentAsync("/admin/llm/runtime", new
                {
                    provider = config.Provider,
                    model,
                    base_url = baseUrl,
                    api_key = apiKey,
                    enabled = config.IsEnabled,
                }, stoppingToken);

                if (result is null)
                    throw new InvalidOperationException("Python runtime returned no LLM sync response.");

                _log.LogInformation("LLM runtime synced to Python provider={Provider}.", config.Provider);
                return;
            }
            catch (OperationCanceledException) when (stoppingToken.IsCancellationRequested)
            {
                return;
            }
            catch (Exception ex)
            {
                _log.LogWarning(ex, "LLM runtime sync attempt {Attempt}/5 failed.", attempt);
                await Task.Delay(TimeSpan.FromSeconds(5 * attempt), stoppingToken);
            }
        }
    }
}
