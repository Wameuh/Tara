$ErrorActionPreference = 'Stop'

$bash = Get-Command bash -ErrorAction SilentlyContinue
if (-not $bash) {
  throw 'Bash (WSL or Git Bash) is required to run the Docker Compose smoke test.'
}

$script = Join-Path $PSScriptRoot 'smoke-web-compose.sh'
& $bash.Source $script
if ($LASTEXITCODE -ne 0) {
  throw "Docker Compose smoke test failed with exit code $LASTEXITCODE."
}
