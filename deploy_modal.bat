@echo off
setlocal EnableExtensions

cd /d "%~dp0"

echo Deploying Tara Modal inference app...
uv run --extra deploy modal deploy modal_inference.py
if errorlevel 1 (
  echo Error: modal deploy failed. 1>&2
  exit /b 1
)

echo Warming Parakeet model cache on Modal volume tara-parakeet-cache...
uv run --extra deploy modal run modal_inference.py::warm_cache_entrypoint
if errorlevel 1 (
  echo WARNING: warm_cache failed. Endpoint is deployed but cache may be cold. 1>&2
  echo Retry: uv run --extra deploy modal run modal_inference.py::warm_cache_entrypoint 1>&2
  exit /b 0
)

echo Modal deploy and warm cache completed successfully.
exit /b 0
