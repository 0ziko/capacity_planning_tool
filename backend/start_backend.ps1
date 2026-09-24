# Backend'i arka planda, cikti dosyaya yazilarak baslatir (terminal kapansa da calisir).
# Kullanim:  .\start_backend.ps1            -> baslat (calisiyorsa once durdurur)
#            .\start_backend.ps1 -Stop      -> durdur
# Loglar: backend\logs\backend.err.log (uvicorn + hata izleri), backend\logs\backend.out.log
param([switch]$Stop, [switch]$Reload)
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot
$pidFile = "logs\backend.pid"
New-Item -ItemType Directory -Force logs | Out-Null

function Get-BackendProcessTree([int]$RootId, $Processes) {
    # Windows venv launcher -> Python reloader -> multiprocessing worker.
    # Stop descendants first; stopping just one level leaves the worker alive.
    foreach ($child in @($Processes | Where-Object { $_.ParentProcessId -eq $RootId })) {
        Get-BackendProcessTree -RootId $child.ProcessId -Processes $Processes
    }
    $RootId
}

if (Test-Path $pidFile) {
    $old = Get-Content $pidFile -ErrorAction SilentlyContinue
    if ($old) {
        $processes = @(Get-CimInstance Win32_Process)
        $rootProcess = $processes | Where-Object { $_.ProcessId -eq [int]$old }
        if ($rootProcess) {
            if ($rootProcess.ExecutablePath -ne (Join-Path $PSScriptRoot ".venv\Scripts\python.exe") -or $rootProcess.CommandLine -notmatch "-m uvicorn app\.main:app") {
                throw "Kayitli PID bu backend'e ait degil; guvenlik icin durdurulmadi: $old"
            }
            Get-BackendProcessTree -RootId ([int]$old) -Processes $processes | ForEach-Object {
                Stop-Process -Id $_ -Force -ErrorAction SilentlyContinue
            }
        }
    }
    Remove-Item $pidFile -ErrorAction SilentlyContinue
}
if ($Stop) { Write-Host "Backend durduruldu."; exit 0 }

if (Get-NetTCPConnection -LocalPort 8000 -State Listen -ErrorAction SilentlyContinue) {
    throw "8000 portu hala kullanimda. Eski veya baska bir sunucu kapatilmadan backend baslatilmadi."
}

if (-not (Test-Path ".venv")) { python -m venv .venv; .\.venv\Scripts\python.exe -m pip install -q -r requirements.txt }
if (-not (Test-Path ".env")) { Copy-Item .env.example .env }
$env:PYTHONIOENCODING = "utf-8"
# Stable local use does not need the file-watcher/reloader process.
# Opt into live source reload only during active development: -Reload.
$backendArgs = @("-m", "uvicorn", "app.main:app", "--port", "8000")
if ($Reload) { $backendArgs += @("--reload", "--reload-dir", "app", "--reload-delay", "0.5") }
$p = Start-Process -FilePath ".\.venv\Scripts\python.exe" `
    -ArgumentList $backendArgs `
    -WorkingDirectory $PSScriptRoot -WindowStyle Hidden -PassThru `
    -RedirectStandardOutput "logs\backend.out.log" -RedirectStandardError "logs\backend.err.log"
$p.Id | Set-Content $pidFile
$startupWatch = [System.Diagnostics.Stopwatch]::StartNew()
$startupError = "Sunucu henuz hazir degil."
while ($startupWatch.Elapsed.TotalSeconds -lt 30) {
    $p.Refresh()
    if ($p.HasExited) { throw "Backend baslatilirken kapandi; logs\backend.err.log dosyasina bakin." }
    try {
        $h = Invoke-RestMethod http://127.0.0.1:8000/api/health -TimeoutSec 5
        if ($h.db_ok) {
            Write-Host "Backend calisiyor (pid $($p.Id)): $($h.app) -> http://localhost:8000/docs"
            exit 0
        }
        $startupError = "Veritabani henuz hazir degil."
    } catch { $startupError = $_.Exception.Message }
    Start-Sleep -Milliseconds 500
}
throw "Backend 30 saniyede hazir olmadi; logs\backend.err.log dosyasina bakin. $startupError"
