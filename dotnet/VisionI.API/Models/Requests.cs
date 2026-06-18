using System.ComponentModel.DataAnnotations;
using System.Text.Json.Serialization;

namespace VisionI.API.Models.Requests;

public class RegisterRequest
{
    [Required, EmailAddress]
    public string Email { get; set; } = "";

    [Required, MinLength(8)]
    public string Password { get; set; } = "";

    [Required, MinLength(2)]
    public string DisplayName { get; set; } = "";
}

public class LoginRequest
{
    [Required, EmailAddress]
    public string Email { get; set; } = "";

    [Required]
    public string Password { get; set; } = "";
}

public record IngestRequest(
    string Query = "world news",
    int Limit = 10,
    bool Enrich = false,
    string[]? Sources = null
);

public record AddQueryRequest(
    [Required, MinLength(2)] string Query
);

public record AssignRoleRequest(
    [Required] string Role   // Viewer | Analyst | Admin
);

public record CustomStocksRequest(
    [Required] string Tickers,   // comma-separated: AAPL,TSLA
    int Limit = 10
);

public record ResetPasswordRequest(
    [Required, MinLength(8)] string NewPassword
);

public class UpsertLlmProviderRequest
{
    [Required]
    [JsonPropertyName("provider")]
    public string Provider { get; set; } = "";

    [Required]
    [JsonPropertyName("model")]
    public string Model { get; set; } = "";

    [JsonPropertyName("base_url")]
    public string? BaseUrl { get; set; }

    [JsonPropertyName("api_key")]
    public string? ApiKey { get; set; }

    [JsonPropertyName("is_enabled")]
    public bool IsEnabled { get; set; } = true;

    [JsonPropertyName("is_default")]
    public bool IsDefault { get; set; } = true;
}

public class TestLlmProviderRequest
{
    [Required]
    [JsonPropertyName("provider")]
    public string Provider { get; set; } = "";

    [Required]
    [JsonPropertyName("model")]
    public string Model { get; set; } = "";

    [JsonPropertyName("base_url")]
    public string? BaseUrl { get; set; }

    [JsonPropertyName("api_key")]
    public string? ApiKey { get; set; }

    [JsonPropertyName("enabled")]
    public bool Enabled { get; set; } = true;
}

public record UpsertTriageRequest(
    [Required] string EventId,
    [Required] string Title,
    string? Source,
    string? EventType,
    double? RiskScore,
    double? ConfidenceScore,
    string Status = "new",
    string Priority = "medium",
    string? Note = null,
    string? SourceUrl = null,
    string? Region = null,
    int SimilarEventCount = 0,
    int RelatedActorCount = 0
);

public record CopilotAskRequest(
    [Required, MinLength(2)] string Question,
    string? EventId = null,
    string? ActorId = null,
    string? NarrativeId = null,
    string? Context = null,
    object[]? History = null,
    string Analyst = "analyst"
);

public class TotpVerifyRequest
{
    [Required, StringLength(8, MinimumLength = 6)]
    public string? Code { get; set; }
}

public record AddWorkspaceQueryRequest(
    [Required, MinLength(1), MaxLength(512)] string Query
);

public record UpdateProfileRequest(
    [Required, MinLength(2), MaxLength(128)] string DisplayName
);

public record ChangePasswordRequest(
    [Required] string CurrentPassword,
    [Required, MinLength(8)] string NewPassword
);

public record CreateWorkspaceTaskRequest(
    [Required, MinLength(2), MaxLength(256)] string Title,
    string? Description = null,
    string Priority = "medium",
    string? AssigneeUserId = null
);

public record UpdateWorkspaceTaskRequest(
    string? Title = null,
    string? Description = null,
    string? Status = null,       // open|in_progress|done|cancelled
    string? Priority = null,
    string? AssigneeUserId = null
);

public record PinEvidenceRequest(
    [Required] string ItemType,   // event|asset|entity|signal|narrative
    [Required] string ItemId,
    string? Title = null,
    string? Source = null,
    string? Note = null
);
