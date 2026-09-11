$Base = Join-Path (Split-Path $PSScriptRoot -Parent) ".postgres"
$Bin = Join-Path $Base "pgsql\bin"
$Data = Join-Path $Base "data"
$Log = Join-Path $Base "postgres.log"
if (-not (Test-Path (Join-Path $Bin "pg_ctl.exe"))) {
    Write-Host "Once install_portable_postgres.ps1 calistirin." -ForegroundColor Yellow
    exit 1
}
& (Join-Path $Bin "pg_isready.exe") -h localhost -p 5432 -U kapasite -d kapasite 2>$null
if ($LASTEXITCODE -eq 0) {
    Write-Host "PostgreSQL zaten calisiyor."
    exit 0
}
& (Join-Path $Bin "pg_ctl.exe") -D $Data -l $Log start
Start-Sleep -Seconds 2
Write-Host "PostgreSQL baslatildi."
