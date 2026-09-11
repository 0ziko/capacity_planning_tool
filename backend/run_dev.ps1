# Backend'i gelistirme modunda baslatir (http://localhost:8000, dokuman: /docs)
Set-Location $PSScriptRoot
if (-not (Test-Path ".venv")) { python -m venv .venv }
& .\.venv\Scripts\Activate.ps1
pip install -q -r requirements.txt
if (-not (Test-Path ".env")) {
    Copy-Item .env.example .env
    Write-Host "Ilk calistirma: PostgreSQL icin .\scripts\install_portable_postgres.ps1 calistirin." -ForegroundColor Yellow
}
$url = (Select-String -Path ".env" -Pattern '^\s*DATABASE_URL\s*=\s*(.+)$' | Select-Object -First 1).Matches.Groups[1].Value.Trim()
if ($url -match '^postgresql') {
    $pgStart = Join-Path $PSScriptRoot "scripts\start_postgres.ps1"
    if (Test-Path $pgStart) { & $pgStart | Out-Null }
    Write-Host "Veritabani: PostgreSQL" -ForegroundColor DarkGray
} else {
    Write-Host "Veritabani: SQLite — PostgreSQL icin .\scripts\install_portable_postgres.ps1" -ForegroundColor Yellow
}
uvicorn app.main:app --reload --reload-dir app --reload-delay 0.5 --port 8000
