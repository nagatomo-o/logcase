Set-Location $PSScriptRoot\..
if (-not (Test-Path .venv)) { python -m venv .venv }
.\.venv\Scripts\Activate.ps1
pip install -e ".[dev]"
python -m access_log_db.app @args
