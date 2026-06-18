using VisionI.Web.Models;

namespace VisionI.Web.Services;

public sealed class AirspaceService
{
    private readonly ApiService _api;

    public AirspaceClosuresResponse?   Closures    { get; private set; }
    public JammingHeatmapResponse?     Jamming     { get; private set; }
    public ReroutesResponse?           Reroutes    { get; private set; }
    public SatellitePassesResponse?    Satellites  { get; private set; }

    public bool   Loading { get; private set; } = true;
    public string Error   { get; private set; } = "";

    public event Action? OnChanged;

    public AirspaceService(ApiService api) => _api = api;

    private DateTime _lastLoaded = DateTime.MinValue;

    public async Task EnsureLoadedAsync()
    {
        if (DateTime.UtcNow - _lastLoaded < TimeSpan.FromMinutes(1)) return;
        await LoadAsync();
    }

    public async Task LoadAsync(bool silent = false)
    {
        if (!silent)
            Loading = true;
        Error   = "";
        if (!silent)
            OnChanged?.Invoke();

        try
        {
            await Task.WhenAll(
                _api.GetAirspaceAsync()      .ContinueWith(t => { if (!t.IsFaulted) Closures   = t.Result; }),
                _api.GetJammingHeatmapAsync().ContinueWith(t => { if (!t.IsFaulted) Jamming    = t.Result; }),
                _api.GetReroutesAsync()      .ContinueWith(t => { if (!t.IsFaulted) Reroutes   = t.Result; }),
                _api.GetSatellitePassesAsync().ContinueWith(t => { if (!t.IsFaulted) Satellites = t.Result; })
            );
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

    public static string StatusClass(string? status) => status?.ToLower() switch
    {
        "active"   => "crit",
        "pending"  => "warn",
        "expired"  => "faint",
        _          => "info"
    };

    public static string IntensityClass(double? intensity) => intensity switch
    {
        >= 0.7 => "crit",
        >= 0.4 => "warn",
        _      => "info"
    };
}
