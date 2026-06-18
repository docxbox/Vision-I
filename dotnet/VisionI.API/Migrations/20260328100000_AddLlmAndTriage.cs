using System;
using Microsoft.EntityFrameworkCore.Migrations;
using Npgsql.EntityFrameworkCore.PostgreSQL.Metadata;

#nullable disable

namespace VisionI.API.Migrations
{
    /// <inheritdoc />
    public partial class AddLlmAndTriage : Migration
    {
        /// <inheritdoc />
        protected override void Up(MigrationBuilder migrationBuilder)
        {
            migrationBuilder.CreateTable(
                name: "LlmProviderConfigs",
                columns: table => new
                {
                    Id = table.Column<int>(type: "integer", nullable: false)
                        .Annotation("Npgsql:ValueGenerationStrategy", NpgsqlValueGenerationStrategy.IdentityByDefaultColumn),
                    Provider = table.Column<string>(type: "character varying(32)", maxLength: 32, nullable: false),
                    Model = table.Column<string>(type: "character varying(200)", maxLength: 200, nullable: false),
                    BaseUrl = table.Column<string>(type: "character varying(500)", maxLength: 500, nullable: true),
                    EncryptedApiKey = table.Column<string>(type: "text", nullable: false),
                    IsEnabled = table.Column<bool>(type: "boolean", nullable: false, defaultValue: true),
                    IsDefault = table.Column<bool>(type: "boolean", nullable: false, defaultValue: false),
                    UpdatedByUserId = table.Column<string>(type: "text", nullable: false, defaultValue: ""),
                    UpdatedAt = table.Column<DateTime>(type: "timestamp with time zone", nullable: false),
                    LastTestedAt = table.Column<DateTime>(type: "timestamp with time zone", nullable: true),
                    LastTestSucceeded = table.Column<bool>(type: "boolean", nullable: true),
                    LastTestMessage = table.Column<string>(type: "text", nullable: true)
                },
                constraints: table =>
                {
                    table.PrimaryKey("PK_LlmProviderConfigs", x => x.Id);
                });

            migrationBuilder.CreateIndex(
                name: "IX_LlmProviderConfigs_IsDefault",
                table: "LlmProviderConfigs",
                column: "IsDefault");

            migrationBuilder.CreateIndex(
                name: "IX_LlmProviderConfigs_Provider",
                table: "LlmProviderConfigs",
                column: "Provider");

            migrationBuilder.CreateTable(
                name: "EventTriageRecords",
                columns: table => new
                {
                    Id = table.Column<int>(type: "integer", nullable: false)
                        .Annotation("Npgsql:ValueGenerationStrategy", NpgsqlValueGenerationStrategy.IdentityByDefaultColumn),
                    EventId = table.Column<string>(type: "character varying(128)", maxLength: 128, nullable: false),
                    Title = table.Column<string>(type: "character varying(512)", maxLength: 512, nullable: false, defaultValue: ""),
                    Source = table.Column<string>(type: "character varying(64)", maxLength: 64, nullable: false, defaultValue: ""),
                    EventType = table.Column<string>(type: "character varying(128)", maxLength: 128, nullable: false, defaultValue: ""),
                    Status = table.Column<string>(type: "character varying(32)", maxLength: 32, nullable: false, defaultValue: "new"),
                    Priority = table.Column<string>(type: "character varying(32)", maxLength: 32, nullable: false, defaultValue: "medium"),
                    RiskScore = table.Column<double>(type: "double precision", nullable: true),
                    ConfidenceScore = table.Column<double>(type: "double precision", nullable: true),
                    AnalystUserId = table.Column<string>(type: "character varying(128)", maxLength: 128, nullable: true),
                    AnalystDisplayName = table.Column<string>(type: "character varying(256)", maxLength: 256, nullable: true),
                    Note = table.Column<string>(type: "text", nullable: true),
                    SourceUrl = table.Column<string>(type: "character varying(1024)", maxLength: 1024, nullable: true),
                    Region = table.Column<string>(type: "character varying(256)", maxLength: 256, nullable: true),
                    SimilarEventCount = table.Column<int>(type: "integer", nullable: false, defaultValue: 0),
                    RelatedActorCount = table.Column<int>(type: "integer", nullable: false, defaultValue: 0),
                    LastSeenAt = table.Column<DateTime>(type: "timestamp with time zone", nullable: false),
                    CreatedAt = table.Column<DateTime>(type: "timestamp with time zone", nullable: false),
                    UpdatedAt = table.Column<DateTime>(type: "timestamp with time zone", nullable: false)
                },
                constraints: table =>
                {
                    table.PrimaryKey("PK_EventTriageRecords", x => x.Id);
                });

            migrationBuilder.CreateIndex(
                name: "IX_EventTriageRecords_EventId",
                table: "EventTriageRecords",
                column: "EventId",
                unique: true);

            migrationBuilder.CreateIndex(
                name: "IX_EventTriageRecords_Priority",
                table: "EventTriageRecords",
                column: "Priority");

            migrationBuilder.CreateIndex(
                name: "IX_EventTriageRecords_Status",
                table: "EventTriageRecords",
                column: "Status");

            migrationBuilder.CreateIndex(
                name: "IX_EventTriageRecords_AnalystUserId",
                table: "EventTriageRecords",
                column: "AnalystUserId");

            migrationBuilder.CreateIndex(
                name: "IX_EventTriageRecords_UpdatedAt",
                table: "EventTriageRecords",
                column: "UpdatedAt");
        }

        /// <inheritdoc />
        protected override void Down(MigrationBuilder migrationBuilder)
        {
            migrationBuilder.DropTable(name: "EventTriageRecords");
            migrationBuilder.DropTable(name: "LlmProviderConfigs");
        }
    }
}
