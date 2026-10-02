param(
    [string]$OutputDirectory = "$PSScriptRoot\..\backups"
)

$database = Join-Path $PSScriptRoot '..\data\research_intelligence.db'
if (-not (Test-Path $database)) {
    throw "Database not found: $database"
}

New-Item -ItemType Directory -Force -Path $OutputDirectory | Out-Null
$timestamp = Get-Date -Format 'yyyyMMdd-HHmmss'
$destination = Join-Path $OutputDirectory "research_intelligence-$timestamp.db"
Copy-Item -Path $database -Destination $destination
Write-Output "Backup created: $destination"
