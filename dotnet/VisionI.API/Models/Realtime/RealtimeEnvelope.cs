using System.Text.Json;

namespace VisionI.API.Models.Realtime;

public static class RealtimeGroups
{
    public const string All = "all";
    public const string IntelligenceStream = "intelligence_stream";
}

public static class RealtimeMethods
{
    // Single stable SignalR method name for realtime delivery.
    public const string RealtimeEvent = "RealtimeEvent";
}

public enum RealtimeEventType
{
    IngestComplete,
    IntelligenceUpdate,
    CorrelationUpdate,
    CompositeEventDetected,
    AssetStreamUpdate,
}

public sealed record RealtimeEnvelope(
    int V,
    RealtimeEventType Type,
    string Ts,
    JsonElement Data,
    string? Source = null,
    string? TraceId = null
);

