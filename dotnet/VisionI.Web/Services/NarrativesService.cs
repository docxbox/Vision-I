using System.Text.Json;
using VisionI.Web.Models;

namespace VisionI.Web.Services;

public sealed class NarrativesService
{
    private readonly ApiService _api;

    public UnrestWatchDto? Watch  { get; private set; }
    public JsonElement? Forecast  { get; private set; }
    public string ActiveId        { get; set; } = "";
    public string Topic           { get; set; } = "";
    public string Sev             { get; set; } = "";
    public bool Loading           { get; private set; } = true;
    public bool Detecting         { get; private set; }
    public string Error           { get; private set; } = "";
    public string WhatChangedSummary
    {
        get
        {
            var lead = LeadIndicator;
            if (lead is null)
                return "No narrative signals are currently visible.";

            return lead.ObservationSummary;
        }
    }
    public string WhyItMattersSummary =>
        LeadIndicator is { } lead
            ? lead.AssessmentSummary
            : Watch?.Overview is { } overview
            ? $"Unrest level is {overview.UnrestLevel}, with {overview.RisingNarratives} rising narrative(s) and {overview.WatchedActors} actor(s) worth tracking."
            : "Narratives show how messaging is spreading across actors and sources, which helps analysts spot coordinated influence activity before it becomes obvious in raw events alone.";
    public string WhatIsConnectedSummary =>
        LeadIndicator is { } lead
            ? lead.CorrelationSummary
            : $"{NarrativeCount} narrative items are in scope, plus {TopRegions.Count} hotspot region(s) and {TopActors.Count} actor watch item(s).";
    public string RecommendedActionSummary =>
        LeadIndicator?.RecommendedAction
            ?? Watch?.Overview?.RecommendedAction
            ?? "Review high-severity narratives first, then run forecast on the strongest ones to estimate whether amplification is likely to continue.";

    public int NarrativeCount => FilteredNarratives.Count;
    public List<UnrestNarrativeDto> FilteredNarratives => (Watch?.Narratives ?? new()).Where(Filter).ToList();
    public List<AnalystIndicatorDto> NarrativeIndicators => FilteredNarratives.Select(AnalystIndicatorFactory.FromNarrative).ToList();
    public AnalystIndicatorDto? LeadIndicator => NarrativeIndicators.FirstOrDefault();
    public List<UnrestRegionDto> TopRegions => Watch?.Regions.Take(6).ToList() ?? new();
    public List<UnrestActorDto> TopActors => Watch?.Actors.Take(6).ToList() ?? new();

    public event Action? OnChanged;

    public NarrativesService(ApiService api) => _api = api;

    private DateTime _lastLoaded = DateTime.MinValue;
    public async Task EnsureLoadedAsync()
    {
        if (DateTime.UtcNow - _lastLoaded < TimeSpan.FromMinutes(2)) return;
        _lastLoaded = DateTime.UtcNow;
        await LoadAsync();
    }

    public async Task LoadAsync()
    {
        Loading = true; Error = ""; Notify();
        try
        {
            Watch = await _api.GetUnrestWatchAsync();
        }
        catch (Exception ex) { Error = $"Failed to load narratives: {ex.Message}"; }
        finally { Loading = false; Notify(); }
    }

    public async Task DetectAsync()
    {
        Detecting = true; Notify();
        try { await _api.PostAsync<JsonElement?>("api/narratives/detect", new { }); await Task.Delay(1500); await LoadAsync(); }
        catch { }
        finally { Detecting = false; Notify(); }
    }

    public async Task ForecastAsync(string id)
    {
        ActiveId = id;
        try { Forecast = await _api.GetAsync<JsonElement?>($"api/narratives/{id}/forecast"); }
        catch { Forecast = null; }
        Notify();
    }

    public bool Filter(UnrestNarrativeDto narrative)
    {
        if (!string.IsNullOrEmpty(Sev))
        {
            if (!string.Equals(narrative.Severity, Sev, StringComparison.OrdinalIgnoreCase)) return false;
        }
        if (!string.IsNullOrEmpty(Topic)
            && !(narrative.Topic?.Contains(Topic, StringComparison.OrdinalIgnoreCase) ?? false)
            && !(narrative.TopRegion?.Contains(Topic, StringComparison.OrdinalIgnoreCase) ?? false)
            && !narrative.Actors.Any(a => a.Contains(Topic, StringComparison.OrdinalIgnoreCase)))
            return false;
        return true;
    }

    public static string SevCls(string s) => s.ToUpper() switch
    {
        "CRITICAL" or "HIGH" => "danger", "MEDIUM" => "warn", "LOW" => "accent", _ => ""
    };

    public IEnumerable<(string label, string value)> SummaryTiles()
    {
        if (Watch?.Overview is not { } overview) yield break;
        yield return ("UNREST LEVEL", overview.UnrestLevel.ToUpperInvariant());
        yield return ("PRESSURE", overview.OverallPressure.ToString("F2"));
        yield return ("RISING NARRATIVES", overview.RisingNarratives.ToString());
        yield return ("HOT REGIONS", overview.HotRegionCount.ToString());
        yield return ("CORROBORATED ALERTS", overview.CorroboratedAlerts.ToString());
        yield return ("WATCHED ACTORS", overview.WatchedActors.ToString());
    }

    private void Notify() => OnChanged?.Invoke();
}
