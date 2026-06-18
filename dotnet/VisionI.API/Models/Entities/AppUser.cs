using Microsoft.AspNetCore.Identity;

namespace VisionI.API.Models.Entities;

/// <summary>
/// Vision-I application user.
/// Extends IdentityUser — password hashing, lockout, and claims are all handled by ASP.NET Identity.
/// </summary>
public class AppUser : IdentityUser
{
    public string DisplayName { get; set; } = string.Empty;
    public DateTime CreatedAt { get; set; } = DateTime.UtcNow;
    public bool IsActive { get; set; } = true;
    /// <summary>Base32-encoded TOTP secret. Null means 2FA not configured.</summary>
    public string? TotpSecret { get; set; }
    public bool TotpEnabled { get; set; } = false;
}