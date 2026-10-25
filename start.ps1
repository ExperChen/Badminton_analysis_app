param([int]$Port = 8102, [int]$CompatPort = 8000, [switch]$OriginalUI)
$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
$privateConfigPath = Join-Path $PSScriptRoot '.secrets\site.json'
if (Test-Path -LiteralPath $privateConfigPath) {
    $privateConfig = Get-Content -LiteralPath $privateConfigPath -Raw | ConvertFrom-Json
    if (-not $env:YUXU_PUBLIC_ORIGIN) { $env:YUXU_PUBLIC_ORIGIN = $privateConfig.origin }
    if ($privateConfig.require_vpn_identity) { $env:YUXU_REQUIRE_VPN_IDENTITY = '1' }
}
if (-not $env:YUXU_MEDIA_ROOT) { $env:YUXU_MEDIA_ROOT = Join-Path $PSScriptRoot 'media' }
$env:YUXU_CPU_BUDGET = if ($env:YUXU_CPU_BUDGET) { $env:YUXU_CPU_BUDGET } else { '14' }
if (-not $env:YUXU_DB_PATH) { $env:YUXU_DB_PATH = Join-Path $PSScriptRoot 'ggab.db' }
if (-not $env:YUXU_UPLOAD_STORAGE) { $env:YUXU_UPLOAD_STORAGE = 'local' }
$projectPython = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $projectPython)) {
    throw 'Missing .venv. Follow README.md to install Python dependencies first.'
}
& $projectPython (Join-Path $PSScriptRoot 'tools\init_db.py')
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
$application = if ($OriginalUI) { 'app:app' } else { 'production_app:app' }
if (-not $OriginalUI -and -not $env:YUXU_UI_RELEASE -and -not (Test-Path -LiteralPath (Join-Path $PSScriptRoot 'deployments\active.json'))) {
    throw 'Build the React UI first: .venv\Scripts\python.exe tools\build_release.py'
}
& $projectPython (Join-Path $PSScriptRoot 'run_site.py') --port $Port --compat-port $CompatPort --application $application
exit $LASTEXITCODE
