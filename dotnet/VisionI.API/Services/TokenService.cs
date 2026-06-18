using System.IdentityModel.Tokens.Jwt;
using System.Security.Claims;
using System.Security.Cryptography;
using System.Text;
using Microsoft.IdentityModel.Tokens;
using VisionI.API.Models.Entities;

namespace VisionI.API.Services;

public interface ITokenService
{
    string GenerateAccessToken(AppUser user, string role);
    string GenerateRefreshToken();
    ClaimsPrincipal? ValidateAccessToken(string token);
}

public class TokenService : ITokenService
{
    private readonly IConfiguration _config;
    private readonly ILogger<TokenService> _log;

    public TokenService(IConfiguration config, ILogger<TokenService> log)
    {
        _config = config;
        _log = log;
    }

    public string GenerateAccessToken(AppUser user, string role)
    {
        var secret = _config["Jwt:Key"] ?? throw new InvalidOperationException("Jwt:Key not configured");
        var key = new SymmetricSecurityKey(Encoding.UTF8.GetBytes(secret));
        var creds = new SigningCredentials(key, SecurityAlgorithms.HmacSha256);
        var expiry = int.Parse(_config["Jwt:AccessTokenExpiryMinutes"] ?? "15");

        var claims = new[]
        {
            new Claim(JwtRegisteredClaimNames.Sub,   user.Id),
            new Claim(JwtRegisteredClaimNames.Email, user.Email ?? ""),
            new Claim(JwtRegisteredClaimNames.Jti,   Guid.NewGuid().ToString()),
            new Claim(ClaimTypes.Role,               role),
            new Claim("display_name",                user.DisplayName),
        };

        var token = new JwtSecurityToken(
            issuer: _config["Jwt:Issuer"],
            audience: _config["Jwt:Audience"],
            claims: claims,
            expires: DateTime.UtcNow.AddMinutes(expiry),
            signingCredentials: creds
        );

        return new JwtSecurityTokenHandler().WriteToken(token);
    }

    public string GenerateRefreshToken()
    {
        var bytes = RandomNumberGenerator.GetBytes(64);
        return Convert.ToHexString(bytes).ToLower();
    }

    public ClaimsPrincipal? ValidateAccessToken(string token)
    {
        var secret = _config["Jwt:Key"] ?? "";
        var key = new SymmetricSecurityKey(Encoding.UTF8.GetBytes(secret));

        try
        {
            var principal = new JwtSecurityTokenHandler().ValidateToken(token, new TokenValidationParameters
            {
                ValidateIssuerSigningKey = true,
                IssuerSigningKey = key,
                ValidateIssuer = true,
                ValidIssuer = _config["Jwt:Issuer"],
                ValidateAudience = true,
                ValidAudience = _config["Jwt:Audience"],
                ValidateLifetime = false,   // allow expired — we're just reading claims
            }, out _);

            return principal;
        }
        catch (Exception ex)
        {
            _log.LogDebug("Token validation failed: {Error}", ex.Message);
            return null;
        }
    }
}
