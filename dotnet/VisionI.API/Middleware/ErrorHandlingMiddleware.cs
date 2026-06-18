using System.Net;
using System.Text.Json;
using VisionI.API.Models.Responses;

namespace VisionI.API.Middleware;

/// <summary>
/// Global exception handler — converts unhandled exceptions to consistent JSON error responses.
/// Registered as the outermost middleware in Program.cs.
/// </summary>
public class ErrorHandlingMiddleware
{
    private readonly RequestDelegate _next;
    private readonly ILogger<ErrorHandlingMiddleware> _log;

    public ErrorHandlingMiddleware(RequestDelegate next, ILogger<ErrorHandlingMiddleware> log)
    {
        _next = next;
        _log = log;
    }

    public async Task InvokeAsync(HttpContext ctx)
    {
        try
        {
            await _next(ctx);
        }
        catch (Exception ex)
        {
            var traceId = System.Diagnostics.Activity.Current?.Id ?? ctx.TraceIdentifier;

            _log.LogError(ex, "Unhandled exception on {Method} {Path} [TraceId: {TraceId}]",
                ctx.Request.Method, ctx.Request.Path, traceId);

            ctx.Response.ContentType = "application/problem+json";
            ctx.Response.StatusCode = (int)HttpStatusCode.InternalServerError;

            var env = ctx.RequestServices.GetRequiredService<IHostEnvironment>();
            var error = new
            {
                type = "https://visioni.local/docs/errors/internal",
                title = "An unexpected error occurred.",
                status = 500,
                detail = env.IsDevelopment() ? ex.Message : "The server encountered an unexpected condition that prevented it from fulfilling the request.",
                traceId = traceId,
                instance = ctx.Request.Path
            };

            await ctx.Response.WriteAsync(
                JsonSerializer.Serialize(error, new JsonSerializerOptions
                {
                    PropertyNamingPolicy = JsonNamingPolicy.CamelCase
                })
            );
        }
    }
}