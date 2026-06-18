using System.IO;
using System.Net.Http.Headers;
using Microsoft.AspNetCore.Antiforgery;
using Microsoft.AspNetCore.DataProtection;
using MudBlazor.Services;
using VisionI.Web.Components;
using VisionI.Web.Services;

var builder = WebApplication.CreateBuilder(args);

var internalApiBase = (
    builder.Configuration["InternalApiBaseUrl"]
    ?? builder.Configuration["ApiBaseUrl"]
    ?? "http://localhost:5000/"
).TrimEnd('/');

var dataProtectionPath = builder.Configuration["DataProtection:KeyPath"];
if (string.IsNullOrWhiteSpace(dataProtectionPath))
{
    dataProtectionPath = OperatingSystem.IsWindows()
        ? Path.Combine(builder.Environment.ContentRootPath, ".data-protection")
        : "/root/.aspnet/DataProtection-Keys";
}

Directory.CreateDirectory(dataProtectionPath);

builder.Services
    .AddDataProtection()
    .PersistKeysToFileSystem(new DirectoryInfo(dataProtectionPath))
    .SetApplicationName("VisionI.Web");

// Short timeout keeps the UI responsive if upstream calls stall.
builder.Services.AddHttpClient("api", c =>
{
    c.BaseAddress = new Uri(internalApiBase + "/");
    c.Timeout     = TimeSpan.FromSeconds(18);
});

// Fleet snapshots can legitimately be large on cold start (20k+ vessels,
// 2k+ aircraft). Keep this separate so normal UI calls still fail fast.
builder.Services.AddHttpClient("fleet", c =>
{
    c.BaseAddress = new Uri(internalApiBase + "/");
    c.Timeout     = TimeSpan.FromSeconds(120);
});

// Longer timeout for proxying hubs and streaming responses.
builder.Services.AddHttpClient("proxy", c =>
{
    c.BaseAddress  = new Uri(internalApiBase + "/");
    c.Timeout      = TimeSpan.FromMinutes(5);
});

builder.Services.AddScoped<AuthService>();

builder.Services.AddScoped<ApiService>(sp => new ApiService(
    sp.GetRequiredService<IHttpClientFactory>().CreateClient("api"),
    sp.GetRequiredService<AuthService>(),
    sp.GetRequiredService<ILogger<ApiService>>()));

builder.Services.AddSingleton<RegionGeoService>();

// Per-circuit state cache (scoped) — prevents cross-user data leakage.
builder.Services.AddScoped<ViStateService>();
builder.Services.AddScoped<ViLiveSession>();
builder.Services.AddScoped<ToastService>();
builder.Services.AddScoped<EventsService>();
builder.Services.AddScoped<HomeService>();
builder.Services.AddScoped<AlertsService>();
builder.Services.AddScoped<MapService>();
builder.Services.AddScoped<GraphService>();
builder.Services.AddScoped<EntityService>();
builder.Services.AddScoped<EventDetailService>();
builder.Services.AddScoped<SignalsService>();
builder.Services.AddScoped<CopilotService>();
builder.Services.AddScoped<NarrativesService>();
builder.Services.AddScoped<InfluenceService>();
builder.Services.AddScoped<DecisionsService>();
builder.Services.AddScoped<PlaybooksService>();
builder.Services.AddScoped<IngestService>();
builder.Services.AddScoped<SourcesService>();
builder.Services.AddScoped<StreamsService>();
builder.Services.AddScoped<OntologyService>();
builder.Services.AddScoped<SentimentService>();
builder.Services.AddScoped<MissionsService>();
builder.Services.AddScoped<OperationsService>();
builder.Services.AddScoped<TriageService>();
builder.Services.AddScoped<AdminUsersService>();
builder.Services.AddScoped<AdminJobsService>();
builder.Services.AddScoped<AdminDlqService>();
builder.Services.AddScoped<AdminLlmService>();
builder.Services.AddScoped<AdminStatsService>();
builder.Services.AddScoped<AdminQueriesService>();
builder.Services.AddScoped<AdminHealthService>();
builder.Services.AddScoped<AdminAuditService>();
builder.Services.AddScoped<WorkspaceService>();
builder.Services.AddScoped<ThreatBoardService>();
builder.Services.AddScoped<AirspaceService>();
builder.Services.AddMudServices();


builder.Services.AddRazorComponents()
    .AddInteractiveServerComponents()
    .AddHubOptions(options =>
    {
        options.ClientTimeoutInterval    = TimeSpan.FromSeconds(60);
        options.KeepAliveInterval        = TimeSpan.FromSeconds(15);
        options.HandshakeTimeout         = TimeSpan.FromSeconds(30);
        options.MaximumReceiveMessageSize = 32 * 1024 * 1024;
    });

var app = builder.Build();

if (!app.Environment.IsDevelopment())
{
    app.UseExceptionHandler("/Error", createScopeForErrors: true);
    app.UseHsts();
}

app.UseStaticFiles();
// Proxy browser traffic through the web app so the browser never calls the API directly.
app.Map("/api/{**rest}", async (HttpContext ctx, IHttpClientFactory factory, string? rest) =>
{
    await ProxyAsync(ctx, factory, $"api/{rest ?? ""}");
});

app.Map("/visionHub/{**rest}", async (HttpContext ctx, IHttpClientFactory factory, string? rest) =>
{
    await ProxyAsync(ctx, factory, $"visionHub/{rest ?? ""}");
});

app.Map("/health", async (HttpContext ctx, IHttpClientFactory factory) =>
{
    await ProxyAsync(ctx, factory, "health");
});

app.Use(async (context, next) =>
{
    try { await next(); }
    catch (AntiforgeryValidationException)
    {
        foreach (var cookie in context.Request.Cookies.Keys.Where(IsAntiforgeryCookie))
            context.Response.Cookies.Delete(cookie, new CookieOptions { Path = "/" });

        if (!context.Response.HasStarted)
            context.Response.Redirect($"{context.Request.PathBase}{context.Request.Path}{context.Request.QueryString}");
    }
});

app.UseAntiforgery();

app.MapRazorComponents<App>()
    .AddInteractiveServerRenderMode();

app.Run();
static async Task ProxyAsync(HttpContext ctx, IHttpClientFactory factory, string target)
{
    var client = factory.CreateClient("proxy");
    var qs     = ctx.Request.QueryString.Value ?? "";
    var url    = $"{target}{qs}";

    var proxyReq = new HttpRequestMessage(new HttpMethod(ctx.Request.Method), url);

    // Forward the request body for write methods.
    var method = ctx.Request.Method.ToUpperInvariant();
    if (method is "POST" or "PUT" or "PATCH" or "DELETE")
    {
        ctx.Request.EnableBuffering();
        var bodyStream = new MemoryStream();
        await ctx.Request.Body.CopyToAsync(bodyStream);
        bodyStream.Position = 0;
        proxyReq.Content = new StreamContent(bodyStream);
        if (ctx.Request.ContentType is { } ct)
            proxyReq.Content.Headers.ContentType = MediaTypeHeaderValue.Parse(ct);
    }

    // Forward request headers that are safe to proxy.
    foreach (var h in ctx.Request.Headers)
    {
        if (h.Key.Equals("Host", StringComparison.OrdinalIgnoreCase)) continue;
        if (h.Key.Equals("Transfer-Encoding", StringComparison.OrdinalIgnoreCase)) continue;
        try { proxyReq.Headers.TryAddWithoutValidation(h.Key, (IEnumerable<string?>)h.Value.ToArray()); } catch { }
    }

    try
    {
        using var resp = await client.SendAsync(proxyReq, HttpCompletionOption.ResponseHeadersRead, ctx.RequestAborted);

        ctx.Response.StatusCode = (int)resp.StatusCode;

        // Forward upstream response headers.
        foreach (var h in resp.Headers)
        {
            if (h.Key.Equals("Transfer-Encoding", StringComparison.OrdinalIgnoreCase)) continue;
            ctx.Response.Headers[h.Key] = h.Value.ToArray();
        }
        foreach (var h in resp.Content.Headers)
            ctx.Response.Headers[h.Key] = h.Value.ToArray();

        await resp.Content.CopyToAsync(ctx.Response.Body, ctx.RequestAborted);
    }
    catch (TaskCanceledException)
    {
        if (!ctx.Response.HasStarted)
        {
            // Use 499 for client disconnects and 504 for upstream timeouts.
            ctx.Response.StatusCode = ctx.RequestAborted.IsCancellationRequested ? 499 : 504;
        }
    }
    catch (Exception ex)
    {
        if (!ctx.Response.HasStarted)
        {
            ctx.Response.StatusCode = 502;
            await ctx.Response.WriteAsync($"Gateway error: {ex.Message}");
        }
    }
}

static bool IsAntiforgeryCookie(string n) =>
    n.StartsWith(".AspNetCore.Antiforgery", StringComparison.OrdinalIgnoreCase) ||
    n.Contains("Antiforgery", StringComparison.OrdinalIgnoreCase) ||
    n.Equals("__RequestVerificationToken", StringComparison.OrdinalIgnoreCase);
