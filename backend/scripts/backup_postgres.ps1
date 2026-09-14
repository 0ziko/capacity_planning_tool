# PostgreSQL tam yedek (custom format pg_dump). Sifre loga yazilmaz.
# Rol/GRANT ayarlari ayri ele alinmalidir — yalnizca sema+veri.

$ErrorActionPreference = "Stop"
$BackendRoot = Split-Path $PSScriptRoot -Parent
Set-Location $BackendRoot

$PgBin = Join-Path $BackendRoot ".postgres\pgsql\bin"
$DumpExe = Join-Path $PgBin "pg_dump.exe"
if (-not (Test-Path $DumpExe)) {
    throw "Portable PostgreSQL bulunamadi. Once backend/scripts/install_portable_postgres.ps1 calistirin."
}

$EnvFile = Join-Path $BackendRoot ".env"
if (-not (Test-Path $EnvFile)) {
    throw ".env bulunamadi: $EnvFile"
}

function Get-DatabaseUrl {
    $line = Get-Content $EnvFile -Encoding UTF8 | Where-Object { $_ -match '^\s*DATABASE_URL\s*=' } | Select-Object -First 1
    if (-not $line) { throw "DATABASE_URL .env icinde yok" }
    return ($line -replace '^\s*DATABASE_URL\s*=\s*', '').Trim().Trim('"')
}

function Parse-PgUrl([string]$Url) {
    $u = $Url -replace '^postgresql\+\w+://', 'postgresql://'
    if ($u -notmatch '^postgresql://([^:/@]+)(?::([^@]*))?@([^:/]+)(?::(\d+))?/([^?]+)') {
        throw "DATABASE_URL cozulemedi (postgresql bekleniyor)"
    }
    return @{
        User = $Matches[1]
        Password = $Matches[2]
        Host = $Matches[3]
        Port = if ($Matches[4]) { $Matches[4] } else { "5432" }
        Database = $Matches[5]
    }
}

$pg = Parse-PgUrl (Get-DatabaseUrl)
$OutDir = Join-Path $BackendRoot "backup"
New-Item -ItemType Directory -Force -Path $OutDir | Out-Null
$Stamp = Get-Date -Format "yyyyMMdd_HHmmss"
$DumpFile = Join-Path $OutDir "kapasite_$Stamp.dump"

$env:PGPASSWORD = $pg.Password
try {
    & $DumpExe -Fc -h $pg.Host -p $pg.Port -U $pg.User -d $pg.Database -f $DumpFile --no-owner --no-acl
    if ($LASTEXITCODE -ne 0) { throw "pg_dump cikis kodu $LASTEXITCODE" }
}
finally {
    Remove-Item Env:PGPASSWORD -ErrorAction SilentlyContinue
}

$size = (Get-Item $DumpFile).Length
$manifest = @{
    created_at = (Get-Date).ToUniversalTime().ToString("o")
    format     = "pg_custom"
    source_db  = $pg.Database
    host       = $pg.Host
    port       = $pg.Port
    dump_file  = (Split-Path $DumpFile -Leaf)
    size_bytes = $size
    validation = "pending"
    note       = "Rol ve erisim haklari bu dosyada degil; ayri kurulmalidir."
}
$ManifestPath = "$DumpFile.manifest.json"
$manifest | ConvertTo-Json -Depth 4 | Set-Content -Path $ManifestPath -Encoding UTF8

Write-Host "Yedek olusturuldu: $DumpFile ($size byte)" -ForegroundColor Green
Write-Host "Manifest: $ManifestPath"
