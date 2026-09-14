# Yalnizca audit/test gecici veritabanina pg_restore. Canli 'kapasite' hedeflenemez.

param(
    [Parameter(Mandatory = $true)]
    [string]$DumpFile,
    [string]$TargetDatabase = "kapasite_audit_test"
)

$ErrorActionPreference = "Stop"
$BackendRoot = Split-Path $PSScriptRoot -Parent
Set-Location $BackendRoot

$Python = Join-Path $BackendRoot ".venv\Scripts\python.exe"
if (-not (Test-Path $Python)) { throw ".venv python bulunamadi" }

function Get-DatabaseUrl {
    $EnvFile = Join-Path $BackendRoot ".env"
    $line = Get-Content $EnvFile -Encoding UTF8 | Where-Object { $_ -match '^\s*DATABASE_URL\s*=' } | Select-Object -First 1
    return ($line -replace '^\s*DATABASE_URL\s*=\s*', '').Trim().Trim('"')
}

function Parse-PgUrl([string]$Url) {
    $u = $Url -replace '^postgresql\+\w+://', 'postgresql://'
    if ($u -notmatch '^postgresql://([^:/@]+)(?::([^@]*))?@([^:/]+)(?::(\d+))?/([^?]+)') {
        throw "DATABASE_URL cozulemedi"
    }
    return @{
        User = $Matches[1]
        Password = $Matches[2]
        Host = $Matches[3]
        Port = if ($Matches[4]) { $Matches[4] } else { "5432" }
        Database = $Matches[5]
    }
}

$sourceDb = (Parse-PgUrl (Get-DatabaseUrl)).Database
& $Python -c "from app.services.backup_guard import validate_restore_target; validate_restore_target('$TargetDatabase', source_db='$sourceDb')"
if ($LASTEXITCODE -ne 0) { throw "Hedef veritabani reddedildi" }

$PgBin = Join-Path $BackendRoot ".postgres\pgsql\bin"
$RestoreExe = Join-Path $PgBin "pg_restore.exe"
$PsqlExe = Join-Path $PgBin "psql.exe"
$CreatedbExe = Join-Path $PgBin "createdb.exe"
$DropdbExe = Join-Path $PgBin "dropdb.exe"
if (-not (Test-Path $RestoreExe)) {
    throw "Portable PostgreSQL bulunamadi."
}

$pg = Parse-PgUrl (Get-DatabaseUrl)
$env:PGPASSWORD = $pg.Password
$prevEap = $ErrorActionPreference
try {
    $ErrorActionPreference = "Continue"
    & $DropdbExe -h $pg.Host -p $pg.Port -U $pg.User --if-exists $TargetDatabase *> $null
    $ErrorActionPreference = "Stop"
    & $CreatedbExe -h $pg.Host -p $pg.Port -U $pg.User $TargetDatabase
    if ($LASTEXITCODE -ne 0) { throw "createdb basarisiz" }
    & $RestoreExe -h $pg.Host -p $pg.Port -U $pg.User -d $TargetDatabase --no-owner --no-acl $DumpFile
    if ($LASTEXITCODE -ne 0) { throw "pg_restore cikis kodu $LASTEXITCODE" }
}
finally {
    $ErrorActionPreference = $prevEap
    Remove-Item Env:PGPASSWORD -ErrorAction SilentlyContinue
}

$TargetUrl = "postgresql+psycopg://$($pg.User):$($pg.Password)@$($pg.Host):$($pg.Port)/$TargetDatabase"
$CountsFile = Join-Path $BackendRoot "backup\restore_expected_counts.json"
if (Test-Path $CountsFile) {
    & $Python (Join-Path $BackendRoot "scripts\validate_restore_db.py") --url $TargetUrl --expected-counts $CountsFile
} else {
    & $Python (Join-Path $BackendRoot "scripts\validate_restore_db.py") --url $TargetUrl
}
$valExit = $LASTEXITCODE

if (Test-Path "$DumpFile.manifest.json") {
    $manifestPath = "$DumpFile.manifest.json"
    $mObj = Get-Content $manifestPath -Raw | ConvertFrom-Json
    $m = @{}
    $mObj.PSObject.Properties | ForEach-Object { $m[$_.Name] = $_.Value }
    $m["validation"] = if ($valExit -eq 0) { "ok" } else { "failed_exit_$valExit" }
    $m["validated_at"] = (Get-Date).ToUniversalTime().ToString("o")
    $m["restore_target"] = $TargetDatabase
    $m | ConvertTo-Json -Depth 4 | Set-Content -Path $manifestPath -Encoding UTF8
}

Write-Host "Restore tamamlandi: $TargetDatabase (dogrulama cikis=$valExit)" -ForegroundColor $(if ($valExit -eq 0) { "Green" } else { "Yellow" })
exit $valExit
