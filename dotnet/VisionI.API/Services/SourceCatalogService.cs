using System.Data;
using System.Text.Json;
using Microsoft.EntityFrameworkCore;
using VisionI.API.Infrastructure;

namespace VisionI.API.Services;

public sealed class SourceCatalogService
{
    private static readonly JsonSerializerOptions JsonOptions = new(JsonSerializerDefaults.Web);

    private readonly AppDbContext _db;
    private readonly IConfiguration _config;
    private readonly IIntelligenceService _intelligence;

    public SourceCatalogService(AppDbContext db, IConfiguration config, IIntelligenceService intelligence)
    {
        _db = db;
        _config = config;
        _intelligence = intelligence;
    }

    public async Task<string> BuildCatalogJsonAsync(CancellationToken ct = default)
    {
        var cacheKey = "cache:sources:catalog:v3-local";
        var cached = await _intelligence.ReadCacheJsonAsync(cacheKey, ct);
        if (!string.IsNullOrWhiteSpace(cached))
            return cached;

        var checkpoints = await ReadCheckpointRowsAsync(ct);
        var items = Definitions
            .Select(definition => BuildCatalogItem(definition, checkpoints))
            .ToList();

        var json = JsonSerializer.Serialize(new
        {
            total = items.Count,
            sources = items,
            _served_from = "db",
        }, JsonOptions);

        await _intelligence.SetCachedJsonAsync(cacheKey, json, TimeSpan.FromMinutes(3), ct);
        return json;
    }

    public async Task<string> BuildOverviewSourceHealthJsonAsync(CancellationToken ct = default)
    {
        var cacheKey = "cache:overview:source-health:v2-local";
        var cached = await _intelligence.ReadCacheJsonAsync(cacheKey, ct);
        if (!string.IsNullOrWhiteSpace(cached))
            return cached;

        var checkpoints = await ReadCheckpointRowsAsync(ct);
        var rows = Definitions
            .Select(definition => BuildOverviewHealthRow(definition, checkpoints))
            .ToList();

        var json = JsonSerializer.Serialize(rows, JsonOptions);
        await _intelligence.SetCachedJsonAsync(cacheKey, json, TimeSpan.FromMinutes(2), ct);
        return json;
    }

    private object BuildCatalogItem(SourceDefinition definition, Dictionary<string, SourceCheckpointRow> checkpoints)
    {
        var health = BuildHealth(definition, checkpoints);
        return new
        {
            key = definition.Key,
            label = definition.Label,
            category = definition.Category,
            extractor = definition.Extractor,
            modes = definition.Modes,
            supports_query = definition.SupportsQuery,
            nlp_mode = definition.NlpMode,
            description = definition.Description,
            route = definition.Route,
            aliases = definition.Aliases,
            requires_credentials = definition.RequiresCredentials,
            parameters = definition.Parameters.Select(parameter => new
            {
                name = parameter.Name,
                kind = parameter.Kind,
                required = parameter.Required,
                description = parameter.Description,
                @default = parameter.Default,
            }),
            health,
            credibility_score = health.CredibilityScore,
        };
    }

    private object BuildOverviewHealthRow(SourceDefinition definition, Dictionary<string, SourceCheckpointRow> checkpoints)
    {
        var health = BuildHealth(definition, checkpoints);
        return new
        {
            source_name = definition.Label,
            status = health.Status,
            record_count = health.RecordCount,
            last_checked = health.LastChecked,
        };
    }

    private SourceHealthEnvelope BuildHealth(SourceDefinition definition, Dictionary<string, SourceCheckpointRow> checkpoints)
    {
        checkpoints.TryGetValue(definition.Key, out var checkpoint);

        var credsConfigured = !definition.RequiresCredentials || definition.RequiredEnvVars.Any(IsConfigured);
        var lastChecked = checkpoint?.LastRunAt?.ToString("O");
        var recordCount = checkpoint?.EventsFetched ?? 0;
        var errorCount = checkpoint?.ErrorCount ?? 0;
        var lastError = checkpoint?.LastError ?? string.Empty;

        string status;
        string? detail = null;

        if (!credsConfigured)
        {
            status = "not_configured";
            detail = "optional credentials missing";
        }
        else if (checkpoint?.LastRunAt is null)
        {
            status = definition.RequiresCredentials ? "unknown" : "standby";
            detail = definition.RequiresCredentials ? "no recent checkpoint" : "available on demand";
        }
        else
        {
            var age = DateTimeOffset.UtcNow - checkpoint.LastRunAt.Value;
            if (errorCount >= 5 && recordCount == 0)
            {
                status = "down";
                detail = string.IsNullOrWhiteSpace(lastError) ? "repeated extractor failures" : lastError;
            }
            else if (errorCount > 0 || age > TimeSpan.FromHours(24))
            {
                status = "degraded";
                detail = string.IsNullOrWhiteSpace(lastError) ? "stale or partial health" : lastError;
            }
            else if (recordCount > 0 || age <= TimeSpan.FromHours(24))
            {
                status = "healthy";
                detail = "recent checkpoint observed";
            }
            else
            {
                status = "unknown";
                detail = "checkpoint present without recent activity";
            }
        }

        return new SourceHealthEnvelope(
            Status: status,
            Detail: detail,
            LastChecked: lastChecked,
            RecordCount: recordCount,
            ErrorCount: errorCount,
            CredibilityScore: checkpoint?.CredibilityScore,
            ProbeStatus: null);
    }

    private bool IsConfigured(string envVar)
        => !string.IsNullOrWhiteSpace(_config[envVar]);

    private async Task<Dictionary<string, SourceCheckpointRow>> ReadCheckpointRowsAsync(CancellationToken ct)
    {
        var rows = new Dictionary<string, SourceCheckpointRow>(StringComparer.OrdinalIgnoreCase);

        var conn = _db.Database.GetDbConnection();
        if (conn.State != ConnectionState.Open)
            await conn.OpenAsync(ct);

        await using var cmd = conn.CreateCommand();
        cmd.CommandText = """
            SELECT source, last_run_at, events_fetched, credibility_score, credibility_note, meta
            FROM source_checkpoints
            """;

        await using var reader = await cmd.ExecuteReaderAsync(ct);
        while (await reader.ReadAsync(ct))
        {
            var source = reader.IsDBNull(0) ? "" : reader.GetString(0);
            var canonical = Canonicalize(source);
            if (string.IsNullOrWhiteSpace(canonical))
                continue;

            var meta = reader.IsDBNull(5) ? null : reader.GetString(5);
            var metaDoc = string.IsNullOrWhiteSpace(meta) ? default(JsonDocument) : JsonDocument.Parse(meta);
            try
            {
                var errorCount = 0;
                string? lastError = null;
                if (metaDoc is not null && metaDoc.RootElement.ValueKind == JsonValueKind.Object)
                {
                    if (metaDoc.RootElement.TryGetProperty("error_count", out var errorCountProp) &&
                        errorCountProp.TryGetInt32(out var parsedErrorCount))
                    {
                        errorCount = parsedErrorCount;
                    }

                    if (metaDoc.RootElement.TryGetProperty("last_error", out var lastErrorProp) &&
                        lastErrorProp.ValueKind == JsonValueKind.String)
                    {
                        lastError = lastErrorProp.GetString();
                    }
                    else if (metaDoc.RootElement.TryGetProperty("error_summary", out var summaryProp) &&
                             summaryProp.ValueKind == JsonValueKind.String)
                    {
                        lastError = summaryProp.GetString();
                    }
                }

                var row = new SourceCheckpointRow(
                    Source: source,
                    LastRunAt: reader.IsDBNull(1) ? null : new DateTimeOffset(reader.GetDateTime(1), TimeSpan.Zero),
                    EventsFetched: reader.IsDBNull(2) ? 0 : reader.GetInt32(2),
                    CredibilityScore: reader.IsDBNull(3) ? null : reader.GetDouble(3),
                    CredibilityNote: reader.IsDBNull(4) ? null : reader.GetString(4),
                    ErrorCount: errorCount,
                    LastError: lastError);

                if (!rows.TryGetValue(canonical, out var existing) ||
                    (row.LastRunAt ?? DateTimeOffset.MinValue) > (existing.LastRunAt ?? DateTimeOffset.MinValue))
                {
                    rows[canonical] = row;
                }
            }
            finally
            {
                metaDoc?.Dispose();
            }
        }

        return rows;
    }

    private static string? Canonicalize(string source)
    {
        var normalized = (source ?? string.Empty).Trim().ToLowerInvariant();
        foreach (var definition in Definitions)
        {
            if (definition.Key.Equals(normalized, StringComparison.OrdinalIgnoreCase) ||
                definition.Aliases.Any(alias => alias.Equals(normalized, StringComparison.OrdinalIgnoreCase)))
            {
                return definition.Key;
            }
        }

        return null;
    }

    private static readonly IReadOnlyList<SourceDefinition> Definitions =
    [
        new("news", "NewsAPI", "news", "NewsExtractor", ["query"], true, "full", "Keyword-driven article ingestion from NewsAPI.", "/sources/news", ["newsapi"], true, ["NEWSAPI_KEY"], [new("query", "string", true, "Search keywords", null), new("limit", "int", false, "Max results", "10"), new("language", "string", false, "ISO language code", "en"), new("days_back", "int", false, "History window", "1"), new("sort_by", "string", false, "publishedAt | relevancy | popularity", "publishedAt")]),
        new("gdelt", "GDELT", "news", "GDELTExtractor", ["query"], true, "full", "Global event/news archive via the GDELT v2 APIs.", "/sources/gdelt", ["gdelt_doc", "gdelt_geo", "gdelt_context", "gdelt_tv"], false, [], [new("query", "string", true, "GDELT query string", null), new("limit", "int", false, "Max records per API", "25"), new("apis", "string", false, "doc | geo | context | tv", "doc"), new("delay", "float", false, "Seconds between requests", "0.5")]),
        new("socials", "Reddit", "social", "RedditExtractor", ["query"], true, "full", "Community discourse and emerging social signals from Reddit.", "/sources/reddit", ["reddit"], false, [], [new("query", "string", true, "Search keywords", null), new("limit", "int", false, "Max results", "25"), new("sort", "string", false, "new | hot | relevance | top", "new"), new("subreddit", "string", false, "Restrict to one subreddit", null)]),
        new("youtube", "YouTube", "social", "YouTubeExtractor", ["query"], true, "full", "Video discovery via yt-dlp search.", "/sources/youtube", [], false, [], [new("query", "string", true, "Search keywords", null), new("limit", "int", false, "Max results", "10")]),
        new("rss", "RSS Feeds", "news", "RSSExtractor", ["query"], true, "full", "Open RSS feed aggregation for flexible OSINT extension.", "/sources/rss", [], false, [], [new("query", "string", true, "Search keywords", null), new("limit", "int", false, "Max results", "20")]),
        new("hackernews", "Hacker News", "community", "HackerNewsExtractor", ["query"], true, "full", "Technology and cyber-adjacent signals from Hacker News.", "/sources/hackernews", [], false, [], [new("query", "string", true, "Search keywords", null), new("limit", "int", false, "Max results", "20")]),
        new("twitter", "Twitter / X", "social", "TwitterExtractor", ["query"], true, "full", "Recent search via the Twitter v2 API. Surfaces social amplification, verified actors, and geotagged posts.", "/sources/twitter", ["x"], true, ["TWITTER_BEARER_TOKEN"], [new("query", "string", true, "Twitter v2 search query", null), new("limit", "int", false, "Max results (10-100)", "25"), new("lang", "string", false, "ISO language code", "en")]),
        new("telegram", "Telegram", "social", "TelegramExtractor", ["query"], true, "full", "Channel-based social signal monitoring from Telegram.", "/sources/telegram", [], true, ["TELEGRAM_BOT_TOKEN", "TELEGRAM_API_ID", "TELEGRAM_API_HASH"], [new("query", "string", true, "Search keywords", null), new("limit", "int", false, "Max results", "20")]),
        new("usgs", "USGS Earthquakes", "geospatial", "USGSExtractor", ["live", "direct"], false, "none", "Structured seismic events from the USGS feed.", "/sources/usgs", [], false, [], [new("limit", "int", false, "Max results", "10"), new("min_mag", "float", false, "Minimum magnitude", "4.0"), new("hours_back", "int", false, "Lookback window", "24")]),
        new("stocks", "Yahoo Finance", "market", "StockExtractor", ["live", "direct"], false, "none", "Market-moving asset signals from tracked tickers.", "/sources/stocks", ["yahoo_finance"], false, [], [new("tickers", "string", false, "Comma-separated ticker list", null), new("limit", "int", false, "Max results", "20")]),
        new("opensky", "OpenSky", "transport", "OpenSkyExtractor", ["live", "direct"], false, "none", "Live aircraft telemetry and transport anomalies.", "/sources/opensky", [], false, [], [new("limit", "int", false, "Max results", "50"), new("callsign", "string", false, "Filter by callsign", null), new("icao24", "string", false, "Filter by ICAO 24-bit hex", null), new("airborne_only", "bool", false, "Only airborne aircraft", null), new("on_ground_only", "bool", false, "Only on-ground aircraft", null)]),
        new("firms", "NASA FIRMS", "geospatial", "FIRMSExtractor", ["live"], false, "none", "Wildfire and thermal anomaly detections.", "", [], true, ["NASA_FIRMS_KEY"], []),
        new("ais", "AIS Vessel Tracking", "transport", "AISExtractor", ["live", "direct"], false, "none", "Live vessel telemetry via aisstream.io WebSocket (free API key) or legacy HTTP AIS endpoint.", "/sources/ais", [], true, ["AIS_API_KEY"], [new("limit", "int", false, "Max results", "50")]),
        new("nws", "Weather", "weather", "WeatherExtractor", ["live"], false, "none", "Operational weather alerts and geospatial hazard context.", "", ["weather"], false, [], []),
        new("who", "WHO", "health", "WHOExtractor", ["query"], false, "light", "Public health and epidemiological intelligence feeds.", "", [], false, [], []),
        new("bluesky", "Bluesky", "social", "bluesky.fetch", ["query", "direct"], true, "full", "Bluesky decentralised social network posts via public AT Protocol API. No credentials required.", "/sources/bluesky", ["bsky"], false, [], [new("query", "string", false, "Search keywords (empty = trending)", ""), new("limit", "int", false, "Max results (1-100)", "25")]),
        new("cisa_kev", "CISA KEV", "vulnerability", "cisa_kev.fetch", ["live", "direct"], false, "none", "CISA Known Exploited Vulnerabilities catalogue. Updated daily. No credentials required.", "/sources/cisa_kev", ["cisa", "kev"], false, [], [new("limit", "int", false, "Max vulnerabilities to return", "50")]),
        new("treasury", "US Treasury Fiscal Data", "fiscal", "treasury.fetch", ["live", "direct"], false, "none", "US Treasury public fiscal data API (debt, spending, revenue). No credentials required.", "/sources/treasury", ["fiscal", "us_treasury"], false, [], [new("endpoint", "string", false, "Fiscal Data API endpoint path", "v1/debt/mspd/mspd_table_1"), new("limit", "int", false, "Max records", "10")]),
    ];

    private sealed record SourceDefinition(
        string Key,
        string Label,
        string Category,
        string Extractor,
        IReadOnlyList<string> Modes,
        bool SupportsQuery,
        string NlpMode,
        string Description,
        string Route,
        IReadOnlyList<string> Aliases,
        bool RequiresCredentials,
        IReadOnlyList<string> RequiredEnvVars,
        IReadOnlyList<SourceParameterDefinition> Parameters);

    private sealed record SourceParameterDefinition(
        string Name,
        string Kind,
        bool Required,
        string Description,
        string? Default);

    private sealed record SourceCheckpointRow(
        string Source,
        DateTimeOffset? LastRunAt,
        int EventsFetched,
        double? CredibilityScore,
        string? CredibilityNote,
        int ErrorCount,
        string? LastError);

    private sealed record SourceHealthEnvelope(
        string Status,
        string? Detail,
        string? LastChecked,
        int RecordCount,
        int ErrorCount,
        double? CredibilityScore,
        string? ProbeStatus);
}
