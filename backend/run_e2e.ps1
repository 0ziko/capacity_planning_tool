# Tum bilesenleri entegre test eder ve outputs/e2e altina rapor yazar.
#   1) Backend birim/servis testleri (tests/, e2e haric)     -> izole SQLite
#   2) E2E senaryo paketi (tests/e2e)                         -> izole SQLite, TestClient
#   3) Frontend birim testleri + TypeScript/Vite derlemesi
#   4) Canli ortam salt okunur duman testi (localhost:8000)   -> canli veri DEGISMEZ
# Kullanim: cd backend; .\run_e2e.ps1 [-SkipUnit] [-SkipLive]
param([switch]$SkipUnit, [switch]$SkipLive)
Set-Location $PSScriptRoot
$py = ".\.venv\Scripts\python.exe"
$out = Join-Path $PSScriptRoot "..\outputs\e2e"
New-Item -ItemType Directory -Force $out | Out-Null
$env:PYTHONIOENCODING = "utf-8"
$summary = @()

if (-not $SkipUnit) {
    Write-Host "== 1/4 Backend birim testleri" -ForegroundColor Cyan
    & $py -m pytest tests --ignore=tests/e2e -q --no-header -p no:cacheprovider -W ignore --junitxml="$out\backend_unit.xml" 2>&1 | Tee-Object -FilePath "$out\backend_unit.log" | Select-Object -Last 3
    $summary += "Backend birim: exit $LASTEXITCODE"
}

Write-Host "== 2/4 E2E senaryo paketi" -ForegroundColor Cyan
& $py -m pytest tests/e2e -q --no-header -p no:cacheprovider -W ignore --junitxml="$out\e2e.xml" 2>&1 | Tee-Object -FilePath "$out\e2e.log" | Select-Object -Last 3
$summary += "E2E senaryolar: exit $LASTEXITCODE (rapor: outputs/e2e/e2e_results.md)"

Write-Host "== 3/4 Frontend test + build" -ForegroundColor Cyan
Push-Location (Join-Path $PSScriptRoot "..\frontend")
npm test 2>&1 | Tee-Object -FilePath "$out\frontend_test.log" | Select-Object -Last 4
$summary += "Frontend test: exit $LASTEXITCODE"
npm run build 2>&1 | Tee-Object -FilePath "$out\frontend_build.log" | Select-Object -Last 2
$summary += "Frontend build: exit $LASTEXITCODE"
Pop-Location

if (-not $SkipLive) {
    Write-Host "== 4/4 Canli duman testi (salt okunur)" -ForegroundColor Cyan
    & $py tests\e2e\live_smoke.py 2>&1 | Tee-Object -FilePath "$out\live_smoke.log" | Select-Object -Last 2
    $summary += "Canli duman: exit $LASTEXITCODE (rapor: outputs/e2e/live_smoke.md)"
}

Write-Host ""
Write-Host "== OZET" -ForegroundColor Green
$summary | ForEach-Object { Write-Host "  $_" }
