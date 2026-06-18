using System.Diagnostics;
using System.Text.Json;

namespace VisionI.API.Services;

public interface INativeLlmService
{
    Task<NativeLlmRuntime> GetRuntimeAsync(CancellationToken ct = default);
    Task<NativeLlmCompletion> CompleteAsync(
        string prompt,
        string systemPrompt,
        int maxTokens = 1024,
        double temperature = 0.2,
        CancellationToken ct = default);
    Task<NativeLlmTestResult> TestAsync(
        string provider,
        string model,
        string? baseUrl,
        string? apiKey,
        bool enabled,
        CancellationToken ct = default);
}

public sealed record NativeLlmRuntime(
    string Provider,
    string Model,
    IReadOnlyList<string> Models,
    string? BaseUrl,
    bool Available,
    string RuntimeSource,
    string? Message = null);

public sealed record NativeLlmCompletion(
    bool Ok,
    string Text,
    string ModelUsed,
    int LatencyMs,
    string? Error = null);

public sealed record NativeLlmTestResult(
    bool Ok,
    string Detail,
    string ModelUsed,
    int LatencyMs);

/// <summary>
/// Thin client to the single LLM gateway in the Python tier (LLMProvider). The .NET tier
/// no longer talks to any LLM provider directly — there is ONE provider implementation and
/// ONE config (Python). All completions route through POST /admin/llm/complete.
/// </summary>
public sealed class NativeLlmService : INativeLlmService
{
    // Kept for AdminController's default-model display chips (provider catalog).
    public const string OpenRouterFreeModelChain =
        "openai/gpt-oss-20b:free,openai/gpt-oss-120b:free,qwen/qwen3-next-80b-a3b-instruct:free,deepseek/deepseek-v4-flash:free,meta-llama/llama-3.3-70b-instruct:free";
    public const string GroqModelChain = "llama-3.3-70b-versatile,llama-3.1-8b-instant,openai/gpt-oss-20b";

    private readonly IIntelligenceClient _python;
    private readonly ILogger<NativeLlmService> _log;

    public NativeLlmService(IIntelligenceClient python, ILogger<NativeLlmService> log)
    {
        _python = python;
        _log = log;
    }

    public async Task<NativeLlmRuntime> GetRuntimeAsync(CancellationToken ct = default)
    {
        try
        {
            using var doc = await _python.GetAsync("/admin/llm/runtime", ct);
            if (doc is null)
                return new NativeLlmRuntime("none", "n/a", Array.Empty<string>(), null, false, "none",
                    "LLM gateway (Python) is unavailable.");

            var r = doc.RootElement;
            var provider = Str(r, "provider") ?? "none";
            var model = Str(r, "model") ?? "n/a";
            var baseUrl = Str(r, "base_url");
            var available = r.TryGetProperty("available", out var a) && a.ValueKind == JsonValueKind.True;

            var models = new List<string>();
            if (r.TryGetProperty("models", out var ms) && ms.ValueKind == JsonValueKind.Array)
                foreach (var x in ms.EnumerateArray())
                    if (x.GetString() is { Length: > 0 } s) models.Add(s);
            if (models.Count == 0 && model != "n/a") models.Add(model);

            return new NativeLlmRuntime(provider, model, models, baseUrl, available, "python",
                available ? null : "LLM provider not configured in the Python tier.");
        }
        catch (Exception ex)
        {
            _log.LogWarning(ex, "Failed to read LLM runtime from the Python gateway");
            return new NativeLlmRuntime("none", "n/a", Array.Empty<string>(), null, false, "none", ex.Message);
        }
    }

    public async Task<NativeLlmCompletion> CompleteAsync(
        string prompt,
        string systemPrompt,
        int maxTokens = 1024,
        double temperature = 0.2,
        CancellationToken ct = default)
    {
        var sw = Stopwatch.StartNew();
        try
        {
            var body = new
            {
                prompt,
                system = systemPrompt,
                max_tokens = Math.Clamp(maxTokens, 16, 8192),
                temperature = Math.Clamp(temperature, 0, 1),
            };

            using var doc = await _python.PostAsync("/admin/llm/complete", body, ct);
            sw.Stop();
            if (doc is null)
                return new NativeLlmCompletion(false, "", "none", (int)sw.ElapsedMilliseconds,
                    "LLM gateway returned no response.");

            var root = doc.RootElement;
            var ok = root.TryGetProperty("ok", out var okEl) && okEl.ValueKind == JsonValueKind.True;
            var text = Str(root, "text") ?? "";
            var model = Str(root, "model") ?? "none";

            if (!ok || string.IsNullOrWhiteSpace(text))
            {
                var err = Str(root, "error") ?? "LLM returned an empty completion.";
                return new NativeLlmCompletion(false, "", model, (int)sw.ElapsedMilliseconds, err);
            }

            return new NativeLlmCompletion(true, text.Trim(), model, (int)sw.ElapsedMilliseconds);
        }
        catch (Exception ex)
        {
            sw.Stop();
            _log.LogWarning(ex, "LLM gateway completion failed");
            return new NativeLlmCompletion(false, "", "none", (int)sw.ElapsedMilliseconds, ex.Message);
        }
    }

    public async Task<NativeLlmTestResult> TestAsync(
        string provider,
        string model,
        string? baseUrl,
        string? apiKey,
        bool enabled,
        CancellationToken ct = default)
    {
        if (!enabled)
            return new NativeLlmTestResult(false, "Provider is disabled.", "none", 0);

        // Provider/key are owned by the Python gateway now — probe the active provider.
        var result = await CompleteAsync(
            "Respond with READY only.",
            "You are a connectivity probe. Return exactly READY.",
            16, 0, ct);

        var ok = result.Ok && result.Text.Contains("READY", StringComparison.OrdinalIgnoreCase);
        var detail = ok
            ? $"LLM gateway responded using {result.ModelUsed} in {result.LatencyMs} ms."
            : result.Error ?? "LLM gateway returned no usable completion.";
        return new NativeLlmTestResult(ok, detail, result.ModelUsed, result.LatencyMs);
    }

    private static string? Str(JsonElement el, string prop)
        => el.TryGetProperty(prop, out var p) && p.ValueKind == JsonValueKind.String ? p.GetString() : null;
}
