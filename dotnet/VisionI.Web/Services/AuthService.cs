using System.Text.Json;
using System.Text.Json.Serialization;
using Microsoft.JSInterop;
using VisionI.Web.Models;

namespace VisionI.Web.Services;

/// <summary>
/// Authentication service for Blazor Server.
/// Access tokens stay in memory only; refresh cookie stays in the browser.
/// Browser-owned auth calls are performed through JS fetch so cookies persist
/// across reconnects and reloads.
/// </summary>
public class AuthService
{
    private readonly string _publicApiBase;
    private readonly IJSRuntime _js;
    private readonly ILogger<AuthService> _log;
    private UserDto? _currentUser;

    public string AccessToken { get; private set; } = "";
    public bool IsAuthenticated => !string.IsNullOrEmpty(AccessToken) && _currentUser != null;
    public UserDto? CurrentUser => _currentUser;

    private static readonly JsonSerializerOptions _json = new()
    {
        PropertyNameCaseInsensitive = true,
        DefaultIgnoreCondition = JsonIgnoreCondition.WhenWritingNull,
        PropertyNamingPolicy = JsonNamingPolicy.CamelCase,
    };

    public AuthService(IJSRuntime js, IConfiguration config, ILogger<AuthService> log)
    {
        _publicApiBase = (
            config["PublicApiBaseUrl"]
            ?? config["ApiBaseUrl"]
            ?? ""
        ).TrimEnd('/');
        _js = js;
        _log = log;
    }

    public async Task InitializeAsync(CancellationToken ct = default)
    {
        await TryRefreshAsync(ct);
    }

    public async Task<(bool Ok, string? Error)> LoginAsync(LoginDto dto, CancellationToken ct = default)
    {
        try
        {
            var resp = await SendBrowserRequestAsync("api/auth/login", dto, ct);
            if (resp?.Ok == true)
            {
                var result = Deserialize<LoginResponseDto>(resp.Body);
                if (result?.User != null && !string.IsNullOrEmpty(result.Token))
                {
                    AccessToken = result.Token;
                    _currentUser = result.User;
                    _log.LogInformation("User {Id} authenticated", result.User.Id);
                    return (true, null);
                }

                _log.LogWarning("Login response missing token or user");
                return (false, "Login failed. Please try again.");
            }

            var err = Deserialize<ApiErrorDto>(resp?.Body);
            return (false, err?.Message ?? "Invalid email or password.");
        }
        catch (JSException ex)
        {
            _log.LogWarning("Login JS error: {Error}", ex.Message);
            return (false, "Cannot reach the server. Is the API running?");
        }
        catch (Exception ex)
        {
            _log.LogWarning("Login exception: {Error}", ex.Message);
            return (false, "Unexpected error. Please try again.");
        }
    }

    public async Task<string?> RegisterAsync(RegisterDto dto, CancellationToken ct = default)
    {
        try
        {
            var resp = await SendBrowserRequestAsync("api/auth/register", dto, ct);
            if (resp?.Ok == true) return null;

            var err = Deserialize<ApiErrorDto>(resp?.Body);
            if (!string.IsNullOrEmpty(err?.Message)) return err.Message;

            return resp?.Status == 409
                ? "An account with this email already exists."
                : $"Registration failed (HTTP {resp?.Status ?? 0}).";
        }
        catch (JSException ex)
        {
            _log.LogWarning("Registration JS error: {Error}", ex.Message);
            return "Cannot reach the server. Is the API running?";
        }
        catch (Exception ex)
        {
            _log.LogWarning("Registration exception: {Error}", ex.Message);
            return "Unexpected error. Please try again.";
        }
    }

    public async Task<bool> TryRefreshAsync(CancellationToken ct = default)
    {
        try
        {
            var resp = await SendBrowserRequestAsync("api/auth/refresh", null, ct);
            if (resp?.Ok != true) return false;

            var result = Deserialize<LoginResponseDto>(resp.Body);
            if (result?.User != null && !string.IsNullOrEmpty(result.Token))
            {
                AccessToken = result.Token;
                _currentUser = result.User;
                return true;
            }

            return false;
        }
        catch
        {
            return false;
        }
    }

    public async Task LogoutAsync(CancellationToken ct = default)
    {
        try
        {
            await SendBrowserRequestAsync("api/auth/logout", null, ct, AccessToken);
        }
        catch { }
        finally
        {
            AccessToken = "";
            _currentUser = null;
            _log.LogInformation("User signed out");
        }
    }

    private async Task<BrowserHttpResult?> SendBrowserRequestAsync(
        string path,
        object? body,
        CancellationToken ct,
        string? bearerToken = null)
    {
        return await _js.InvokeAsync<BrowserHttpResult>(
            "visionAuth.send",
            ct,
            BuildUrl(path),
            body,
            bearerToken);
    }

    private string BuildUrl(string path)
    {
        if (Uri.TryCreate(path, UriKind.Absolute, out _)) return path;
        return string.IsNullOrEmpty(_publicApiBase)
            ? path
            : $"{_publicApiBase}/{path.TrimStart('/')}";
    }

    private static T? Deserialize<T>(string? body)
    {
        if (string.IsNullOrWhiteSpace(body)) return default;
        try
        {
            return JsonSerializer.Deserialize<T>(body, _json);
        }
        catch
        {
            return default;
        }
    }

    private sealed class ApiErrorDto
    {
        [JsonPropertyName("message")]
        public string? Message { get; set; }
    }

    private sealed class BrowserHttpResult
    {
        [JsonPropertyName("ok")]
        public bool Ok { get; set; }

        [JsonPropertyName("status")]
        public int Status { get; set; }

        [JsonPropertyName("body")]
        public string? Body { get; set; }
    }
}
