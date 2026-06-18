using System.Text.Json;
using VisionI.Web.Models;

namespace VisionI.Web.Services;

/// <summary>
/// Singleton data cache that holds one copy of live platform state shared across all
/// circuits. Contains NO auth token and NO hub connection (those live in the
/// scoped ViLiveSession). Any circuit can read state; only ViLiveSession writes it.
/// </summary>
public sealed class ViStateService
{
    private readonly ILogger<ViStateService> _log;

    public List<EventDto>          Events     { get; private set; } = new();
    public List<EventDto>          LiveEvents { get; private set; } = new();
    public List<AssetDto>          Assets     { get; private set; } = new();
    public List<AlertDto>          Alerts     { get; private set; } = new();
    public DashboardOverviewDto    Overview   { get; private set; } = new();
    public EscalationResponse      Escalation { get; private set; } = new();
    public (int Aircraft, int Vessels) Counts { get; private set; }
    public string   JarvisInsight { get; private set; } = "";
    public DateTime LastUpdated   { get; private set; } = DateTime.UtcNow;

    public event Action? OnStateChanged;

    public void NotifyChanged() => OnStateChanged?.Invoke();

    public ViStateService(ILogger<ViStateService> log) => _log = log;

    public void SetEvents(List<EventDto> list, List<EventDto> live)
    {
        Events     = OrderEvents(list, 2000);
        LiveEvents = OrderEvents(live, 300);
        LastUpdated = DateTime.UtcNow;
    }

    /// <summary>
    /// Incremental merge: fold a delta of newly-ingested events into the store instead of
    /// replacing it. Delta listed first so newer copies win the dedupe in OrderEvents.
    /// </summary>
    public void AppendEvents(List<EventDto> delta)
    {
        if (delta is null || delta.Count == 0) return;
        Events     = OrderEvents(delta.Concat(Events),     2000);
        LiveEvents = OrderEvents(delta.Concat(LiveEvents), 300);
        LastUpdated = DateTime.UtcNow;
    }

    public void PatchAssets(List<AssetDto> incoming, (int Aircraft, int Vessels) counts)
    {
        var dict = Assets.ToDictionary(a => a.AssetId ?? "", a => a);
        foreach (var a in incoming.Where(a => !string.IsNullOrEmpty(a.AssetId)))
            dict[a.AssetId!] = a;
        Assets = dict.Values
            .OrderByDescending(IsAssetAnomalous)
            .ThenByDescending(a => ParseTs(a.LastSeen))
            .ThenByDescending(a => string.Equals(a.AssetType, "vessel", StringComparison.OrdinalIgnoreCase))
            .ThenBy(a => a.Callsign ?? a.AssetId)
            .Take(30000).ToList();
        Counts      = counts;
        LastUpdated = DateTime.UtcNow;
    }

    public void SetOverview(DashboardOverviewDto overview, EscalationResponse escalation)
    {
        Overview   = overview;
        Escalation = escalation;
        Alerts     = overview.RecentAlerts ?? new();
        if (!string.IsNullOrEmpty(overview.JarvisInsight))
            JarvisInsight = overview.JarvisInsight;
        if (Events.Count == 0)
        {
            var ev = overview.Events ?? new();
            var lv = overview.LiveEvents ?? new();
            SetEvents(ev.Count > 0 ? ev : lv, lv);
        }
        LastUpdated = DateTime.UtcNow;
    }

    public void SetJarvisInsight(string msg) { JarvisInsight = msg; }

    public static bool IsAssetAnomalous(AssetDto a) =>
        (a.LastSpeed > 500) ||
        (a.AssetType == "aircraft" && (a.LastAltitude ?? 9999) < 1000 && (a.LastAltitude ?? 0) > 0);

    private static List<EventDto> OrderEvents(IEnumerable<EventDto> src, int take) =>
        src.Where(e => !string.IsNullOrWhiteSpace(e.EventId))
           .GroupBy(e => e.EventId!, StringComparer.OrdinalIgnoreCase)
           .Select(g => g.OrderByDescending(e => e.RiskScore ?? 0)
                         .ThenByDescending(e => e.InfluenceScore ?? 0)
                         .ThenByDescending(e => ParseTs(e.Timestamp))
                         .First())
           .OrderByDescending(e => e.RiskScore ?? 0)
           .ThenByDescending(e => e.InfluenceScore ?? 0)
           .ThenByDescending(e => ParseTs(e.Timestamp))
           .Take(take).ToList();

    private static DateTime ParseTs(string? v) =>
        DateTime.TryParse(v, out var d) ? d : DateTime.MinValue;
}

