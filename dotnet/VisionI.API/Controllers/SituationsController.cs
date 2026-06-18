using Microsoft.AspNetCore.Authorization;
using Microsoft.AspNetCore.Mvc;
using Microsoft.AspNetCore.RateLimiting;
using System.Text.Json;
using VisionI.API.Models.Responses;
using VisionI.API.Services;

namespace VisionI.API.Controllers;

[ApiController]
[Route("api/situations")]
[Authorize]
[Produces("application/json")]
public class SituationsController : ControllerBase
{
    private readonly IIntelligenceService _intelligence;

    public SituationsController(IIntelligenceService intelligence)
    {
        _intelligence = intelligence;
    }

    [HttpGet]
    [EnableRateLimiting("query")]
    public async Task<IActionResult> ListSituations(
        [FromQuery] int limit = 24,
        [FromQuery] string? severity = null,
        [FromQuery] string? status = "active",
        CancellationToken ct = default)
    {
        var key = $"cache:situations:list:{limit}:{severity}:{status}";
        var json = await _intelligence.GetCachedJsonAsync(
            key,
            innerCt =>
            {
                var parts = new List<string> { $"limit={limit}" };
                if (!string.IsNullOrWhiteSpace(severity)) parts.Add($"severity={Uri.EscapeDataString(severity)}");
                if (!string.IsNullOrWhiteSpace(status)) parts.Add($"status={Uri.EscapeDataString(status)}");
                return _intelligence.GetPythonJsonAsync($"/situations?{string.Join("&", parts)}", innerCt);
            },
            TimeSpan.FromMinutes(2),
            ct);

        if (json == null) return StatusCode(502, "Intelligence layer unavailable.");
        var payload = _intelligence.DeserializeJson<SituationsResponse>(json);
        if (payload is null) return StatusCode(502, "Invalid situations payload.");

        var projected = payload with
        {
            Situations = payload.Situations
                .Select(situation => situation with { Indicator = BuildSituationIndicator(situation) })
                .ToList()
        };

        return Content(_intelligence.SerializeJson(projected), "application/json");
    }

    [HttpGet("{situationId}")]
    [EnableRateLimiting("query")]
    public async Task<IActionResult> GetSituation(string situationId, CancellationToken ct = default)
    {
        var payload = await _intelligence.GetPythonModelAsync<SituationRecordResponse>($"/situations/{Uri.EscapeDataString(situationId)}", ct);
        if (payload == null) return NotFound();

        var projected = payload with
        {
            Indicator = BuildSituationIndicator(payload)
        };

        return Content(_intelligence.SerializeJson(projected), "application/json");
    }

    private static AnalystIndicatorResponse BuildSituationIndicator(SituationRecordResponse situation)
    {
        var severity = string.IsNullOrWhiteSpace(situation.Severity) ? SeverityFromScore(situation.RiskScore) : situation.Severity.ToLowerInvariant();
        var sourceCount = TryGetInt(situation.Meta, "source_count");
        var avgRisk = TryGetDouble(situation.Meta, "avg_risk");
        var actorCount = situation.ActorIds?.Count ?? 0;
        var driver = sourceCount >= 4
            ? "cross-source convergence"
            : actorCount >= 3
                ? "actor convergence"
                : situation.EventCount >= 3
                    ? "event clustering"
                    : "emerging situation";
        var trajectory = severity is "critical" or "high" ? "rising" : "stable";
        var action = severity switch
        {
            "critical" => "triage now",
            "high" => "escalate to ops",
            "medium" => "investigate",
            _ => "monitor"
        };
        var confidence = Math.Min(1.0, 0.3 + (situation.EventCount * 0.08) + (actorCount * 0.05) + (sourceCount * 0.06));

        return new AnalystIndicatorResponse(
            Id: situation.SituationId,
            Label: situation.Title,
            Category: "situation",
            IndicatorKind: "situation",
            EvidenceKind: situation.EventCount > 1 || actorCount > 1 ? "correlated" : "observed",
            AssessmentKind: "correlated_situation",
            Severity: severity,
            Driver: driver,
            DriverCode: NormalizeCode(driver, "correlated_situation"),
            Trajectory: trajectory,
            TrajectoryCode: trajectory,
            RecommendedAction: action,
            RecommendedActionCode: NormalizeCode(action, "monitor"),
            Region: string.IsNullOrWhiteSpace(situation.Region) ? null : situation.Region,
            Score: situation.RiskScore,
            Confidence: confidence,
            Corroboration: Math.Min(1.0, (sourceCount * 0.12) + (situation.EventCount * 0.10)),
            Linked: new IndicatorLinkCountsResponse(
                Actors: actorCount,
                Narratives: 0,
                Signals: 0,
                Alerts: 0,
                Regions: string.IsNullOrWhiteSpace(situation.Region) ? 0 : 1,
                Sources: sourceCount,
                Events: situation.EventCount
            ),
            Summary: situation.Description ?? situation.Title,
            ObservationSummary: $"{situation.EventCount} event(s) and {actorCount} actor(s) were grouped into {situation.Title.ToLowerInvariant()}.",
            AssessmentSummary: $"{situation.Title} is assessed as {severity} severity with risk {situation.RiskScore:0.00} and average member risk {avgRisk:0.00}.",
            CorrelationSummary: $"{situation.EventCount} linked event(s), {actorCount} actor(s), and {sourceCount} source family(s) support this case."
        );
    }

    private static int TryGetInt(JsonElement meta, string property)
    {
        if (meta.ValueKind != JsonValueKind.Object || !meta.TryGetProperty(property, out var value))
            return 0;
        if (value.TryGetInt32(out var intValue))
            return intValue;
        return 0;
    }

    private static double TryGetDouble(JsonElement meta, string property)
    {
        if (meta.ValueKind != JsonValueKind.Object || !meta.TryGetProperty(property, out var value))
            return 0;
        if (value.TryGetDouble(out var doubleValue))
            return doubleValue;
        return 0;
    }

    private static string SeverityFromScore(double score) => score switch
    {
        >= 0.85 => "critical",
        >= 0.65 => "high",
        >= 0.45 => "medium",
        _ => "low",
    };

    private static string NormalizeCode(string? value, string fallback)
    {
        if (string.IsNullOrWhiteSpace(value))
            return fallback;

        var normalized = value.Trim().ToLowerInvariant()
            .Replace("/", " ")
            .Replace("-", " ")
            .Replace(".", " ");
        var parts = normalized
            .Split(' ', StringSplitOptions.RemoveEmptyEntries | StringSplitOptions.TrimEntries);
        return parts.Length == 0 ? fallback : string.Join("_", parts);
    }
}
