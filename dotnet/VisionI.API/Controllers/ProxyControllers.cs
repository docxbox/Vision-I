using System.Text.Json;
using Microsoft.AspNetCore.Authorization;
using Microsoft.AspNetCore.Mvc;
using VisionI.API.Services;

namespace VisionI.API.Controllers;

[ApiController]
[Route("api/entities")]
[Authorize]
[Produces("application/json")]
public class EntitiesController : ControllerBase
{
    private readonly IIntelligenceService _intelligence;
    private readonly IHttpClientFactory _httpFactory;

    public EntitiesController(IIntelligenceService intelligence, IHttpClientFactory httpFactory)
    {
        _intelligence = intelligence;
        _httpFactory = httpFactory;
    }

    /// <summary>List known actors and locations - cached 10 minutes.</summary>
    [HttpGet]
    public async Task<IActionResult> GetEntities(
        [FromQuery] string? type = null,
        [FromQuery] int min_mentions = 1,
        [FromQuery] int limit = 100,
        [FromQuery] int offset = 0,
        CancellationToken ct = default)
    {
        var key = $"cache:entities:{type}:{min_mentions}:{limit}:{offset}";
        var json = await _intelligence.GetCachedJsonAsync(
            key,
            async innerCt =>
            {
                var parts = new List<string>
                {
                    $"min_mentions={min_mentions}",
                    $"limit={limit}",
                    $"offset={offset}",
                };
                if (!string.IsNullOrWhiteSpace(type)) parts.Add($"type={Uri.EscapeDataString(type)}");
                return await _intelligence.GetPythonJsonAsync($"/entities?{string.Join("&", parts)}", innerCt);
            },
            TimeSpan.FromMinutes(10),
            ct);

        if (json == null) return StatusCode(502, "Intelligence layer unavailable.");
        return Content(json, "application/json");
    }

    /// <summary>Relationship graph for one actor - cached 10 minutes.</summary>
    [HttpGet("{entityId}/graph")]
    public async Task<IActionResult> GetEntityGraph(
        string entityId,
        [FromQuery] int depth = 1,
        CancellationToken ct = default)
    {
        var key = $"cache:entity:graph:{entityId}:{depth}";
        var json = await _intelligence.GetCachedJsonAsync(
            key,
            innerCt => _intelligence.GetPythonJsonAsync(
                $"/entities/{Uri.EscapeDataString(entityId)}/graph?depth={depth}",
                innerCt),
            TimeSpan.FromMinutes(10),
            ct);

        if (json == null) return NotFound();
        return Content(json, "application/json");
    }

    /// <summary>Normalize and persist a graph node as an entity before navigation.</summary>
    [HttpPost("map")]
    public async Task<IActionResult> MapEntityFromGraph([FromBody] JsonElement body, CancellationToken ct = default)
    {
        var json = await _intelligence.PostPythonJsonAsync("/entities/map", body, ct);
        if (json == null) return StatusCode(502, "Intelligence layer unavailable.");

        await _intelligence.RemoveCacheAsync("cache:entities:::100:0", ct);
        await _intelligence.RemoveCacheAsync("cache:entities::1:100:0", ct);
        await _intelligence.RemoveCacheAsync("cache:entities::1:200:0", ct);
        return Content(json, "application/json");
    }

    /// <summary>Best-effort public encyclopedia enrichment for an entity profile.</summary>
    [HttpGet("{entityId}/wikipedia")]
    public async Task<IActionResult> GetWikipediaProfile(string entityId, CancellationToken ct = default)
    {
        var label = DecodeEntityLabel(entityId);
        try
        {
            using var timeoutCts = CancellationTokenSource.CreateLinkedTokenSource(ct);
            timeoutCts.CancelAfter(TimeSpan.FromSeconds(4));

            var http = _httpFactory.CreateClient();
            http.Timeout = TimeSpan.FromSeconds(4);
            http.DefaultRequestHeaders.UserAgent.ParseAdd("Vision-I/1.0 local intelligence workspace");

            var summary = await TryFetchWikipediaSummaryAsync(http, label, timeoutCts.Token);
            if (summary is null)
            {
                var resolvedTitle = await TrySearchWikipediaTitleAsync(http, label, timeoutCts.Token);
                if (!string.IsNullOrWhiteSpace(resolvedTitle))
                    summary = await TryFetchWikipediaSummaryAsync(http, resolvedTitle, timeoutCts.Token);
            }

            if (summary is not null)
                return Content(summary, "application/json");
        }
        catch
        {
            // Keep entity profiles usable even when the local environment has no internet.
        }

        return Content(JsonSerializer.Serialize(new
        {
            title = label,
            extract = $"No external encyclopedia summary is available for {label}. Showing local ontology and event evidence instead.",
            content_urls = new { desktop = new { page = $"https://en.wikipedia.org/wiki/{Uri.EscapeDataString(label.Replace(' ', '_'))}" } },
            _served_from = "fallback"
        }), "application/json");
    }

    private static string DecodeEntityLabel(string entityId)
    {
        var decoded = Uri.UnescapeDataString(entityId)
            .Replace("actor:", "", StringComparison.OrdinalIgnoreCase)
            .Replace("org:", "", StringComparison.OrdinalIgnoreCase)
            .Replace('_', ' ')
            .Replace('-', ' ')
            .Trim();
        return string.IsNullOrWhiteSpace(decoded) ? entityId : decoded;
    }

    private static async Task<string?> TryFetchWikipediaSummaryAsync(HttpClient http, string title, CancellationToken ct)
    {
        var url = $"https://en.wikipedia.org/api/rest_v1/page/summary/{Uri.EscapeDataString(title.Replace(' ', '_'))}";
        using var response = await http.GetAsync(url, ct);
        if (!response.IsSuccessStatusCode)
            return null;

        var json = await response.Content.ReadAsStringAsync(ct);
        using var doc = JsonDocument.Parse(json);
        if (doc.RootElement.TryGetProperty("type", out var type) &&
            string.Equals(type.GetString(), "https://mediawiki.org/wiki/HyperSwitch/errors/not_found", StringComparison.OrdinalIgnoreCase))
            return null;

        return JsonSerializer.Serialize(new
        {
            title = doc.RootElement.TryGetProperty("title", out var t) ? t.GetString() : title,
            description = doc.RootElement.TryGetProperty("description", out var d) ? d.GetString() : null,
            extract = doc.RootElement.TryGetProperty("extract", out var e) ? e.GetString() : null,
            thumbnail = doc.RootElement.TryGetProperty("thumbnail", out var th) ? th.Clone() : default(JsonElement?),
            content_urls = doc.RootElement.TryGetProperty("content_urls", out var urls) ? urls.Clone() : default(JsonElement?),
            _served_from = "wikipedia"
        });
    }

    private static async Task<string?> TrySearchWikipediaTitleAsync(HttpClient http, string query, CancellationToken ct)
    {
        var url = $"https://en.wikipedia.org/w/api.php?action=query&list=search&srsearch={Uri.EscapeDataString(query)}&format=json&srlimit=1";
        using var response = await http.GetAsync(url, ct);
        if (!response.IsSuccessStatusCode)
            return null;

        using var doc = JsonDocument.Parse(await response.Content.ReadAsStringAsync(ct));
        if (doc.RootElement.TryGetProperty("query", out var q) &&
            q.TryGetProperty("search", out var results) &&
            results.ValueKind == JsonValueKind.Array &&
            results.GetArrayLength() > 0 &&
            results[0].TryGetProperty("title", out var title))
            return title.GetString();

        return null;
    }
}

[ApiController]
[Route("api/streams")]
[Authorize]
[Produces("application/json")]
public class StreamsController : ControllerBase
{
    private readonly IIntelligenceService _intelligence;

    public StreamsController(IIntelligenceService intelligence)
    {
        _intelligence = intelligence;
    }

    /// <summary>
    /// Latest live events — reads precomputed Redis key only (&lt;5ms).
    /// No Python fallback: live fetch is excluded from the request path because
    /// orchestrator.run_live_only() takes 15 s+ and blocks Uvicorn workers.
    /// The live_ingest_job (default 180 s interval) keeps precomputed:live_streams warm.
    /// </summary>
    [HttpGet("live")]
    public async Task<IActionResult> GetLive(
        [FromQuery] int limit = 20,
        [FromQuery] string? sources = null,
        CancellationToken ct = default)
    {
        try
        {
            var precomputed = await _intelligence.GetPrecomputedJsonAsync("precomputed:live_streams", ct);
            if (precomputed != null)
                return Content(NormalizeLiveStreamsPayload(precomputed, limit, sources), "application/json");
        }
        catch { /* Redis unavailable — fall through to degraded empty response */ }

        return Content(
            "{\"total\":0,\"source_counts\":{},\"source_errors\":{\"cache\":\"warming\"},\"events\":[],\"_served_from\":\"degraded\"}",
            "application/json");
    }

    private static string NormalizeLiveStreamsPayload(string json, int limit, string? sources)
    {
        using var doc = JsonDocument.Parse(json);

        JsonElement eventsElement;
        if (doc.RootElement.ValueKind == JsonValueKind.Array)
        {
            eventsElement = doc.RootElement;
        }
        else if (doc.RootElement.ValueKind == JsonValueKind.Object
            && doc.RootElement.TryGetProperty("events", out var payloadEvents)
            && payloadEvents.ValueKind == JsonValueKind.Array)
        {
            eventsElement = payloadEvents;
        }
        else
        {
            return json;
        }

        HashSet<string>? allowedSources = null;
        if (!string.IsNullOrWhiteSpace(sources))
        {
            allowedSources = sources
                .Split(',', StringSplitOptions.RemoveEmptyEntries | StringSplitOptions.TrimEntries)
                .ToHashSet(StringComparer.OrdinalIgnoreCase);
        }

        var events = new List<JsonElement>();
        var sourceCounts = new Dictionary<string, int>(StringComparer.OrdinalIgnoreCase);

        foreach (var item in eventsElement.EnumerateArray())
        {
            var source = item.TryGetProperty("source", out var sourceProp)
                ? sourceProp.GetString() ?? "unknown"
                : "unknown";

            if (allowedSources != null && !allowedSources.Contains(source))
                continue;

            events.Add(item.Clone());
            sourceCounts[source] = sourceCounts.GetValueOrDefault(source) + 1;

            if (events.Count >= limit)
                break;
        }

        return JsonSerializer.Serialize(new
        {
            total = events.Count,
            source_counts = sourceCounts,
            source_errors = new Dictionary<string, string>(),
            events,
            _served_from = "precomputed",
        });
    }
}

[ApiController]
[Route("api/sentiment")]
[Authorize]
[Produces("application/json")]
public class SentimentController : ControllerBase
{
    private readonly IIntelligenceService _intelligence;

    public SentimentController(IIntelligenceService intelligence)
    {
        _intelligence = intelligence;
    }

    /// <summary>
    /// Country-level sentiment heatmap - reads precomputed Redis key first, fallback to Python API.
    /// Used by COP HEAT layer for geographic sentiment overlay.
    /// </summary>
    [HttpGet("country-heatmap")]
    public async Task<IActionResult> GetCountryHeatmap(
        [FromQuery] int days_back = 7,
        CancellationToken ct = default)
    {
        if (days_back == 7)
        {
            var precomputed = await _intelligence.GetPrecomputedJsonAsync("precomputed:country_sentiment", ct);
            if (precomputed != null)
                return Content(precomputed, "application/json");
        }

        var json = await _intelligence.GetCachedJsonAsync(
            $"cache:sentiment:country:{days_back}",
            innerCt => _intelligence.GetPythonJsonAsync($"/sentiment/country-heatmap?days_back={days_back}", innerCt),
            TimeSpan.FromMinutes(5),
            ct);

        if (json == null) return StatusCode(502, "Intelligence layer unavailable.");
        return Content(json, "application/json");
    }

    /// <summary>
    /// Aggregated sentiment scores over time - cached 5 minutes.
    /// Returns a time series bucketed by hour, day, or week.
    /// </summary>
    [HttpGet("timeline")]
    public async Task<IActionResult> GetTimeline(
        [FromQuery] string? query = null,
        [FromQuery] string? source = null,
        [FromQuery] string? entity = null,
        [FromQuery] string? entity_id = null,
        [FromQuery] string? from = null,
        [FromQuery] string? to = null,
        [FromQuery] int? hours = null,
        [FromQuery] string bucket = "day",
        CancellationToken ct = default)
    {
        entity_id ??= entity;
        var key = $"cache:sentiment:{query}:{source}:{entity_id}:{from}:{to}:{hours}:{bucket}";

        var json = await _intelligence.GetCachedJsonAsync(
            key,
            async innerCt =>
            {
                var parts = new List<string> { $"bucket={Uri.EscapeDataString(bucket)}" };
                if (!string.IsNullOrWhiteSpace(query)) parts.Add($"query={Uri.EscapeDataString(query)}");
                if (!string.IsNullOrWhiteSpace(source)) parts.Add($"source={Uri.EscapeDataString(source)}");
                if (!string.IsNullOrWhiteSpace(entity_id)) parts.Add($"entity_id={Uri.EscapeDataString(entity_id)}");
                if (!string.IsNullOrWhiteSpace(from)) parts.Add($"from={Uri.EscapeDataString(from)}");
                if (!string.IsNullOrWhiteSpace(to)) parts.Add($"to={Uri.EscapeDataString(to)}");
                if (hours.HasValue) parts.Add($"hours={hours.Value}");
                return await _intelligence.GetPythonJsonAsync($"/sentiment/timeline?{string.Join("&", parts)}", innerCt);
            },
            TimeSpan.FromMinutes(5),
            ct);

        if (json == null) return StatusCode(502, "Intelligence layer unavailable.");
        return Content(json, "application/json");
    }
}

[ApiController]
[Route("api/sources")]
[Authorize]
[Produces("application/json")]
public class SourcesController : ControllerBase
{
    private readonly IIntelligenceService _intelligence;
    private readonly SourceCatalogService _catalog;

    public SourcesController(IIntelligenceService intelligence, SourceCatalogService catalog)
    {
        _intelligence = intelligence;
        _catalog = catalog;
    }

    [HttpGet("catalog")]
    public async Task<IActionResult> GetCatalog(CancellationToken ct = default)
    {
        var json = await _catalog.BuildCatalogJsonAsync(ct);
        return Content(json, "application/json");
    }

    /// <summary>
    /// Proxy any per-extractor source endpoint to the Python API.
    /// Pass source-specific query parameters through transparently.
    /// Example: GET /api/sources/usgs?min_mag=6.0&amp;hours_back=48
    /// </summary>
    [HttpGet("{source}")]
    public async Task<IActionResult> SearchSource(
        string source,
        CancellationToken ct = default)
    {
        var queryParams = Request.Query
            .ToDictionary(kv => kv.Key, kv => kv.Value.FirstOrDefault());

        var qpStable = string.Join("&", queryParams
            .OrderBy(kv => kv.Key, StringComparer.Ordinal)
            .Select(kv => $"{kv.Key}={kv.Value}"));
        var cacheKey = $"cache:sources:{source}:{qpStable}";

        var json = await _intelligence.GetCachedJsonAsync(
            cacheKey,
            innerCt =>
            {
                var queryString = string.Join("&", queryParams
                    .Where(kv => kv.Value != null)
                    .Select(kv => $"{Uri.EscapeDataString(kv.Key)}={Uri.EscapeDataString(kv.Value!)}"));
                var path = $"/sources/{Uri.EscapeDataString(source)}";
                if (!string.IsNullOrWhiteSpace(queryString))
                {
                    path += $"?{queryString}";
                }

                return _intelligence.GetPythonJsonAsync(path, innerCt);
            },
            TimeSpan.FromSeconds(30),
            ct);

        if (json == null) return StatusCode(502, "Intelligence layer unavailable or source is invalid.");
        return Content(json, "application/json");
    }
}
