# Backend'i arka planda, cikti dosyaya yazilarak baslatir (terminal kapansa da calisir).
# Kullanim:  .\start_backend.ps1            -> baslat (calisiyorsa once durdurur)
#            .\start_backend.ps1 -Stop      -> durdur
# Loglar: backend\logs\backend.err.log (uvicorn + hata izleri), backend\logs\backend.out.log
param([switch]$Stop)
Set-Location $PSScriptRoot
$pidFile = "logs\backend.pid"
New-Item -ItemType Directory -Force logs | Out-Null

if (Test-Path $pidFile) {
    $old = Get-Content $pidFile -ErrorAction SilentlyContinue
    if ($old) {
        # reloader + alt surecleri
        Get-CimInstance Win32_Process | Where-Object { $_.ParentProcessId -eq [int]$old } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
        Stop-Process -Id ([int]$old) -Force -ErrorAction SilentlyContinue
    }
    Remove-Item $pidFile -ErrorAction SilentlyContinue
}
if ($Stop) { Write-Host "Backend durduruldu."; exit 0 }

if (-not (Test-Path ".venv")) { python -m venv .venv; .\.venv\Scripts\python.exe -m pip install -q -r requirements.txt }
if (-not (Test-Path ".env")) { Copy-Item .env.example .env }
$env:PYTHONIOENCODING = "utf-8"
$p = Start-Process -FilePath ".\.venv\Scripts\python.exe" `
    -ArgumentList "-m", "uvicorn", "app.main:app", "--port", "8000", "--reload" `
    -WorkingDirectory $PSScriptRoot -WindowStyle Hidden -PassThru `
    -RedirectStandardOutput "logs\backend.out.log" -RedirectStandardError "logs\backend.err.log"
$p.Id | Set-Content $pidFile
Start-Sleep -Seconds 4
try {
    $h = Invoke-RestMethod http://localhost:8000/api/health -TimeoutSec 10
    Write-Host "Backend calisiyor (pid $($p.Id)): $($h.app) -> http://localhost:8000/docs"
} catch {
    Write-Host "Backend yanit vermedi; logs\backend.err.log dosyasina bakin." -ForegroundColor Red
}
