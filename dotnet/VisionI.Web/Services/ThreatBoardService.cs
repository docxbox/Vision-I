using VisionI.Web.Models;

namespace VisionI.Web.Services;

public sealed class ThreatBoardService
{
    private readonly ApiService _api;

    public ThreatBoardResponse? Board { get; private set; }
    public bool Loading { get; private set; } = true;
    public string Error { get; private set; } = "";
    public int Hours { get; set; } = 24;

    public string OverallLevel => Board?.OverallLevel ?? "monitoring";
    public List<ThreatZoneDto> Zones => Board?.Zones ?? new();
    public int CriticalCount => Board?.Summary.GetValueOrDefault("critical", 0) ?? 0;
    public int HighCount     => Board?.Summary.GetValueOrDefault("high", 0) ?? 0;
    public int MediumCount   => Board?.Summary.GetValueOrDefault("medium", 0) ?? 0;
    public int TotalZones    => Zones.Count;

    public event Action? OnChanged;

    public ThreatBoardService(ApiService api) => _api = api;

    private DateTime _lastLoaded = DateTime.MinValue;

    public async Task EnsureLoadedAsync()
    {
        if (DateTime.UtcNow - _lastLoaded < TimeSpan.FromMinutes(2)) return;
        await LoadAsync();
    }

    public async Task LoadAsync()
    {
        Loading = true;
        Error = "";
        OnChanged?.Invoke();
        try
        {
            Board = await _api.GetAsync<ThreatBoardResponse>($"api/threatboard?hours={Hours}");
            _lastLoaded = DateTime.UtcNow;
        }
        catch (Exception ex)
        {
            Error = ex.Message;
        }
        finally
        {
            Loading = false;
            OnChanged?.Invoke();
        }
    }

    public static string LevelClass(string? level) => level?.ToLower() switch
    {
        "critical"  => "crit",
        "high"      => "warn",
        "elevated"  => "warn",
        "medium"    => "info",
        "low"       => "ok",
        _           => "faint"
    };

    public static string TrendIcon(string? trend) => trend?.ToLower() switch
    {
        "rising"  => "trending_up",
        "falling" => "trending_down",
        "new"     => "fiber_new",
        _         => "trending_flat"
    };

    public static string TrendClass(string? trend) => trend?.ToLower() switch
    {
        "rising" => "warn",
        "new"    => "info",
        "falling"=> "ok",
        _        => "faint"
    };
}
