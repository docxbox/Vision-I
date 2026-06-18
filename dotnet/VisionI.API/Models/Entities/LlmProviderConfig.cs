namespace VisionI.API.Models.Entities;

/// <summary>
/// Encrypted runtime LLM provider configuration managed from the admin UI.
/// Secrets are encrypted before storage and only decrypted when syncing the
/// active provider to the Python intelligence runtime.
/// </summary>
public class LlmProviderConfig
{
    public int Id { get; set; }
    public string Provider { get; set; } = string.Empty;
    public string Model { get; set; } = string.Empty;
    public string? BaseUrl { get; set; }
    public string EncryptedApiKey { get; set; } = string.Empty;
    public bool IsEnabled { get; set; } = true;
    public bool IsDefault { get; set; } = false;
    public string UpdatedByUserId { get; set; } = string.Empty;
    public DateTime UpdatedAt { get; set; } = DateTime.UtcNow;
    public DateTime? LastTestedAt { get; set; }
    public bool? LastTestSucceeded { get; set; }
    public string? LastTestMessage { get; set; }
}
