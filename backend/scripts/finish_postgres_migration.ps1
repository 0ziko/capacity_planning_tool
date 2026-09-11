# PostgreSQL gecisi tamamlama: dogrulama, SQLite yedek, hazirlik raporu
$ErrorActionPreference = "Stop"
Set-Location (Split-Path $PSScriptRoot -Parent)

$PgUrl = "postgresql+psycopg://kapasite:kapasite@localhost:5432/kapasite"
$StartScript = Join-Path $PSScriptRoot "start_postgres.ps1"

Write-Host "=== PostgreSQL gecis tamamlama ===" -ForegroundColor Cyan

# PostgreSQL calistir
if (Test-Path $StartScript) {
    & $StartScript
}

# Baglanti + veri dogrulama
& .\.venv\Scripts\python.exe scripts/verify_postgres_migration.py --target $PgUrl
if ($LASTEXITCODE -ne 0) {
    Write-Host "Veri dogrulama basarisiz. SQLite'dan yeniden tasima:" -ForegroundColor Red
    Write-Host "  .\scripts\install_portable_postgres.ps1" -ForegroundColor Yellow
    exit 1
}

# SQLite yedekle (henuz yedeklenmediyse)
$backupDir = Join-Path (Get-Location) "backup"
New-Item -ItemType Directory -Force -Path $backupDir | Out-Null
foreach ($name in @("kapasite_dev.db", "kapasite_dev.db-shm", "kapasite_dev.db-wal")) {
    $src = Join-Path (Get-Location) $name
    if (Test-Path $src) {
        $dst = Join-Path $backupDir ($name + ".pre-postgres")
        if (-not (Test-Path $dst)) {
            Move-Item $src $dst -Force
            Write-Host "Yedeklendi: $dst" -ForegroundColor DarkGray
        } else {
            Remove-Item $src -Force -ErrorAction SilentlyContinue
        }
    }
}

# Uygulama sagligi
$health = @"
import os
os.environ.setdefault('DATABASE_URL', '$PgUrl')
from fastapi.testclient import TestClient
from app.main import app
c = TestClient(app)
h = c.get('/api/health').json()
assert h.get('db_ok'), h
r = c.post('/api/auth/login', data={'username': 'admin', 'password': 'admin123'})
assert r.status_code == 200, r.text
orders = c.get('/api/orders?status=open', headers={'Authorization': f"Bearer {r.json()['access_token']}"}).json()
print(f"Health: {h['database']} | Acik siparis: {len(orders)}")
"@ | & .\.venv\Scripts\python.exe -
if ($LASTEXITCODE -ne 0) { exit 1 }

Write-Host ""
Write-Host "PostgreSQL gecisi tamamlandi." -ForegroundColor Green
Write-Host "  Veritabani : $PgUrl" -ForegroundColor DarkGray
Write-Host "  SQLite yedek: backup\*.pre-postgres" -ForegroundColor DarkGray
Write-Host ""
Write-Host "Devam etmek icin:" -ForegroundColor Cyan
Write-Host "  1) cd backend && .\run_dev.ps1" -ForegroundColor White
Write-Host "  2) Ayri terminal: cd frontend && npm run dev" -ForegroundColor White
Write-Host "  3) http://localhost:5173 - admin / admin123" -ForegroundColor White
