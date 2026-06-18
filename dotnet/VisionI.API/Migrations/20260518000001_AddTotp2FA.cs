using Microsoft.EntityFrameworkCore.Migrations;

#nullable disable

namespace VisionI.API.Migrations
{
    public partial class AddTotp2FA : Migration
    {
        protected override void Up(MigrationBuilder migrationBuilder)
        {
            migrationBuilder.AddColumn<string>(
                name: "TotpSecret",
                table: "AspNetUsers",
                type: "character varying(256)",
                maxLength: 256,
                nullable: true);

            migrationBuilder.AddColumn<bool>(
                name: "TotpEnabled",
                table: "AspNetUsers",
                type: "boolean",
                nullable: false,
                defaultValue: false);
        }

        protected override void Down(MigrationBuilder migrationBuilder)
        {
            migrationBuilder.DropColumn(name: "TotpSecret",  table: "AspNetUsers");
            migrationBuilder.DropColumn(name: "TotpEnabled", table: "AspNetUsers");
        }
    }
}
