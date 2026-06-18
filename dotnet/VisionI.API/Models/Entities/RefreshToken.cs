namespace VisionI.API.Models.Entities;

/// <summary>
/// Persisted refresh token. One row per issued token.
/// Revoked tokens remain in the table (for audit), just with RevokedAt set.
/// </summary>
public class RefreshToken
{
    public int Id { get; set; }
    public string UserId { get; set; } = string.Empty;
    public string Token { get; set; } = string.Empty;   // random 64-byte hex
    public DateTime ExpiresAt { get; set; }
    public DateTime CreatedAt { get; set; } = DateTime.UtcNow;
    public DateTime? RevokedAt { get; set; }

    public bool IsActive => RevokedAt == null && DateTime.UtcNow < ExpiresAt;

    // Navigation
    public AppUser User { get; set; } = null!;
}