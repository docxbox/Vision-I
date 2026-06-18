using Microsoft.AspNetCore.Authorization;
using Microsoft.AspNetCore.Mvc;
using VisionI.API.Models.Responses;
using VisionI.API.Services;

namespace VisionI.API.Controllers;

/// <summary>
/// Signal intelligence endpoints - semantic search, clusters, signal detail.
/// Reads from precomputed Redis keys where available, falls back to Python API.
/// </summary>
[ApiController]
[Route("api/signals")]
[Authorize]
[Produces("application/json")]
public class SignalsController : ControllerBase
{
    private readonly IIntelligenceService _intelligence;

    public SignalsController(IIntelligenceService intelligence)
    {
        _intelligence = intelligence;
    }

    /// <summary>List signals - cached 2 minutes.</summary>
    [HttpGet]
    public async Task<IActionResult> GetSignals(
        [FromQuery] string? source = null,
        [FromQuery] string? cluster_id = null,
        [FromQuery] int limit = 50,
        CancellationToken ct = default)
    {
        var key = $"cache:signals:{source}:{cluster_id}:{limit}";
        var json = await _intelligence.GetCachedJsonAsync(
            key,
            async innerCt =>
            {
                var parts = new List<string> { $"limit={limit}" };
                if (!string.IsNullOrWhiteSpace(source)) parts.Add($"source={Uri.EscapeDataString(source)}");
                if (!string.IsNullOrWhiteSpace(cluster_id)) parts.Add($"cluster_id={Uri.EscapeDataString(cluster_id)}");
                return await _intelligence.GetPythonJsonAsync($"/signals?{string.Join("&", parts)}", innerCt);
            },
            TimeSpan.FromMinutes(2),
            ct);

        if (json == null) return StatusCode(502, "Intelligence layer unavailable.");

        var payload = _intelligence.DeserializeJson<SignalListResponse>(json);
        if (payload is null) return StatusCode(502, "Invalid signal payload.");

        var projected = payload with
        {
            Signals = payload.Signals
                .Select(signal => signal with { Indicator = BuildSignalIndicator(signal) })
                .ToList()
        };

        return Content(_intelligence.SerializeJson(projected), "application/json");
    }

    /// <summary>Semantic similarity search on signals.</summary>
    [HttpGet("search")]
    public async Task<IActionResult> SearchSignals(
        [FromQuery] string q,
        [FromQuery] float threshold = 0.5f,
        [FromQuery] int limit = 20,
        CancellationToken ct = default)
    {
        var payload = await _intelligence.GetPythonModelAsync<SignalListResponse>(
            $"/signals/search?q={Uri.EscapeDataString(q)}&threshold={threshold}&limit={limit}",
            ct);
        if (payload == null) return StatusCode(502, "Intelligence layer unavailable.");
        var projected = payload with
        {
            Signals = payload.Signals
                .Select(signal => signal with { Indicator = BuildSignalIndicator(signal) })
                .ToList()
        };
        return Content(_intelligence.SerializeJson(projected), "application/json");
    }

    /// <summary>Recent signal clusters - reads precomputed key first.</summary>
    [HttpGet("clusters")]
    public async Task<IActionResult> GetClusters(
        [FromQuery] int limit = 20,
        CancellationToken ct = default)
    {
        var precomputed = await _intelligence.GetPrecomputedJsonAsync("precomputed:signal_clusters", ct);
        if (precomputed != null)
        {
            try
            {
                var cachedPayload = _intelligence.DeserializeJson<SignalClustersResponse>(precomputed);
                if (cachedPayload != null)
                    return Content(_intelligence.SerializeJson(cachedPayload with
                    {
                        ServedFrom = "precomputed",
                        Clusters = (cachedPayload.Clusters ?? []).Select(cluster => cluster with { Indicator = BuildClusterIndicator(cluster) }).ToList()
                    }), "application/json");
            }
            catch
            {
                // Precomputed key may be a raw JSON array (list, not object) from an older
                // cache write. Fall through to Python which returns the correct {clusters:[...]} shape.
            }
        }

        var payload = await _intelligence.GetPythonModelAsync<SignalClustersResponse>($"/signals/clusters?limit={limit}", ct);
        if (payload == null) return StatusCode(502, "Intelligence layer unavailable.");
        var projected = payload with
        {
            Clusters = (payload.Clusters ?? []).Select(cluster => cluster with { Indicator = BuildClusterIndicator(cluster) }).ToList()
        };
        return Content(_intelligence.SerializeJson(projected), "application/json");
    }

    /// <summary>Correlation summary - reads precomputed key.</summary>
    [HttpGet("correlation-summary")]
    public async Task<IActionResult> GetCorrelationSummary(CancellationToken ct = default)
    {
        var precomputed = await _intelligence.GetPrecomputedJsonAsync("precomputed:correlation_summary", ct);
        if (precomputed != null)
        {
            var cachedPayload = _intelligence.DeserializeJson<SignalCorrelationSummaryResponse>(precomputed);
            if (cachedPayload != null)
                return Content(_intelligence.SerializeJson(cachedPayload with { ServedFrom = "precomputed" }), "application/json");
        }

        var payload = await _intelligence.GetPythonModelAsync<SignalCorrelationSummaryResponse>("/signals/correlation-summary", ct);
        if (payload == null) return StatusCode(502, "Intelligence layer unavailable.");
        return Content(_intelligence.SerializeJson(payload), "application/json");
    }

    /// <summary>Confidence distribution - reads precomputed key.</summary>
    [HttpGet("confidence-distribution")]
    public async Task<IActionResult> GetConfidenceDistribution(CancellationToken ct = default)
    {
        var precomputed = await _intelligence.GetPrecomputedJsonAsync("precomputed:confidence_distribution", ct);
        if (precomputed != null)
        {
            var cachedPayload = _intelligence.DeserializeJson<SignalConfidenceDistributionResponse>(precomputed);
            if (cachedPayload != null)
                return Content(_intelligence.SerializeJson(cachedPayload), "application/json");
        }

        var payload = await _intelligence.GetPythonModelAsync<SignalConfidenceDistributionResponse>("/signals/confidence-distribution", ct);
        if (payload == null) return StatusCode(502, "Intelligence layer unavailable.");
        return Content(_intelligence.SerializeJson(payload), "application/json");
    }

    /// <summary>Single signal detail.</summary>
    [HttpGet("{signalId}")]
    public async Task<IActionResult> GetSignal(string signalId, CancellationToken ct = default)
    {
        var payload = await _intelligence.GetPythonModelAsync<SignalDetailResponse>($"/signals/{Uri.EscapeDataString(signalId)}", ct);
        if (payload == null) return NotFound();
        var projected = payload with
        {
            Indicator = BuildSignalIndicator(new SignalRecordResponse(
                SignalId: payload.SignalId,
                SourceEventId: payload.SourceEventId,
                Source: payload.Source,
                SignalType: payload.SignalType,
                Title: payload.Title,
                Body: payload.Body,
                Timestamp: payload.Timestamp,
                Actors: payload.Actors,
                LocationName: payload.LocationName,
                LocationLat: payload.LocationLat,
                LocationLon: payload.LocationLon,
                SentimentScore: payload.SentimentScore,
                Confidence: payload.Confidence,
                ClusterId: payload.ClusterId,
                Meta: payload.Meta,
                Similarity: null,
                Indicator: null))
        };
        return Content(_intelligence.SerializeJson(projected), "application/json");
    }

    private static AnalystIndicatorResponse BuildSignalIndicator(SignalRecordResponse signal)
    {
        var confidence = signal.Confidence ?? 0;
        var driver = !string.IsNullOrWhiteSpace(signal.ClusterId) ? "clustered signal"
            : confidence >= 0.75 ? "strong confidence"
            : !string.IsNullOrWhiteSpace(signal.Source) ? $"{signal.Source} pickup"
            : "emerging signal";
        var action = confidence >= 0.8 ? "investigate"
            : !string.IsNullOrWhiteSpace(signal.ClusterId) ? "compare cluster"
            : "watch";
        var severity = confidence switch
        {
            >= 0.85 => "critical",
            >= 0.65 => "high",
            >= 0.45 => "medium",
            _ => "low",
        };
        var trajectory = action switch
        {
            "investigate" => "rising",
            "compare cluster" => "watch",
            _ => "stable"
        };

        return new AnalystIndicatorResponse(
            Id: signal.SignalId,
            Label: string.IsNullOrWhiteSpace(signal.Title) ? "Signal" : signal.Title,
            Category: "signal",
            IndicatorKind: "signal",
            EvidenceKind: string.IsNullOrWhiteSpace(signal.ClusterId) ? "observed" : "correlated",
            AssessmentKind: string.IsNullOrWhiteSpace(signal.ClusterId) ? "signal_detection" : "signal_cluster_member",
            Severity: severity,
            Driver: driver,
            DriverCode: NormalizeCode(driver, "signal_detection"),
            Trajectory: trajectory,
            TrajectoryCode: trajectory,
            RecommendedAction: action,
            RecommendedActionCode: NormalizeCode(action, "watch"),
            Region: signal.LocationName,
            Score: confidence,
            Confidence: confidence,
            Corroboration: Math.Min(1.0, 0.2 + ((signal.Actors?.Count ?? 0) * 0.12) + (string.IsNullOrWhiteSpace(signal.ClusterId) ? 0 : 0.28)),
            Linked: new IndicatorLinkCountsResponse(
                Actors: signal.Actors?.Count ?? 0,
                Narratives: 0,
                Signals: 1,
                Alerts: 0,
                Regions: string.IsNullOrWhiteSpace(signal.LocationName) ? 0 : 1,
                Sources: string.IsNullOrWhiteSpace(signal.Source) ? 0 : 1,
                Events: string.IsNullOrWhiteSpace(signal.SourceEventId) ? 0 : 1
            ),
            Summary: string.IsNullOrWhiteSpace(signal.Title) ? "Signal" : signal.Title,
            ObservationSummary: $"{(string.IsNullOrWhiteSpace(signal.Title) ? "Signal" : signal.Title)} is the leading signal in the current result set.",
            AssessmentSummary: $"{(string.IsNullOrWhiteSpace(signal.Title) ? "Signal" : signal.Title)} carries confidence {confidence:0.00} and is best treated as {severity} priority evidence.",
            CorrelationSummary: $"{(string.IsNullOrWhiteSpace(signal.Source) ? 0 : 1)} source(s), {(signal.Actors?.Count ?? 0)} actor(s), and {(string.IsNullOrWhiteSpace(signal.ClusterId) ? "no cluster" : $"cluster {signal.ClusterId}")} support this signal."
        );
    }

    private static AnalystIndicatorResponse BuildClusterIndicator(SignalClusterResponse cluster)
    {
        var signalCount = cluster.SignalCount;
        var sourceCount = cluster.Sources?.Count ?? 0;
        var actorCount = cluster.SharedActors?.Count ?? 0;
        var score = Math.Min(1.0, 0.35 + (signalCount * 0.08) + (sourceCount * 0.06) + (actorCount * 0.04));
        var severity = score switch
        {
            >= 0.85 => "critical",
            >= 0.65 => "high",
            >= 0.45 => "medium",
            _ => "low",
        };
        var action = signalCount >= 4 ? "investigate" : "compare cluster";
        var label = string.IsNullOrWhiteSpace(cluster.RepresentativeTitle)
            ? $"Cluster {cluster.ClusterId}"
            : cluster.RepresentativeTitle;

        return new AnalystIndicatorResponse(
            Id: cluster.ClusterId,
            Label: label,
            Category: "signal",
            IndicatorKind: "signal_cluster",
            EvidenceKind: "correlated",
            AssessmentKind: "signal_cluster",
            Severity: severity,
            Driver: "semantic convergence",
            DriverCode: "semantic_convergence",
            Trajectory: signalCount >= 4 ? "rising" : "watch",
            TrajectoryCode: signalCount >= 4 ? "rising" : "watch",
            RecommendedAction: action,
            RecommendedActionCode: NormalizeCode(action, "compare_cluster"),
            Region: null,
            Score: score,
            Confidence: score,
            Corroboration: Math.Min(1.0, (signalCount * 0.12) + (sourceCount * 0.12)),
            Linked: new IndicatorLinkCountsResponse(
                Actors: actorCount,
                Narratives: 0,
                Signals: signalCount,
                Alerts: 0,
                Regions: 0,
                Sources: sourceCount,
                Events: signalCount
            ),
            Summary: label,
            ObservationSummary: $"{label} groups related weak signals into a common cluster.",
            AssessmentSummary: $"{label} suggests multiple weak indicators are converging.",
            CorrelationSummary: $"{signalCount} clustered signal(s), {sourceCount} source(s), and {actorCount} shared actor(s) are grouped together."
        );
    }

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
