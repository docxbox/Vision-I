using Microsoft.AspNetCore.Identity.EntityFrameworkCore;
using Microsoft.EntityFrameworkCore;
using VisionI.API.Models.Entities;
namespace VisionI.API.Infrastructure;

/// <summary>
/// Application database context.
/// Inherits IdentityDbContext so ASP.NET Identity tables (users, roles, claims…)
/// are created automatically alongside our own tables.
/// </summary>
public class AppDbContext : IdentityDbContext<AppUser>
{
    public AppDbContext(DbContextOptions<AppDbContext> options) : base(options) { }

    public DbSet<RefreshToken> RefreshTokens => Set<RefreshToken>();
    public DbSet<AuditLog>     AuditLogs     => Set<AuditLog>();
    public DbSet<LlmProviderConfig> LlmProviderConfigs => Set<LlmProviderConfig>();
    public DbSet<EventTriageRecord> EventTriageRecords => Set<EventTriageRecord>();
    public DbSet<Workspace> Workspaces => Set<Workspace>();
    public DbSet<WorkspaceGeoFilter> WorkspaceGeoFilters => Set<WorkspaceGeoFilter>();
    public DbSet<WorkspaceQuery> WorkspaceQueries => Set<WorkspaceQuery>();
    public DbSet<WorkspaceEntity> WorkspaceEntities => Set<WorkspaceEntity>();
    public DbSet<WorkspaceSourceProfile> WorkspaceSourceProfiles => Set<WorkspaceSourceProfile>();
    public DbSet<WorkspaceSnapshot> WorkspaceSnapshots => Set<WorkspaceSnapshot>();
    public DbSet<WorkspaceDecisionContext> WorkspaceDecisionContexts => Set<WorkspaceDecisionContext>();
    public DbSet<WorkspaceTask> WorkspaceTasks => Set<WorkspaceTask>();
    public DbSet<WorkspaceEvidence> WorkspaceEvidence => Set<WorkspaceEvidence>();

    protected override void OnModelCreating(ModelBuilder builder)
    {
        base.OnModelCreating(builder);

        // RefreshToken
        builder.Entity<RefreshToken>(e =>
        {
            e.HasKey(t => t.Id);
            e.HasIndex(t => t.Token).IsUnique();
            e.HasIndex(t => t.UserId);
            e.HasOne(t => t.User)
             .WithMany()
             .HasForeignKey(t => t.UserId)
             .OnDelete(DeleteBehavior.Cascade);
        });

        // AuditLog — append-only, no FK cascade needed
        builder.Entity<AuditLog>(e =>
        {
            e.HasKey(a => a.Id);
            e.HasIndex(a => a.UserId);
            e.HasIndex(a => a.Timestamp);
            e.Property(a => a.Id).UseIdentityColumn();
        });

        builder.Entity<LlmProviderConfig>(e =>
        {
            e.HasKey(c => c.Id);
            e.HasIndex(c => c.Provider);
            e.HasIndex(c => c.IsDefault);
            e.Property(c => c.Provider).HasMaxLength(32);
            e.Property(c => c.Model).HasMaxLength(200);
            e.Property(c => c.BaseUrl).HasMaxLength(500);
        });

        builder.Entity<Workspace>(e =>
        {
            e.HasKey(w => w.Id);
            e.HasIndex(w => w.Slug).IsUnique();
            e.HasIndex(w => w.Status);
            e.Property(w => w.Slug).HasMaxLength(128);
            e.Property(w => w.Title).HasMaxLength(256);
            e.Property(w => w.Status).HasMaxLength(32);
            e.Property(w => w.Classification).HasMaxLength(64);
            e.Property(w => w.Theme).HasMaxLength(64);
            e.Property(w => w.CreatedBy).HasMaxLength(128);
            e.Property(w => w.Visibility).HasMaxLength(32).HasDefaultValue("private");
        });

        builder.Entity<WorkspaceTask>(e =>
        {
            e.HasKey(t => t.Id);
            e.HasIndex(t => t.WorkspaceId);
            e.HasIndex(t => t.Status);
            e.HasOne(t => t.Workspace).WithMany(w => w.Tasks).HasForeignKey(t => t.WorkspaceId);
            e.Property(t => t.Title).HasMaxLength(256);
            e.Property(t => t.Status).HasMaxLength(32);
            e.Property(t => t.Priority).HasMaxLength(32);
            e.Property(t => t.CreatedByUserId).HasMaxLength(128);
            e.Property(t => t.AssigneeUserId).HasMaxLength(128);
            e.Property(t => t.AssigneeDisplayName).HasMaxLength(256);
        });

        builder.Entity<WorkspaceEvidence>(e =>
        {
            e.HasKey(x => x.Id);
            e.HasIndex(x => x.WorkspaceId);
            // Dedupe: the same item can only be pinned once per workspace.
            e.HasIndex(x => new { x.WorkspaceId, x.ItemType, x.ItemId }).IsUnique();
            // No Workspace.Evidence nav collection on purpose — keeps it out of the
            // multi-Include GetBySlugAsync query (cartesian explosion).
            e.HasOne(x => x.Workspace).WithMany().HasForeignKey(x => x.WorkspaceId);
            e.Property(x => x.ItemType).HasMaxLength(32);
            e.Property(x => x.ItemId).HasMaxLength(256);
            e.Property(x => x.Title).HasMaxLength(512);
            e.Property(x => x.Source).HasMaxLength(128);
            e.Property(x => x.PinnedByUserId).HasMaxLength(128);
            e.Property(x => x.PinnedByDisplayName).HasMaxLength(256);
        });

        builder.Entity<WorkspaceGeoFilter>(e =>
        {
            e.HasKey(f => f.Id);
            e.HasOne(f => f.Workspace).WithMany(w => w.GeoFilters).HasForeignKey(f => f.WorkspaceId);
        });

        builder.Entity<WorkspaceQuery>(e =>
        {
            e.HasKey(q => q.Id);
            e.HasIndex(q => q.WorkspaceId);
            e.HasOne(q => q.Workspace).WithMany(w => w.Queries).HasForeignKey(q => q.WorkspaceId);
            e.Property(q => q.Query).HasMaxLength(512);
        });

        builder.Entity<WorkspaceEntity>(e =>
        {
            e.HasKey(we => we.Id);
            e.HasIndex(we => we.WorkspaceId);
            e.HasOne(we => we.Workspace).WithMany(w => w.Entities).HasForeignKey(we => we.WorkspaceId);
            e.Property(we => we.EntityKey).HasMaxLength(256);
            e.Property(we => we.DisplayName).HasMaxLength(256);
        });

        builder.Entity<WorkspaceSourceProfile>(e =>
        {
            e.HasKey(s => s.Id);
            e.HasIndex(s => s.WorkspaceId);
            e.HasOne(s => s.Workspace).WithMany(w => w.SourceProfiles).HasForeignKey(s => s.WorkspaceId);
            e.Property(s => s.SourceName).HasMaxLength(64);
        });

        builder.Entity<WorkspaceSnapshot>(e =>
        {
            e.HasKey(s => s.Id);
            e.HasIndex(s => new { s.WorkspaceId, s.SnapshotType, s.WindowHours });
            e.Property(s => s.SnapshotType).HasMaxLength(32);
        });

        builder.Entity<WorkspaceDecisionContext>(e =>
        {
            e.HasKey(d => d.Id);
            e.HasIndex(d => d.WorkspaceId);
            e.Property(d => d.EventId).HasMaxLength(128);
        });

        builder.Entity<EventTriageRecord>(e =>
        {
            e.HasKey(t => t.Id);
            e.HasIndex(t => t.EventId).IsUnique();
            e.HasIndex(t => t.Status);
            e.HasIndex(t => t.Priority);
            e.HasIndex(t => t.AnalystUserId);
            e.HasIndex(t => t.UpdatedAt);
            e.Property(t => t.EventId).HasMaxLength(128);
            e.Property(t => t.Title).HasMaxLength(512);
            e.Property(t => t.Source).HasMaxLength(64);
            e.Property(t => t.EventType).HasMaxLength(128);
            e.Property(t => t.Status).HasMaxLength(32);
            e.Property(t => t.Priority).HasMaxLength(32);
            e.Property(t => t.AnalystUserId).HasMaxLength(128);
            e.Property(t => t.AnalystDisplayName).HasMaxLength(256);
            e.Property(t => t.SourceUrl).HasMaxLength(1024);
            e.Property(t => t.Region).HasMaxLength(256);
        });
    }
}
