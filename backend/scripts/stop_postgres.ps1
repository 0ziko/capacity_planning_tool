$Base = Join-Path (Split-Path $PSScriptRoot -Parent) ".postgres"
$Bin = Join-Path $Base "pgsql\bin"
$Data = Join-Path $Base "data"
if (Test-Path (Join-Path $Bin "pg_ctl.exe")) {
    & (Join-Path $Bin "pg_ctl.exe") -D $Data stop
    Write-Host "PostgreSQL durduruldu."
}
