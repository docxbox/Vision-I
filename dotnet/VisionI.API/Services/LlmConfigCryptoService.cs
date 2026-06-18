using Microsoft.AspNetCore.DataProtection;

namespace VisionI.API.Services;

/// <summary>
/// Small wrapper around ASP.NET Data Protection for encrypting LLM secrets at rest.
/// </summary>
public class LlmConfigCryptoService
{
    private readonly IDataProtector _protector;

    public LlmConfigCryptoService(IDataProtectionProvider provider)
    {
        _protector = provider.CreateProtector("VisionI.API.LlmProviderConfig.v1");
    }

    public string Encrypt(string plaintext) => _protector.Protect(plaintext);

    public string Decrypt(string ciphertext) => _protector.Unprotect(ciphertext);

    public static string Mask(string? value)
    {
        if (string.IsNullOrWhiteSpace(value))
            return string.Empty;
        if (value.Length <= 8)
            return new string('*', value.Length);
        return $"{value[..4]}...{value[^4..]}";
    }
}
