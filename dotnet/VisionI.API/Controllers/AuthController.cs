using Microsoft.AspNetCore.Authorization;
using Microsoft.AspNetCore.Identity;
using Microsoft.AspNetCore.Identity.Data;
using Microsoft.AspNetCore.Mvc;
using Microsoft.EntityFrameworkCore;
using System.Security.Cryptography;
using System.Security.Claims;
using VisionI.API.Infrastructure;
using VisionI.API.Models.Entities;
using VisionI.API.Models.Requests;
using VisionI.API.Services;
using VisionI.API.Models.Responses;

namespace VisionI.API.Controllers;

[ApiController]
[Route("api/auth")]
[Produces("application/json")]
public class AuthController : ControllerBase
{
    private readonly UserManager<AppUser> _users;
    private readonly RoleManager<IdentityRole> _roles;
    private readonly ITokenService _tokens;
    private readonly AppDbContext _db;
    private readonly IConfiguration _config;
    private readonly IWebHostEnvironment _env;
    private readonly ILogger<AuthController> _log;

    public AuthController(
        UserManager<AppUser> users,
        RoleManager<IdentityRole> roles,
        ITokenService tokens,
        AppDbContext db,
        IConfiguration config,
        IWebHostEnvironment env,
        ILogger<AuthController> log)
    {
        _users = users;
        _roles = roles;
        _tokens = tokens;
        _db = db;
        _config = config;
        _env = env;
        _log = log;
    }

    /// <summary>Register a new user account. New accounts default to the Viewer role.</summary>
    [HttpPost("register")]
    [AllowAnonymous]
    [ProducesResponseType(typeof(AuthResponse), 201)]
    [ProducesResponseType(typeof(ApiError), 400)]
    public async Task<IActionResult> Register([FromBody] Models.Requests.RegisterRequest req)
    {
        if (await _users.FindByEmailAsync(req.Email) != null)
            return BadRequest(new ApiError("EMAIL_TAKEN", "An account with this email already exists."));

        var user = new AppUser
        {
            UserName = req.Email,
            Email = req.Email,
            DisplayName = req.DisplayName,
        };

        var result = await _users.CreateAsync(user, req.Password);
        if (!result.Succeeded)
        {
            var errors = string.Join("; ", result.Errors.Select(e => e.Description));
            return BadRequest(new ApiError("REGISTRATION_FAILED", errors));
        }

        // Ensure roles exist
        foreach (var role in new[] { "Viewer", "Analyst", "Admin" })
            if (!await _roles.RoleExistsAsync(role))
                await _roles.CreateAsync(new IdentityRole(role));

        // Least privilege: self-registered accounts start as Viewer (read-only). An admin
        // promotes to Analyst/Admin. (Was Analyst — a privilege-escalation gap.)
        await _users.AddToRoleAsync(user, "Viewer");

        _log.LogInformation("New user registered: {Email}", user.Email);

        var (accessToken, refreshToken) = await IssueTokensAsync(user, "Viewer");
        SetRefreshCookie(refreshToken);

        return StatusCode(201, BuildAuthResponse(user, accessToken, "Viewer"));
    }

    /// <summary>Login with email and password. Returns JWT access token + sets HttpOnly refresh cookie.</summary>
    [HttpPost("login")]
    [AllowAnonymous]
    [ProducesResponseType(typeof(AuthResponse), 200)]
    [ProducesResponseType(typeof(ApiError), 401)]
    public async Task<IActionResult> Login([FromBody] Models.Requests.LoginRequest req)
    {
        var user = await _users.FindByEmailAsync(req.Email);
        if (user == null || !user.IsActive)
            return Unauthorized(new ApiError("INVALID_CREDENTIALS", "Invalid email or password."));

        if (!await _users.CheckPasswordAsync(user, req.Password))
        {
            await _users.AccessFailedAsync(user);

            _db.AuditLogs.Add(new AuditLog {
                UserId = user.Id,
                Action = "auth.login_failed",
                Resource = $"user:{user.Id}",
                IpAddress = HttpContext.Connection.RemoteIpAddress?.ToString(),
            });
            await _db.SaveChangesAsync();

            return Unauthorized(new ApiError("INVALID_CREDENTIALS", "Invalid email or password."));
        }

        await _users.ResetAccessFailedCountAsync(user);

        var roles = await _users.GetRolesAsync(user);
        var role = PickRole(roles);

        var (accessToken, refreshToken) = await IssueTokensAsync(user, role);
        SetRefreshCookie(refreshToken);

        _log.LogInformation("User logged in: {Email} ({Role})", user.Email, role);
        return Ok(BuildAuthResponse(user, accessToken, role));
    }

    /// <summary>Exchange a valid refresh token cookie for a new access token. No body required.</summary>
    [HttpPost("refresh")]
    [AllowAnonymous]
    [ProducesResponseType(typeof(AuthResponse), 200)]
    [ProducesResponseType(typeof(ApiError), 401)]
    public async Task<IActionResult> Refresh()
    {
        var tokenValue = Request.Cookies["vision_refresh"];
        if (string.IsNullOrEmpty(tokenValue))
            return Unauthorized(new ApiError("NO_REFRESH_TOKEN", "Refresh token cookie not found."));

        var stored = await _db.RefreshTokens
            .Include(t => t.User)
            .FirstOrDefaultAsync(t => t.Token == tokenValue);

        if (stored == null || !stored.IsActive)
            return Unauthorized(new ApiError("INVALID_REFRESH_TOKEN", "Refresh token is invalid or expired."));

        // Revoke old token
        stored.RevokedAt = DateTime.UtcNow;

        var roles = await _users.GetRolesAsync(stored.User);
        var role = PickRole(roles);

        var (accessToken, newRefreshToken) = await IssueTokensAsync(stored.User, role);
        await _db.SaveChangesAsync();

        SetRefreshCookie(newRefreshToken);
        _log.LogInformation("Token refreshed for: {Email}", stored.User.Email);

        return Ok(BuildAuthResponse(stored.User, accessToken, role));
    }

    /// <summary>Revoke the current refresh token and clear the cookie.</summary>
    [HttpPost("logout")]
    [Authorize]
    public async Task<IActionResult> Logout()
    {
        var tokenValue = Request.Cookies["vision_refresh"];
        if (!string.IsNullOrEmpty(tokenValue))
        {
            var stored = await _db.RefreshTokens
                .FirstOrDefaultAsync(t => t.Token == tokenValue);
            if (stored != null)
            {
                stored.RevokedAt = DateTime.UtcNow;
                await _db.SaveChangesAsync();
            }
        }

        Response.Cookies.Delete("vision_refresh");
        return Ok(new { message = "Logged out successfully." });
    }

    /// <summary>Return current user profile from JWT claims.</summary>
    [HttpGet("me")]
    [Authorize]
    [ProducesResponseType(typeof(UserResponse), 200)]
    public async Task<IActionResult> Me()
    {
        var userId = User.FindFirstValue(ClaimTypes.NameIdentifier)
                  ?? User.FindFirstValue(JwtRegisteredClaimNames_Sub);

        if (string.IsNullOrEmpty(userId))
            return Unauthorized();

        var user = await _users.FindByIdAsync(userId);
        if (user == null) return Unauthorized();

        var roles = await _users.GetRolesAsync(user);
        var role = PickRole(roles);

        return Ok(new
        {
            id           = user.Id,
            email        = user.Email ?? "",
            display_name = user.DisplayName,
            role,
            is_active    = user.IsActive,
        });
    }

    /// <summary>Update display name for the authenticated user.</summary>
    [HttpPost("profile")]
    [Authorize]
    [ProducesResponseType(200)]
    [ProducesResponseType(typeof(ApiError), 400)]
    public async Task<IActionResult> UpdateProfile([FromBody] Models.Requests.UpdateProfileRequest req)
    {
        var userId = User.FindFirstValue(ClaimTypes.NameIdentifier)
                  ?? User.FindFirstValue(JwtRegisteredClaimNames_Sub);
        var user = userId != null ? await _users.FindByIdAsync(userId) : null;
        if (user == null) return Unauthorized();

        user.DisplayName = req.DisplayName.Trim();
        var result = await _users.UpdateAsync(user);
        if (!result.Succeeded)
            return BadRequest(new ApiError("UPDATE_FAILED", string.Join("; ", result.Errors.Select(e => e.Description))));

        _log.LogInformation("Profile updated for {Email}", user.Email);
        return Ok(new { id = user.Id, email = user.Email ?? "", display_name = user.DisplayName });
    }

    /// <summary>Change password for the authenticated user (requires current password).</summary>
    [HttpPost("change-password")]
    [Authorize]
    [ProducesResponseType(200)]
    [ProducesResponseType(typeof(ApiError), 400)]
    public async Task<IActionResult> ChangePassword([FromBody] Models.Requests.ChangePasswordRequest req)
    {
        var userId = User.FindFirstValue(ClaimTypes.NameIdentifier)
                  ?? User.FindFirstValue(JwtRegisteredClaimNames_Sub);
        var user = userId != null ? await _users.FindByIdAsync(userId) : null;
        if (user == null) return Unauthorized();

        var result = await _users.ChangePasswordAsync(user, req.CurrentPassword, req.NewPassword);
        if (!result.Succeeded)
            return BadRequest(new ApiError("PASSWORD_CHANGE_FAILED", string.Join("; ", result.Errors.Select(e => e.Description))));

        _log.LogInformation("Password changed for {Email}", user.Email);
        return Ok(new { message = "Password changed successfully." });
    }

    // ── 2FA / TOTP ─────────────────────────────────────────────────────────────

    /// <summary>Generate a TOTP secret and return the otpauth:// URI for QR code display.</summary>
    [HttpPost("2fa/setup")]
    [Authorize]
    public async Task<IActionResult> TotpSetup()
    {
        var userId = User.FindFirstValue(ClaimTypes.NameIdentifier)
                  ?? User.FindFirstValue(JwtRegisteredClaimNames_Sub);
        var user = userId != null ? await _users.FindByIdAsync(userId) : null;
        if (user == null) return Unauthorized();

        // Generate 20-byte random secret
        var secretBytes  = RandomNumberGenerator.GetBytes(20);
        var base32Secret = TotpHelper.ToBase32(secretBytes);

        user.TotpSecret  = base32Secret;
        user.TotpEnabled = false; // enabled only after first successful verify
        await _users.UpdateAsync(user);

        var issuer   = "Vision-I";
        var account  = Uri.EscapeDataString(user.Email ?? user.UserName ?? "user");
        var otpauth  = $"otpauth://totp/{issuer}:{account}?secret={base32Secret}&issuer={issuer}&algorithm=SHA1&digits=6&period=30";

        return Ok(new { secret = base32Secret, otpauth_uri = otpauth, enabled = false });
    }

    /// <summary>Verify a TOTP code to enable 2FA (setup flow) or confirm login (auth flow).</summary>
    [HttpPost("2fa/verify")]
    [Authorize]
    public async Task<IActionResult> TotpVerify([FromBody] TotpVerifyRequest req)
    {
        var userId = User.FindFirstValue(ClaimTypes.NameIdentifier)
                  ?? User.FindFirstValue(JwtRegisteredClaimNames_Sub);
        var user = userId != null ? await _users.FindByIdAsync(userId) : null;
        if (user == null) return Unauthorized();

        if (string.IsNullOrEmpty(user.TotpSecret))
            return BadRequest(new ApiError("TOTP_NOT_CONFIGURED", "2FA has not been set up for this account."));

        var valid = TotpHelper.Verify(user.TotpSecret, req.Code?.Trim() ?? "");

        if (!valid)
            return BadRequest(new ApiError("INVALID_TOTP", "Invalid or expired 2FA code."));

        if (!user.TotpEnabled)
        {
            user.TotpEnabled = true;
            await _users.UpdateAsync(user);
        }

        return Ok(new { verified = true, totp_enabled = user.TotpEnabled });
    }

    /// <summary>Disable 2FA for the authenticated user (requires current TOTP code).</summary>
    [HttpPost("2fa/disable")]
    [Authorize]
    public async Task<IActionResult> TotpDisable([FromBody] TotpVerifyRequest req)
    {
        var userId = User.FindFirstValue(ClaimTypes.NameIdentifier)
                  ?? User.FindFirstValue(JwtRegisteredClaimNames_Sub);
        var user = userId != null ? await _users.FindByIdAsync(userId) : null;
        if (user == null) return Unauthorized();

        if (string.IsNullOrEmpty(user.TotpSecret) || !user.TotpEnabled)
            return BadRequest(new ApiError("TOTP_NOT_ENABLED", "2FA is not enabled for this account."));

        var valid = TotpHelper.Verify(user.TotpSecret, req.Code?.Trim() ?? "");

        if (!valid)
            return BadRequest(new ApiError("INVALID_TOTP", "Invalid or expired 2FA code."));

        user.TotpSecret  = null;
        user.TotpEnabled = false;
        await _users.UpdateAsync(user);

        return Ok(new { disabled = true });
    }

    private async Task<(string AccessToken, string RefreshToken)> IssueTokensAsync(
        AppUser user, string role)
    {
        var accessToken = _tokens.GenerateAccessToken(user, role);
        var refreshToken = _tokens.GenerateRefreshToken();
        var expiryDays = int.Parse(_config["Jwt:RefreshTokenExpiryDays"] ?? "7");

        _db.RefreshTokens.Add(new RefreshToken
        {
            UserId = user.Id,
            Token = refreshToken,
            ExpiresAt = DateTime.UtcNow.AddDays(expiryDays),
        });
        await _db.SaveChangesAsync();

        return (accessToken, refreshToken);
    }

    private void SetRefreshCookie(string token)
    {
        var expiryDays = int.Parse(_config["Jwt:RefreshTokenExpiryDays"] ?? "7");
        Response.Cookies.Append("vision_refresh", token, new CookieOptions
        {
            HttpOnly = true,
            Secure   = Request.IsHttps,   // Only require Secure on HTTPS — allows HTTP dev/proxy setups
            SameSite = SameSiteMode.Strict,
            Expires = DateTimeOffset.UtcNow.AddDays(expiryDays),
            Path = "/api/auth",
        });
    }

    private static object BuildAuthResponse(AppUser user, string accessToken, string role)
    {
        var handler = new System.IdentityModel.Tokens.Jwt.JwtSecurityTokenHandler();
        var jwt = handler.ReadJwtToken(accessToken);
        var seconds = (int)(jwt.ValidTo - DateTime.UtcNow).TotalSeconds;

        // Shape matches LoginResponseDto in VisionI.Web:
        //   { token, expires_in, user: { id, email, display_name, role, is_active } }
        return new
        {
            token      = accessToken,
            token_type = "Bearer",
            expires_in = seconds,
            user       = new
            {
                id           = user.Id,
                email        = user.Email ?? "",
                display_name = user.DisplayName,
                role,
                is_active    = user.IsActive,
            },
        };
    }

    private static string PickRole(IList<string> roles) =>
        roles.Contains("Admin")   ? "Admin"   :
        roles.Contains("Analyst") ? "Analyst" :
        roles.Contains("Viewer")  ? "Viewer"  : "Viewer";

    // Workaround: JwtRegisteredClaimNames.Sub is a const string
    private const string JwtRegisteredClaimNames_Sub = "sub";
}

/// <summary>
/// RFC 6238 TOTP implementation using only .NET BCL (System.Security.Cryptography).
/// Supports a ±1 step verification window to tolerate clock drift.
/// </summary>
internal static class TotpHelper
{
    private const string _b32Chars = "ABCDEFGHIJKLMNOPQRSTUVWXYZ234567";

    public static string ToBase32(byte[] data)
    {
        var sb = new System.Text.StringBuilder();
        // Use long to avoid int overflow when buffer is shifted left by 8 repeatedly.
        long buffer = data[0];
        int bitsLeft = 8, i = 1;
        while (bitsLeft > 0 || i < data.Length)
        {
            if (bitsLeft < 5)
            {
                if (i < data.Length) { buffer = (buffer << 8) | data[i++]; bitsLeft += 8; }
                else { buffer <<= 5 - bitsLeft; bitsLeft = 5; }
            }
            bitsLeft -= 5;
            sb.Append(_b32Chars[(int)((buffer >> bitsLeft) & 31L)]);
        }
        return sb.ToString();
    }

    public static byte[] FromBase32(string s)
    {
        s = s.TrimEnd('=').ToUpperInvariant();
        var bits  = new System.Text.StringBuilder();
        foreach (var c in s) bits.Append(Convert.ToString(_b32Chars.IndexOf(c), 2).PadLeft(5, '0'));
        var str   = bits.ToString();
        var bytes = new byte[str.Length / 8];
        for (int i = 0; i < bytes.Length; i++)
            bytes[i] = Convert.ToByte(str.Substring(i * 8, 8), 2);
        return bytes;
    }

    private static string ComputeCode(byte[] key, long counter)
    {
        var msg = BitConverter.GetBytes(counter);
        if (BitConverter.IsLittleEndian) Array.Reverse(msg);
        using var hmac = new HMACSHA1(key);
        var hash   = hmac.ComputeHash(msg);
        int offset = hash[^1] & 0x0F;
        int code   = ((hash[offset] & 0x7F) << 24)
                   | ((hash[offset + 1] & 0xFF) << 16)
                   | ((hash[offset + 2] & 0xFF) << 8)
                   |  (hash[offset + 3] & 0xFF);
        return (code % 1_000_000).ToString("D6");
    }

    public static bool Verify(string base32Secret, string userCode)
    {
        if (string.IsNullOrEmpty(base32Secret) || string.IsNullOrEmpty(userCode)) return false;
        try
        {
            var key     = FromBase32(base32Secret);
            long step   = DateTimeOffset.UtcNow.ToUnixTimeSeconds() / 30;
            // Allow ±1 step window for clock drift
            for (long s = step - 1; s <= step + 1; s++)
                if (ComputeCode(key, s) == userCode.Trim()) return true;
        }
        catch { /* malformed secret */ }
        return false;
    }
}
