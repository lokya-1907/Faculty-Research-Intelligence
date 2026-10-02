$ErrorActionPreference='Stop'
$root=(Get-Location).Path
if (-not (Test-Path '.\backend\.venv\Scripts\python.exe')) { throw 'Run .\setup_windows.ps1 first.' }
Start-Process powershell -ArgumentList '-NoExit','-Command',"Set-Location '$root\backend'; & '$root\backend\.venv\Scripts\python.exe' -m uvicorn app.main:app --host 127.0.0.1 --port 8000"
Start-Sleep -Seconds 2
Start-Process powershell -ArgumentList '-NoExit','-Command',"Set-Location '$root\frontend'; npm run dev"
Start-Sleep -Seconds 3
Start-Process 'http://127.0.0.1:5173'
