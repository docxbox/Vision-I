using VisionI.Web.Models;

namespace VisionI.Web.Services;

public sealed class RegionGeoService
{
    private static readonly Dictionary<string, (double Lat, double Lon)> KnownCentroids =
        new(StringComparer.OrdinalIgnoreCase)
        {
            ["UKRAINE"] = (48.3794, 31.1656),
            ["RUSSIA"] = (61.5240, 105.3188),
            ["ISRAEL"] = (31.0461, 34.8516),
            ["PALESTINE"] = (31.9522, 35.2332),
            ["GAZA"] = (31.5000, 34.4700),
            ["LEBANON"] = (33.8547, 35.8623),
            ["SYRIA"] = (34.8021, 38.9968),
            ["IRAN"] = (32.4279, 53.6880),
            ["IRAQ"] = (33.2232, 43.6793),
            ["YEMEN"] = (15.5527, 48.5164),
            ["TURKEY"] = (38.9637, 35.2433),
            ["RED SEA"] = (20.0000, 38.0000),
            ["TAIWAN"] = (23.6978, 120.9605),
            ["TAIWAN STRAIT"] = (24.0000, 119.5000),
            ["SOUTH CHINA SEA"] = (15.0000, 115.0000),
            ["CHINA"] = (35.8617, 104.1954),
            ["NORTH KOREA"] = (40.3399, 127.5101),
            ["SOUTH KOREA"] = (35.9078, 127.7669),
            ["JAPAN"] = (36.2048, 138.2529),
            ["INDIA"] = (20.5937, 78.9629),
            ["PAKISTAN"] = (30.3753, 69.3451),
            ["AFGHANISTAN"] = (33.9391, 67.7100),
            ["SAUDI ARABIA"] = (23.8859, 45.0792),
            ["HORN OF AFRICA"] = (8.0000, 45.0000),
            ["SAHEL"] = (15.0000, 0.0000),
            ["SUDAN"] = (12.8628, 30.2176),
            ["ETHIOPIA"] = (9.1450, 40.4897),
            ["MIDDLE EAST"] = (29.2985, 42.5510),
            ["EAST ASIA"] = (35.0000, 115.0000),
            ["SOUTH ASIA"] = (22.0000, 78.0000),
            ["EASTERN EUROPE"] = (50.0000, 30.0000),
            ["NORTH AFRICA"] = (26.0000, 17.0000),
        };

    private static readonly Dictionary<string, string[]> RegionAliases =
        new(StringComparer.OrdinalIgnoreCase)
        {
            ["TAIWAN STRAIT"] = ["TAIWAN", "CHINA"],
            ["RED SEA"] = ["YEMEN", "SAUDI", "SUEZ"],
            ["HORN OF AFRICA"] = ["SOMALIA", "ETHIOPIA", "ERITREA", "DJIBOUTI"],
            ["MIDDLE EAST"] = ["ISRAEL", "GAZA", "IRAN", "IRAQ", "SYRIA", "LEBANON", "YEMEN"],
            ["EAST ASIA"] = ["TAIWAN", "CHINA", "JAPAN", "KOREA"],
            ["SOUTH ASIA"] = ["INDIA", "PAKISTAN", "AFGHANISTAN"],
            ["EASTERN EUROPE"] = ["UKRAINE", "RUSSIA", "POLAND"],
        };

    public IReadOnlyList<ResolvedEscalationPoint> ResolveHotspots(
        IEnumerable<EscalationScoreDto>? scores,
        IEnumerable<EventDto>? events)
    {
        var eventList = events?.ToList() ?? [];
        var resolved = new List<ResolvedEscalationPoint>();

        foreach (var score in scores ?? [])
        {
            if (string.IsNullOrWhiteSpace(score.Region))
                continue;

            if (!TryResolvePoint(score.Region, eventList, out var lat, out var lon))
                continue;

            var matchingEvents = eventList.Count(ev => MatchesRegion(ev, score.Region));
            resolved.Add(new ResolvedEscalationPoint(
                score.Region,
                lat,
                lon,
                score.Score,
                score.RiskLevel,
                matchingEvents));
        }

        return resolved;
    }

    public bool MatchesRegion(EventDto? ev, string? region)
    {
        if (ev is null || string.IsNullOrWhiteSpace(region))
            return false;

        var candidates = ExpandCandidates(region).ToList();
        var haystacks = new[]
        {
            ev.Location?.Country,
            ev.Location?.Name,
            ev.Title,
            ev.Description,
            ev.Body,
        };

        return haystacks
            .Where(value => !string.IsNullOrWhiteSpace(value))
            .Any(value => candidates.Any(candidate =>
                value!.Contains(candidate, StringComparison.OrdinalIgnoreCase)));
    }

    private static bool TryResolvePoint(string region, IReadOnlyCollection<EventDto> events, out double lat, out double lon)
    {
        var matches = events
            .Where(ev =>
                ev.Location?.Lat is not null &&
                ev.Location?.Lon is not null &&
                MatchesRegionStatic(ev, region))
            .ToList();

        if (matches.Count > 0)
        {
            lat = matches.Average(ev => ev.Location!.Lat!.Value);
            lon = matches.Average(ev => ev.Location!.Lon!.Value);
            return true;
        }

        foreach (var candidate in ExpandCandidates(region))
        {
            if (KnownCentroids.TryGetValue(candidate, out var point))
            {
                lat = point.Lat;
                lon = point.Lon;
                return true;
            }
        }

        lat = default;
        lon = default;
        return false;
    }

    private static bool MatchesRegionStatic(EventDto ev, string region)
    {
        var candidates = ExpandCandidates(region).ToList();
        var haystacks = new[]
        {
            ev.Location?.Country,
            ev.Location?.Name,
            ev.Title,
            ev.Description,
            ev.Body,
        };

        return haystacks
            .Where(value => !string.IsNullOrWhiteSpace(value))
            .Any(value => candidates.Any(candidate =>
                value!.Contains(candidate, StringComparison.OrdinalIgnoreCase)));
    }

    private static IEnumerable<string> ExpandCandidates(string region)
    {
        yield return region.Trim();

        if (RegionAliases.TryGetValue(region.Trim(), out var aliases))
        {
            foreach (var alias in aliases)
                yield return alias;
        }

        foreach (var token in region.Split(['/', ',', '-'], StringSplitOptions.RemoveEmptyEntries | StringSplitOptions.TrimEntries))
            yield return token;
    }
}

public sealed record ResolvedEscalationPoint(
    string Region,
    double Lat,
    double Lon,
    double Score,
    string RiskLevel,
    int MatchingEvents);
