# ── Base runtime ─────────────────────────────────────────────────────────────
FROM mcr.microsoft.com/dotnet/aspnet:8.0 AS base
WORKDIR /app
EXPOSE 5000
ENV ASPNETCORE_HTTP_PORTS=5000

# Install curl for Docker healthcheck
RUN apt-get update && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

# ── Build stage ──────────────────────────────────────────────────────────────
FROM mcr.microsoft.com/dotnet/sdk:8.0 AS build
ARG BUILD_CONFIGURATION=Release
WORKDIR /src
COPY ["VisionI.API/VisionI.API.csproj", "VisionI.API/"]
RUN dotnet restore "./VisionI.API/VisionI.API.csproj"
COPY . .
WORKDIR "/src/VisionI.API"
RUN dotnet build "./VisionI.API.csproj" -c $BUILD_CONFIGURATION -o /app/build

# ── Publish stage ────────────────────────────────────────────────────────────
FROM build AS publish
ARG BUILD_CONFIGURATION=Release
RUN dotnet publish "./VisionI.API.csproj" -c $BUILD_CONFIGURATION -o /app/publish /p:UseAppHost=false

# ── Final stage ──────────────────────────────────────────────────────────────
FROM base AS final
WORKDIR /app
COPY --from=publish /app/publish .
ENTRYPOINT ["dotnet", "VisionI.API.dll"]
