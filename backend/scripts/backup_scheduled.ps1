# Scheduled backup wrapper. Register with Windows Task Scheduler, e.g.
#   schtasks /Create /TN "ResearchPulse Backup" /SC DAILY /ST 02:00 ^
#     /TR "powershell -ExecutionPolicy Bypass -File <path>\backup_scheduled.ps1"
param(
    [int]$Retention = 14,
    [string]$BackendEnv = "$PSScriptRoot\..\.env"
)
$ErrorActionPreference = 'Stop'
$backend = (Resolve-Path "$PSScriptRoot\..").Path
$python = Join-Path $backend '.venv\Scripts\python.exe'
if (-not (Test-Path $python)) { throw "Python venv not found at $python" }

$code = @"
import sys
sys.path.insert(0, r'$backend')
from app.services import extras_db
from app.services import maintenance
maintenance.DEFAULT_RETENTION = $Retention
print(extras_db.backup_database())
print('pruned:', len(extras_db.prune_backups(keep=$Retention)))
"@
$code | & $python -
