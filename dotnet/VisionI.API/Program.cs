using Microsoft.AspNetCore.Authentication.JwtBearer;
using Microsoft.AspNetCore.Identity;
using Microsoft.AspNetCore.RateLimiting;
using Microsoft.EntityFrameworkCore;
using Microsoft.IdentityModel.Tokens;
using Microsoft.OpenApi.Models;
using StackExchange.Redis;
using System.Text;
using System.Threading.RateLimiting;
using VisionI.API.Hubs;
using VisionI.API.Infrastructure;
using VisionI.API.Middleware;
using VisionI.API.Models;
using VisionI.API.Models.Entities;
using VisionI.API.Repositories;
using VisionI.API.Services;
using Serilog;
using Serilog.Events;
using OpenTelemetry.Resources;
using OpenTelemetry.Trace;

var builder = WebApplication.CreateBuilder(args);

// Structured application logging.
Log.Logger = new LoggerConfiguration()
    .MinimumLevel.Information()
    .MinimumLevel.Override("Microsoft", LogEventLevel.Warning)
    .MinimumLevel.Override("System", LogEventLevel.Warning)
    .Enrich.FromLogContext()
    .WriteTo.Console(outputTemplate: "[{Timestamp:HH:mm:ss} {Level:u3}] {Message:lj} {Properties:j}{NewLine}{Exception}")
    .CreateLogger();

builder.Host.UseSerilog();

// Validate required configuration early.
var jwtKey = builder.Configuration["Jwt:Key"]
    ?? throw new InvalidOperationException(
        "Jwt:Key is missing. Set the Jwt__Key environment variable.");

if (jwtKey.Length < 32)
    throw new InvalidOperationException("Jwt:Key must be at least 32 characters.");

// OpenTelemetry tracing.
builder.Services.AddOpenTelemetry()
    .WithTracing(tracerProviderBuilder =>
    {
        tracerProviderBuilder
            .SetResourceBuilder(ResourceBuilder.CreateDefault().AddService("VisionI.API"))
            .AddAspNetCoreInstrumentation()
            .AddHttpClientInstrumentation()
            .AddConsoleExporter();
    });

// Database and identity.
builder.Services.AddDbContext<AppDbContext>(options =>
{
    options.UseNpgsql(builder.Configuration.GetConnectionString("DefaultConnection"));
    if (builder.Environment.IsDevelopment())
        options.EnableSensitiveDataLogging().EnableDetailedErrors();
});

builder.Services.AddIdentity<AppUser, IdentityRole>(options =>
{
    options.Password.RequireDigit = true;
    options.Password.RequiredLength = 8;
    options.Password.RequireUppercase = true;
    options.Password.RequireLowercase = true;
    options.Password.RequireNonAlphanumeric = false;
    options.User.RequireUniqueEmail = true;
    options.Lockout.DefaultLockoutTimeSpan = TimeSpan.FromMinutes(15);
    options.Lockout.MaxFailedAccessAttempts = 5;
    options.Lockout.AllowedForNewUsers = true;
})
.AddEntityFrameworkStores<AppDbContext>()
.AddDefaultTokenProviders();

// JWT authentication.
builder.Services
    .AddAuthentication(options =>
    {
        options.DefaultAuthenticateScheme = JwtBearerDefaults.AuthenticationScheme;
        options.DefaultChallengeScheme = JwtBearerDefaults.AuthenticationScheme;
    })
    .AddJwtBearer(options =>
    {
        options.TokenValidationParameters = new TokenValidationParameters
        {
            ValidateIssuerSigningKey = true,
            IssuerSigningKey = new SymmetricSecurityKey(Encoding.UTF8.GetBytes(jwtKey)),
            ValidateIssuer = true,
            ValidIssuer = builder.Configuration["Jwt:Issuer"],
            ValidateAudience = true,
            ValidAudience = builder.Configuration["Jwt:Audience"],
            ValidateLifetime = true,
            ClockSkew = TimeSpan.Zero,
        };

        // SignalR sends the access token in the query string.
        options.Events = new JwtBearerEvents
        {
            OnMessageReceived = ctx =>
            {
                var token = ctx.Request.Query["access_token"];
                var path = ctx.HttpContext.Request.Path;
                if (!string.IsNullOrEmpty(token) && path.StartsWithSegments("/hubs"))
                    ctx.Token = token;
                return Task.CompletedTask;
            }
        };
    });

builder.Services.AddAuthorization();

// CORS.
var allowedOrigins = builder.Configuration.GetSection("Cors:AllowedOrigins")
    .Get<string[]>() ?? Array.Empty<string>();

var corsOrigins = new List<string>(allowedOrigins);
if (builder.Environment.IsDevelopment())
{
    corsOrigins.AddRange(new[]
    {
        "http://localhost:5001",
        "https://localhost:5001",
        "http://localhost:5297",
        "https://localhost:7187",
        "http://localhost:5125",
        "https://localhost:7224",
    });
}

builder.Services.AddCors(options =>
{
    options.AddPolicy("AllowBlazorClient", policy =>
    {
        policy.WithOrigins(corsOrigins.Distinct().ToArray())
            .AllowAnyMethod()
            .AllowAnyHeader()
            .AllowCredentials();   // Needed for SignalR and the refresh cookie.
    });
});

// Rate limiting.
builder.Services.AddRateLimiter(options =>
{
    // Default per-IP limit.
    options.GlobalLimiter = PartitionedRateLimiter.Create<HttpContext, string>(ctx =>
        RateLimitPartition.GetFixedWindowLimiter(
            ctx.Connection.RemoteIpAddress?.ToString() ?? "anon",
            _ => new FixedWindowRateLimiterOptions
            {
                PermitLimit = 200,
                Window = TimeSpan.FromMinutes(1),
                QueueProcessingOrder = QueueProcessingOrder.OldestFirst,
                QueueLimit = 10,
            }));

    // Tighter limit for auth endpoints.
    options.AddFixedWindowLimiter("auth", opt =>
    {
        opt.PermitLimit = 10;
        opt.Window = TimeSpan.FromMinutes(1);
        opt.QueueProcessingOrder = QueueProcessingOrder.OldestFirst;
        opt.QueueLimit = 2;
    });

    // Lower limit for heavier ingest requests.
    options.AddFixedWindowLimiter("ingest", opt =>
    {
        opt.PermitLimit = 5;
        opt.Window = TimeSpan.FromMinutes(1);
        opt.QueueProcessingOrder = QueueProcessingOrder.OldestFirst;
        opt.QueueLimit = 1;
    });

    // Higher limit for read-heavy UI queries.
    options.AddFixedWindowLimiter("query", opt =>
    {
        opt.PermitLimit = 120;
        opt.Window = TimeSpan.FromMinutes(1);
        opt.QueueProcessingOrder = QueueProcessingOrder.OldestFirst;
        opt.QueueLimit = 20;
    });

    options.RejectionStatusCode = 429;
    options.OnRejected = async (ctx, ct) =>
    {
        ctx.HttpContext.Response.Headers["Retry-After"] = "60";
        await ctx.HttpContext.Response.WriteAsJsonAsync(new
        {
            error = "RATE_LIMITED",
            message = "Too many requests. Please wait before retrying.",
            retry_after_seconds = 60,
        }, ct);
    };
});

// Caching and SignalR.
builder.Services.AddMemoryCache(); // Used when Redis is unavailable.

// Redis cache and pub/sub.
var redisConnString = builder.Configuration["Redis:ConnectionString"] ?? "redis:6379";
try
{
    var redisConnection = ConnectionMultiplexer.Connect(redisConnString);
    builder.Services.AddSingleton<IConnectionMultiplexer>(redisConnection);
    builder.Services.AddStackExchangeRedisCache(options =>
    {
        options.ConnectionMultiplexerFactory = () => Task.FromResult<IConnectionMultiplexer>(redisConnection);
        options.InstanceName = "vision:";
    });
    builder.Services.AddSingleton<RedisCacheService>();
    builder.Services.AddHostedService<RedisSubscriptionService>();
}
catch (Exception ex)
{
    // Redis unavailable — fall back to in-memory cache only
    var startupLogger = LoggerFactory.Create(b => b.AddConsole()).CreateLogger("Startup");
    startupLogger.LogWarning(ex, "Redis unavailable at {Connection} — using in-memory cache only", redisConnString);
    builder.Services.AddDistributedMemoryCache();
    builder.Services.AddSingleton<RedisCacheService>();
}

builder.Services.AddSignalR(opts => opts.EnableDetailedErrors = builder.Environment.IsDevelopment());

// Python API clients.
var pythonBase = builder.Configuration["PythonApi:BaseUrl"] ?? "http://localhost:8000";
var pythonApiKey = builder.Configuration["PythonApi:ApiKey"] ?? "";
var timeoutSec = int.Parse(builder.Configuration["PythonApi:TimeoutSeconds"] ?? "30");

void ConfigurePythonHttpClient(HttpClient client)
{
    client.BaseAddress = new Uri(pythonBase);
    client.Timeout = TimeSpan.FromSeconds(timeoutSec);

    // Python uses service-to-service auth, not the end-user token.
    if (!string.IsNullOrEmpty(pythonApiKey))
        client.DefaultRequestHeaders.Add("X-Internal-Key", pythonApiKey);
}

SocketsHttpHandler CreatePythonHandler()
    => new()
    {
        AutomaticDecompression = System.Net.DecompressionMethods.GZip | System.Net.DecompressionMethods.Deflate,
        PooledConnectionLifetime = TimeSpan.FromMinutes(5),
        PooledConnectionIdleTimeout = TimeSpan.FromMinutes(2),
        MaxConnectionsPerServer = 200,
    };

// Named client for passthrough controllers.
builder.Services.AddHttpClient(nameof(PythonApiClient), ConfigurePythonHttpClient)
    .ConfigurePrimaryHttpMessageHandler(CreatePythonHandler);

var interfaceHttpBuilder = builder.Services
    .AddHttpClient<IIntelligenceClient, PythonApiClient>(ConfigurePythonHttpClient)
    .ConfigurePrimaryHttpMessageHandler(CreatePythonHandler);

// Typed client for regular orchestrator calls.
var httpBuilder = builder.Services
    .AddHttpClient<PythonApiClient>(ConfigurePythonHttpClient)
    .ConfigurePrimaryHttpMessageHandler(CreatePythonHandler);

try
{
    interfaceHttpBuilder.AddStandardResilienceHandler(options =>
    {
        options.AttemptTimeout.Timeout = TimeSpan.FromSeconds(timeoutSec);
        options.TotalRequestTimeout.Timeout = TimeSpan.FromSeconds(timeoutSec + 30);
        options.CircuitBreaker.SamplingDuration = TimeSpan.FromMinutes(5);
        options.CircuitBreaker.FailureRatio = 0.9;
        options.CircuitBreaker.MinimumThroughput = 10;
        options.Retry.MaxRetryAttempts = 1;
    });

    httpBuilder.AddStandardResilienceHandler(options =>
    {
        // Keep the resilience timeout aligned with HttpClient.
        options.AttemptTimeout.Timeout = TimeSpan.FromSeconds(timeoutSec);
        options.TotalRequestTimeout.Timeout = TimeSpan.FromSeconds(timeoutSec + 30);
        // Python can be slow on cold starts, so keep the breaker permissive.
        options.CircuitBreaker.SamplingDuration = TimeSpan.FromMinutes(5);
        options.CircuitBreaker.FailureRatio = 0.9;
        options.CircuitBreaker.MinimumThroughput = 10;
        // POST replay protection is handled inside PythonApiClient.
        options.Retry.MaxRetryAttempts = 1;
    });
}
catch { /* Polly package not installed — works without retry */ }

// Application services.
builder.Services.AddScoped<ITokenService, TokenService>();
builder.Services.AddScoped<IIntelligenceRepository, IntelligenceRepository>();
builder.Services.AddScoped<IIntelligenceService, IntelligenceService>();
builder.Services.AddScoped<SourceCatalogService>();
builder.Services.AddScoped<ITriageRepository, TriageRepository>();
builder.Services.AddScoped<ITriageService, TriageService>();
builder.Services.AddScoped<IWorkspaceRepository, WorkspaceRepository>();
builder.Services.AddScoped<IWorkspaceComposerService, WorkspaceComposerService>();
builder.Services.AddSingleton<LlmConfigCryptoService>();
// NativeLlmService is now a thin client to the Python LLM gateway (one provider impl).
builder.Services.AddScoped<INativeLlmService, NativeLlmService>();
builder.Services.AddControllers()
    .AddJsonOptions(opts =>
    {
        opts.JsonSerializerOptions.PropertyNameCaseInsensitive = true;
        opts.JsonSerializerOptions.PropertyNamingPolicy = System.Text.Json.JsonNamingPolicy.CamelCase;
    });
builder.Services.AddEndpointsApiExplorer();

builder.Services.AddSingleton<StartupWarmupState>();
builder.Services.AddHostedService<ApiWarmupService>();

// Health checks.
builder.Services.AddHealthChecks()
    .AddCheck<StartupWarmupHealthCheck>("startup_ready");

// Swagger in development.
if (builder.Environment.IsDevelopment())
{
    builder.Services.AddSwaggerGen(c =>
    {
        c.SwaggerDoc("v1", new OpenApiInfo { Title = "VisionI API", Version = "v1" });
        c.AddSecurityDefinition("Bearer", new OpenApiSecurityScheme
        {
            Name = "Authorization",
            Type = SecuritySchemeType.Http,
            Scheme = "bearer",
            BearerFormat = "JWT",
            In = ParameterLocation.Header,
        });
        c.AddSecurityRequirement(new OpenApiSecurityRequirement
        {
            {
                new OpenApiSecurityScheme
                {
                    Reference = new OpenApiReference { Type = ReferenceType.SecurityScheme, Id = "Bearer" }
                },
                Array.Empty<string>()
            }
        });
    });
}

// HTTP request logging.
builder.Services.AddHttpLogging(logging =>
{
    logging.LoggingFields = Microsoft.AspNetCore.HttpLogging.HttpLoggingFields.RequestPropertiesAndHeaders
                          | Microsoft.AspNetCore.HttpLogging.HttpLoggingFields.ResponseStatusCode
                          | Microsoft.AspNetCore.HttpLogging.HttpLoggingFields.Duration;
    logging.RequestHeaders.Add("X-Request-ID");
    logging.ResponseHeaders.Add("X-Process-Time-Ms");
    // Keep auth headers out of logs.
    logging.MediaTypeOptions.AddText("application/json");
    logging.RequestBodyLogLimit = 0;
    logging.ResponseBodyLogLimit = 0;
});

var app = builder.Build();

// Prepare the database at startup.
await using (var scope = app.Services.CreateAsyncScope())
{
    var db = scope.ServiceProvider.GetRequiredService<AppDbContext>();
    var logger = scope.ServiceProvider.GetRequiredService<ILogger<Program>>();
    try
    {
        var hasMigrations = db.Database.GetMigrations().Any();
        if (hasMigrations)
        {
            await db.Database.MigrateAsync();
        }
        else if (app.Environment.IsDevelopment())
        {
            // Dev-only fallback: no migrations present, create schema from model
            logger.LogWarning("No EF migrations found — using EnsureCreatedAsync (dev only)");
            await db.Database.EnsureCreatedAsync();
        }
        else
        {
            throw new InvalidOperationException(
                "No EF migrations found. Run 'dotnet ef database update' before starting in non-development environments.");
        }
        logger.LogInformation("Database schema ready");

        // Idempotent schema patches for columns/tables not yet covered by a migration.
        // Safe to run on every startup — IF NOT EXISTS / DO NOTHING guards.
        await db.Database.ExecuteSqlRawAsync("""
            ALTER TABLE "AspNetUsers"
                ADD COLUMN IF NOT EXISTS "TotpEnabled" BOOLEAN NOT NULL DEFAULT FALSE,
                ADD COLUMN IF NOT EXISTS "TotpSecret"  VARCHAR(256);

            ALTER TABLE "Workspaces"
                ADD COLUMN IF NOT EXISTS "Visibility" VARCHAR(32) NOT NULL DEFAULT 'private';

            CREATE TABLE IF NOT EXISTS "WorkspaceTasks" (
                "Id"                  UUID        NOT NULL DEFAULT gen_random_uuid() PRIMARY KEY,
                "WorkspaceId"         UUID        NOT NULL REFERENCES "Workspaces"("Id") ON DELETE CASCADE,
                "Title"               TEXT        NOT NULL,
                "Description"         TEXT,
                "Status"              VARCHAR(32) NOT NULL DEFAULT 'open',
                "Priority"            VARCHAR(32) NOT NULL DEFAULT 'medium',
                "CreatedByUserId"     TEXT,
                "AssigneeUserId"      TEXT,
                "AssigneeDisplayName" TEXT,
                "CreatedAt"           TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                "UpdatedAt"           TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                "CompletedAt"         TIMESTAMPTZ
            );
            CREATE INDEX IF NOT EXISTS "IX_WorkspaceTasks_WorkspaceId" ON "WorkspaceTasks"("WorkspaceId");

            CREATE TABLE IF NOT EXISTS "WorkspaceEvidence" (
                "Id"                  UUID         NOT NULL DEFAULT gen_random_uuid() PRIMARY KEY,
                "WorkspaceId"         UUID         NOT NULL REFERENCES "Workspaces"("Id") ON DELETE CASCADE,
                "ItemType"            VARCHAR(32)  NOT NULL DEFAULT 'event',
                "ItemId"              VARCHAR(256) NOT NULL,
                "Title"               VARCHAR(512) NOT NULL DEFAULT '',
                "Source"              VARCHAR(128),
                "Note"                TEXT,
                "PinnedByUserId"      VARCHAR(128),
                "PinnedByDisplayName" VARCHAR(256),
                "CreatedAt"           TIMESTAMPTZ  NOT NULL DEFAULT NOW()
            );
            CREATE INDEX IF NOT EXISTS "IX_WorkspaceEvidence_WorkspaceId" ON "WorkspaceEvidence"("WorkspaceId");
            CREATE UNIQUE INDEX IF NOT EXISTS "UX_WorkspaceEvidence_Item"
                ON "WorkspaceEvidence"("WorkspaceId", "ItemType", "ItemId");
            """);

        // Trigram indexes for the workspace resolvers' ILIKE %term% search (events table is
        // owned by the Python tier). Best-effort + non-fatal: the events table may not exist
        // yet on a brand-new DB, and index builds shouldn't block API startup.
        try
        {
            await db.Database.ExecuteSqlRawAsync("""
                CREATE EXTENSION IF NOT EXISTS pg_trgm;
                CREATE INDEX IF NOT EXISTS ix_events_title_trgm  ON events USING gin (title gin_trgm_ops);
                CREATE INDEX IF NOT EXISTS ix_events_desc_trgm   ON events USING gin (description gin_trgm_ops);
                CREATE INDEX IF NOT EXISTS ix_events_body_trgm   ON events USING gin (body gin_trgm_ops);
                CREATE INDEX IF NOT EXISTS ix_events_loc_trgm    ON events USING gin (location_name gin_trgm_ops);
                CREATE INDEX IF NOT EXISTS ix_events_actors_trgm ON events USING gin ((actors::text) gin_trgm_ops);
                CREATE INDEX IF NOT EXISTS ix_events_tags_trgm   ON events USING gin ((tags::text) gin_trgm_ops);
                CREATE INDEX IF NOT EXISTS ix_events_extras_trgm ON events USING gin ((extras::text) gin_trgm_ops);
                CREATE INDEX IF NOT EXISTS ix_events_source_trgm ON events USING gin (source gin_trgm_ops);
                CREATE INDEX IF NOT EXISTS ix_events_etype_trgm  ON events USING gin (event_type gin_trgm_ops);
                CREATE INDEX IF NOT EXISTS ix_events_author_trgm ON events USING gin (author gin_trgm_ops);
                """);
            logger.LogInformation("Event trigram search indexes ensured");
        }
        catch (Exception ex)
        {
            logger.LogWarning(ex, "Could not create event trigram indexes (events table may not exist yet)");
        }
        logger.LogInformation("Schema patches applied");

        // Seed Iran / Strait of Hormuz workspace
        var hormuzExists = await db.Workspaces.AnyAsync(w => w.Slug == "iran-strait-of-hormuz");
        if (!hormuzExists)
        {
            var hormuz = new VisionI.API.Models.Entities.Workspace
            {
                Id = Guid.NewGuid(),
                Slug = "iran-strait-of-hormuz",
                Title = "Iran / Strait of Hormuz",
                Description = "Track maritime, aviation, geopolitical, and social developments related to Iran and the Strait of Hormuz.",
                Status = "active",
                Classification = "UNCLASSIFIED",
                DefaultWindowHours = 24,
                CreatedBy = "system",
                CreatedAt = DateTime.UtcNow,
                UpdatedAt = DateTime.UtcNow,
            };
            db.Workspaces.Add(hormuz);
            await db.SaveChangesAsync();

            db.WorkspaceGeoFilters.Add(new VisionI.API.Models.Entities.WorkspaceGeoFilter
            {
                Id = Guid.NewGuid(), WorkspaceId = hormuz.Id,
                FilterType = "bbox", Name = "primary",
                MinLat = 24.0, MaxLat = 28.5, MinLon = 54.0, MaxLon = 58.8,
                CreatedAt = DateTime.UtcNow,
            });

            var queries = new[] { "Iran", "Strait of Hormuz", "IRGC", "Bandar Abbas",
                "Hormuz shipping", "Gulf tanker", "Iranian navy", "Oman Strait of Hormuz", "UAE Iran shipping" };
            int priority = 10;
            foreach (var q in queries)
            {
                db.WorkspaceQueries.Add(new VisionI.API.Models.Entities.WorkspaceQuery
                {
                    Id = Guid.NewGuid(), WorkspaceId = hormuz.Id, Query = q,
                    Priority = priority, IsActive = true,
                    CreatedAt = DateTime.UtcNow, UpdatedAt = DateTime.UtcNow,
                });
                priority += 10;
            }

            var entities = new[] { ("Iran", "country"), ("IRGC", "organization"),
                ("Bandar Abbas", "location"), ("Strait of Hormuz", "location"),
                ("U.S. Fifth Fleet", "organization"), ("Oman", "country"), ("UAE", "country") };
            bool first = true;
            foreach (var (name, type) in entities)
            {
                db.WorkspaceEntities.Add(new VisionI.API.Models.Entities.WorkspaceEntity
                {
                    Id = Guid.NewGuid(), WorkspaceId = hormuz.Id,
                    EntityKey = name.ToLower().Replace(" ", "_"), EntityType = type,
                    DisplayName = name, IsPrimary = first, CreatedAt = DateTime.UtcNow,
                });
                first = false;
            }

            var enabledSources = new[] { "news", "gdelt", "rss", "reddit", "youtube", "opensky", "ais" };
            var disabledSources = new[] { "hackernews", "stocks", "crypto", "weather", "who" };
            foreach (var s in enabledSources)
                db.WorkspaceSourceProfiles.Add(new VisionI.API.Models.Entities.WorkspaceSourceProfile
                    { Id = Guid.NewGuid(), WorkspaceId = hormuz.Id, SourceName = s, IsEnabled = true, CreatedAt = DateTime.UtcNow });
            foreach (var s in disabledSources)
                db.WorkspaceSourceProfiles.Add(new VisionI.API.Models.Entities.WorkspaceSourceProfile
                    { Id = Guid.NewGuid(), WorkspaceId = hormuz.Id, SourceName = s, IsEnabled = false, CreatedAt = DateTime.UtcNow });

            await db.SaveChangesAsync();
            logger.LogInformation("Seeded Iran / Strait of Hormuz workspace");
        }

        // Seed default roles.
        var roleManager = scope.ServiceProvider.GetRequiredService<RoleManager<IdentityRole>>();
        foreach (var role in new[] { "Viewer", "Analyst", "Admin" })
        {
            if (!await roleManager.RoleExistsAsync(role))
                await roleManager.CreateAsync(new IdentityRole(role));
        }
        logger.LogInformation("Roles seeded");

        // Seed the default admin account when configured.
        var userManager = scope.ServiceProvider.GetRequiredService<UserManager<AppUser>>();
        var adminEmail = builder.Configuration["Seed:AdminEmail"];
        var adminPass  = builder.Configuration["Seed:AdminPassword"];
        if (!string.IsNullOrWhiteSpace(adminEmail) && !string.IsNullOrWhiteSpace(adminPass))
        {
            if (await userManager.FindByEmailAsync(adminEmail) == null)
            {
                var admin = new AppUser
                {
                    UserName    = adminEmail,
                    Email       = adminEmail,
                    DisplayName = "System Admin",
                };
                var created = await userManager.CreateAsync(admin, adminPass);
                if (created.Succeeded)
                {
                    await userManager.AddToRoleAsync(admin, "Admin");
                    logger.LogInformation("Default admin account seeded: {Email}", adminEmail);
                }
            }
            else
            {
                // Fix the role if the account already exists.
                var admin = await userManager.FindByEmailAsync(adminEmail);
                if (admin != null && !(await userManager.IsInRoleAsync(admin, "Admin")))
                {
                    await userManager.RemoveFromRoleAsync(admin, "Viewer");
                    await userManager.AddToRoleAsync(admin, "Admin");
                    logger.LogInformation("Existing user promoted to Admin: {Email}", adminEmail);
                }
            }
        }
        else
        {
            logger.LogInformation("Admin seed skipped because Seed:AdminEmail or Seed:AdminPassword is not configured");
        }
    }
    catch (Exception ex)
    {
        logger.LogCritical(ex, "Database schema migration FAILED — workspace and other features will be unavailable. Run 'docker-compose down -v && docker-compose up -d' to reset the database volume.");
        throw;
    }
}

// Middleware order matters here.
app.UseMiddleware<ErrorHandlingMiddleware>();
app.UseMiddleware<CorrelationIdMiddleware>();
app.UseMiddleware<SecurityHeadersMiddleware>();

app.UseRateLimiter();

if (app.Environment.IsDevelopment())
{
    app.UseSwagger();
    app.UseSwaggerUI();
}

// Force HTTPS in production.
if (app.Environment.IsProduction())
    app.UseHsts();

app.UseHttpLogging();
app.UseCors("AllowBlazorClient");
app.UseAuthentication();
app.UseAuthorization();

app.MapControllers();
app.MapHub<EventHub>("/hubs/events");
app.MapHub<VisionHub>("/visionHub");

// Lightweight unauthenticated health endpoint.
app.MapHealthChecks("/health", new Microsoft.AspNetCore.Diagnostics.HealthChecks.HealthCheckOptions
{
    ResponseWriter = async (ctx, report) =>
    {
        ctx.Response.ContentType = "application/json";
        var result = System.Text.Json.JsonSerializer.Serialize(new
        {
            status = report.Status.ToString().ToLower(),
            timestamp = DateTime.UtcNow,
            checks = report.Entries.Select(e => new
            {
                name = e.Key,
                status = e.Value.Status.ToString().ToLower(),
                description = e.Value.Description,
            }),
        });
        await ctx.Response.WriteAsync(result);
    }
});

app.MapGet("/metrics", () =>
{
    var uptimeSeconds = (DateTime.UtcNow - System.Diagnostics.Process.GetCurrentProcess().StartTime.ToUniversalTime()).TotalSeconds;
    var payload = string.Join('\n', new[]
    {
        "# HELP visioni_dotnet_api_up Vision-I .NET API liveness.",
        "# TYPE visioni_dotnet_api_up gauge",
        "visioni_dotnet_api_up 1",
        "# HELP visioni_dotnet_api_uptime_seconds Process uptime in seconds.",
        "# TYPE visioni_dotnet_api_uptime_seconds gauge",
        $"visioni_dotnet_api_uptime_seconds {Math.Round(uptimeSeconds, 0)}",
    });
    return Results.Text(payload, "text/plain");
}).AllowAnonymous();

app.Run();
