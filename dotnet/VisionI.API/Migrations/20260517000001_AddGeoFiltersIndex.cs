using Microsoft.EntityFrameworkCore.Migrations;

#nullable disable

namespace VisionI.API.Migrations
{
    /// <inheritdoc />
    public partial class AddGeoFiltersIndex : Migration
    {
        /// <inheritdoc />
        protected override void Up(MigrationBuilder migrationBuilder)
        {
            migrationBuilder.CreateIndex(
                name: "IX_WorkspaceGeoFilters_WorkspaceId",
                table: "WorkspaceGeoFilters",
                column: "WorkspaceId");
        }

        /// <inheritdoc />
        protected override void Down(MigrationBuilder migrationBuilder)
        {
            migrationBuilder.DropIndex(
                name: "IX_WorkspaceGeoFilters_WorkspaceId",
                table: "WorkspaceGeoFilters");
        }
    }
}
