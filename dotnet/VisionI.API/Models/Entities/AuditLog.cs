namespace VisionI.API.Models.Entities;

/// <summary>
/// Immutable audit trail — one row per significant user action.
/// Written by controllers; never updated or deleted.
/// </summary>
public class AuditLog
{
    public long     Id        { get; set; }
    public string   UserId    { get; set; } = string.Empty;
    public string   Action    { get; set; } = string.Empty;   // e.g. "role.change", "user.suspend"
    public string   Resource  { get; set; } = string.Empty;   // e.g. "user:{id}"
    public DateTime Timestamp { get; set; } = DateTime.UtcNow;
    public string?  IpAddress { get; set; }
    public string?  Detail    { get; set; }                   // optional JSON payload
}
