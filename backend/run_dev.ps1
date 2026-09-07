# Backend'i gelistirme modunda baslatir (http://localhost:8000, dokuman: /docs)
Set-Location $PSScriptRoot
if (-not (Test-Path ".venv")) { python -m venv .venv }
& .\.venv\Scripts\Activate.ps1
pip install -q -r requirements.txt
if (-not (Test-Path ".env")) { Copy-Item .env.example .env }
uvicorn app.main:app --reload --port 8000
