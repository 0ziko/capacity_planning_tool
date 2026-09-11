# Admin gerektirmeyen portable PostgreSQL kurulumu (proje icinde)
# Indirir, baslatir, SQLite verisini tasir, .env gunceller.

$ErrorActionPreference = "Stop"
Set-Location (Split-Path $PSScriptRoot -Parent)

$Base = Join-Path (Get-Location) ".postgres"
$Zip = Join-Path $Base "postgresql-binaries.zip"
$Url = "https://get.enterprisedb.com/postgresql/postgresql-17.11-3-windows-x64-binaries.zip"
$PgRoot = Join-Path $Base "pgsql"
$Bin = Join-Path $PgRoot "bin"
$Data = Join-Path $Base "data"
$Log = Join-Path $Base "postgres.log"
$PwFile = Join-Path $Base "pgpass.txt"
$PgUrl = "postgresql+psycopg://kapasite:kapasite@localhost:5432/kapasite"

if (-not (Test-Path ".venv")) {
    python -m venv .venv
}
& .\.venv\Scripts\Activate.ps1 | Out-Null
pip install -q -r requirements.txt

New-Item -ItemType Directory -Force -Path $Base | Out-Null

# Indir
if (-not (Test-Path (Join-Path $Bin "initdb.exe"))) {
    Write-Host "PostgreSQL binary indiriliyor (~50 MB)..." -ForegroundColor Cyan
    Invoke-WebRequest -Uri $Url -OutFile $Zip -UseBasicParsing
    Write-Host "Cikartiliyor..." -ForegroundColor Cyan
    Expand-Archive -Path $Zip -DestinationPath $Base -Force
    if (-not (Test-Path $Bin)) {
        throw "PostgreSQL bin klasoru bulunamadi: $Bin"
    }
}

# initdb
if (-not (Test-Path (Join-Path $Data "PG_VERSION"))) {
    Write-Host "Veritabani klasoru olusturuluyor..." -ForegroundColor Cyan
    Set-Content -Path $PwFile -Value "kapasite" -NoNewline -Encoding ASCII
    $env:LC_ALL = "C"
    $env:LANG = "C"
    & (Join-Path $Bin "initdb.exe") -D $Data -U kapasite -E UTF8 -A scram-sha-256 --pwfile=$PwFile --locale=C --lc-collate=C --lc-ctype=C
    Remove-Item $PwFile -Force -ErrorAction SilentlyContinue

    if (-not (Test-Path (Join-Path $Data "PG_VERSION"))) {
        throw "initdb basarisiz; pg_hba.conf olusturulamadi."
    }

    $hba = Join-Path $Data "pg_hba.conf"
    $lines = Get-Content $hba
    $lines = $lines | ForEach-Object {
        if ($_ -match '^# IPv4 local connections:' -or $_ -match '^# IPv6 local connections:' -or $_ -match '^# TYPE') { return $_ }
        if ($_ -match '^local\s+all\s+all\s+') { return "local   all             all                                     scram-sha-256" }
        if ($_ -match '^host\s+all\s+all\s+127\.0\.0\.1/32\s+') { return "host    all             all             127.0.0.1/32            scram-sha-256" }
        if ($_ -match '^host\s+all\s+all\s+::1/128\s+') { return "host    all             all             ::1/128                 scram-sha-256" }
        $_
    }
    Set-Content -Path $hba -Value $lines -Encoding ASCII

    $conf = Join-Path $Data "postgresql.conf"
    Add-Content -Path $conf -Value "`nlisten_addresses = 'localhost'`nport = 5432"
}

# Baslat
$ready = $false
if (Test-Path (Join-Path $Bin "pg_isready.exe")) {
    & (Join-Path $Bin "pg_isready.exe") -h localhost -p 5432 -U kapasite -d postgres 2>$null
    if ($LASTEXITCODE -eq 0) { $ready = $true }
}
if (-not $ready) {
    Write-Host "PostgreSQL baslatiliyor..." -ForegroundColor Cyan
    & (Join-Path $Bin "pg_ctl.exe") -D $Data -l $Log start
    Start-Sleep -Seconds 3
    for ($i = 0; $i -lt 20; $i++) {
        & (Join-Path $Bin "pg_isready.exe") -h localhost -p 5432 -U kapasite -d postgres 2>$null
        if ($LASTEXITCODE -eq 0) { $ready = $true; break }
        Start-Sleep -Seconds 1
    }
}
if (-not $ready) {
    Write-Host "PostgreSQL baslamadi. Log: $Log" -ForegroundColor Red
    Get-Content $Log -Tail 30 -ErrorAction SilentlyContinue
    exit 1
}

# Veritabani olustur
$env:PGPASSWORD = "kapasite"
$dbExists = & (Join-Path $Bin "psql.exe") -h localhost -p 5432 -U kapasite -d postgres -tAc "SELECT 1 FROM pg_database WHERE datname='kapasite'" 2>$null
if ($dbExists -ne "1") {
    & (Join-Path $Bin "psql.exe") -h localhost -p 5432 -U kapasite -d postgres -c "CREATE DATABASE kapasite OWNER kapasite;"
}

# SQLite tasima veya sema
$env:DATABASE_URL = $PgUrl
if (Test-Path "kapasite_dev.db") {
    Write-Host "SQLite -> PostgreSQL veri tasima..." -ForegroundColor Cyan
    & .\.venv\Scripts\python.exe scripts/migrate_sqlite_to_postgres.py --source "sqlite:///./kapasite_dev.db" --target $PgUrl
} else {
    @"
import os
os.environ['DATABASE_URL'] = '$PgUrl'
from sqlalchemy import create_engine, text
from app.db.session import Base
from app.db.migrate import ensure_columns, repair_orphans
import app.models  # noqa
eng = create_engine('$PgUrl')
Base.metadata.create_all(eng)
ensure_columns(eng)
repair_orphans(eng)
with eng.connect() as c:
    c.execute(text('SELECT 1'))
print('Semalar hazir.')
"@ | & .\.venv\Scripts\python.exe -
}

# .env
$envFile = ".env"
$lines = @()
if (Test-Path $envFile) {
    $lines = Get-Content $envFile | Where-Object { $_ -notmatch '^\s*DATABASE_URL\s*=' }
} else {
    Copy-Item ".env.example" $envFile
    $lines = Get-Content $envFile | Where-Object { $_ -notmatch '^\s*DATABASE_URL\s*=' }
}
$lines += "DATABASE_URL=$PgUrl"
Set-Content -Path $envFile -Value $lines -Encoding UTF8

# Saglik kontrolu
$health = @"
from sqlalchemy import create_engine, text
eng = create_engine('$PgUrl', pool_pre_ping=True)
with eng.connect() as c:
    c.execute(text('SELECT 1'))
print('PostgreSQL baglantisi OK')
"@ | & .\.venv\Scripts\python.exe -
if ($LASTEXITCODE -ne 0) { exit 1 }

Write-Host ""
Write-Host "Portable PostgreSQL hazir." -ForegroundColor Green
Write-Host "  Konum: $Base" -ForegroundColor DarkGray
Write-Host "  DATABASE_URL=$PgUrl" -ForegroundColor DarkGray
Write-Host "  Baslat: .\scripts\start_postgres.ps1" -ForegroundColor DarkGray
Write-Host "  Durdur: .\scripts\stop_postgres.ps1" -ForegroundColor DarkGray
Write-Host "  Backend: .\run_dev.ps1" -ForegroundColor Green
