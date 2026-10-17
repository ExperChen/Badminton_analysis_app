param([int]$Port = 8102, [int]$CompatPort = 8000, [switch]$OriginalUI)
$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
$projectPython = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $projectPython)) {
    throw 'Create .venv and install requirements.txt first. See README.md.'
}
if (-not $env:GGAB_MEDIA_ROOT) { $env:GGAB_MEDIA_ROOT = Join-Path $PSScriptRoot 'media' }
if (-not $env:GGAB_DB_PATH) { $env:GGAB_DB_PATH = Join-Path $PSScriptRoot 'ggab.db' }
if (-not $env:GGAB_CPU_BUDGET) { $env:GGAB_CPU_BUDGET = '14' }
if (-not $env:GGAB_UPLOAD_STORAGE) { $env:GGAB_UPLOAD_STORAGE = 'local' }
& $projectPython (Join-Path $PSScriptRoot 'tools\init_db.py')
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
$application = if ($OriginalUI) { 'app:app' } else { 'production_app:app' }
if (-not $OriginalUI -and -not $env:GGAB_UI_RELEASE -and -not (Test-Path -LiteralPath (Join-Path $PSScriptRoot 'deployments\active.json'))) {
    throw 'Build the React UI first: .venv\Scripts\python.exe tools\build_release.py'
}
& $projectPython (Join-Path $PSScriptRoot 'run_site.py') --port $Port --compat-port $CompatPort --application $application
exit $LASTEXITCODE
