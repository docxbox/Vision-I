using System.Data;
using Microsoft.EntityFrameworkCore;
using Microsoft.Extensions.Diagnostics.HealthChecks;
using VisionI.API.Infrastructure;

namespace VisionI.API.Services;

public sealed class StartupWarmupState
{
    public volatile bool IsReady;
    public DateTimeOffset? CompletedAtUtc;
    public string? LastError;
}

public sealed class StartupWarmupHealthCheck : IHealthCheck
{
    private readonly StartupWarmupState _state;

    public StartupWarmupHealthCheck(StartupWarmupState state)
    {
        _state = state;
    }

    public Task<HealthCheckResult> CheckHealthAsync(HealthCheckContext context, CancellationToken cancellationToken = default)
    {
        if (_state.IsReady)
        {
            return Task.FromResult(HealthCheckResult.Healthy("startup warmup complete"));
        }

        return Task.FromResult(HealthCheckResult.Unhealthy(
            description: _state.LastError ?? "startup warmup in progress"));
    }
}

public sealed class ApiWarmupService : BackgroundService
{
    private readonly IServiceProvider _services;
    private readonly StartupWarmupState _state;
    private readonly ILogger<ApiWarmupService> _log;

    public ApiWarmupService(
        IServiceProvider services,
        StartupWarmupState state,
        ILogger<ApiWarmupService> log)
    {
        _services = services;
        _state = state;
        _log = log;
    }

    protected override async Task ExecuteAsync(CancellationToken stoppingToken)
    {
        try
        {
            await using var scope = _services.CreateAsyncScope();
            var db = scope.ServiceProvider.GetRequiredService<AppDbContext>();
            var intelligence = scope.ServiceProvider.GetRequiredService<IIntelligenceService>();
            var sources = scope.ServiceProvider.GetRequiredService<SourceCatalogService>();

            await PrimeDbAsync(db, stoppingToken);
            await sources.BuildCatalogJsonAsync(stoppingToken);
            await sources.BuildOverviewSourceHealthJsonAsync(stoppingToken);

            // Prime the common precomputed keys so first overview/dashboard reads
            // don't pay the direct Redis lookup cost on the critical request path.
            _ = await intelligence.GetPrecomputedJsonAsync("precomputed:dashboard_summary", stoppingToken);
            _ = await intelligence.GetPrecomputedJsonAsync("precomputed:live_streams", stoppingToken);

            _state.IsReady = true;
            _state.CompletedAtUtc = DateTimeOffset.UtcNow;
            _state.LastError = null;
            _log.LogInformation("API startup warmup complete");
        }
        catch (OperationCanceledException) when (stoppingToken.IsCancellationRequested)
        {
        }
        catch (Exception ex)
        {
            _state.LastError = ex.Message;
            _log.LogError(ex, "API startup warmup failed");
        }
    }

    private static async Task PrimeDbAsync(AppDbContext db, CancellationToken ct)
    {
        await db.Database.OpenConnectionAsync(ct);
        var conn = db.Database.GetDbConnection();

        await ExecuteAsync(conn, "SELECT 1", ct);
        await ExecuteAsync(conn, "SELECT COUNT(*) FROM events", ct);
        await ExecuteAsync(conn, "SELECT COUNT(*) FROM alerts", ct);
        await ExecuteAsync(conn, "SELECT COUNT(*) FROM narratives", ct);
        await ExecuteAsync(conn, "SELECT COUNT(*) FROM source_checkpoints", ct);
        await ExecuteAsync(conn, """
            SELECT event_id, title, source, event_type, timestamp
            FROM events
            ORDER BY timestamp DESC NULLS LAST, ingest_time DESC NULLS LAST
            LIMIT 3
            """, ct);
        await db.Database.CloseConnectionAsync();
    }

    private static async Task ExecuteAsync(System.Data.Common.DbConnection conn, string sql, CancellationToken ct)
    {
        await using var cmd = conn.CreateCommand();
        cmd.CommandText = sql;
        _ = await cmd.ExecuteScalarAsync(ct);
    }
}
