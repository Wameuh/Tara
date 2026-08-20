@echo off
setlocal enableextensions enabledelayedexpansion

rem Launches the local transcription inference server and standalone Tara.

set "AUDIO_DIR="
set "MERGED_TRANSCRIPTION="
set "CONFIG="
set "CONTEXT="
set "PRIOR_CONTEXT="
set "WRITE_CONTEXT_DEBUG="
set "SKIP_ANALYSIS="
set "INFERENCE_ENDPOINT="
set "INFERENCE_AUTH_PROVIDER="
set "USE_MODAL=0"
set "FORCE_RESTART_SERVER=0"
set "SERVER_PORT=8000"
set "SERVER_HOST=localhost"
set "SCRIPT_DIR=%~dp0"
for %%I in ("%SCRIPT_DIR%.") do set "PROJECT_DIR=%%~fI"
set "LOG_FILE=%SCRIPT_DIR%inference_server.log"
set "SERVER_PID="
set "SERVER_STARTED_BY_US=0"
set "SERVER_READY=0"
set "DEFAULT_CONFIG=%SCRIPT_DIR%config\configuration.json"
set "UV_EXE=uv"
if not defined UV_CACHE_DIR set "UV_CACHE_DIR=%SCRIPT_DIR%.uv-cache"
set "USAGE_EXIT_CODE=1"
set "RUN_STARTED_DATE_UTC="
set "RUN_ENDED_DATE_UTC="
set "RUN_START_TICKS="

call :load_dotenv
for /f "usebackq delims=" %%T in (`powershell -NoProfile -Command "(Get-Date).ToUniversalTime().ToString('yyyy-MM-dd')"`) do set "RUN_STARTED_DATE_UTC=%%T"

if "%~1"=="" goto usage

:args_loop
if "%~1"=="" goto args_done
if /i "%~1"=="--audio-dir" goto take_audio_dir
if /i "%~1"=="--merged-transcription" goto take_merged_transcription
if /i "%~1"=="--config" goto take_config
if /i "%~1"=="--context" goto take_context
if /i "%~1"=="--prior-context" goto take_prior_context
if /i "%~1"=="--write-context-debug" goto take_write_context_debug
if /i "%~1"=="--skip-analysis" goto take_skip_analysis
if /i "%~1"=="--transcription-only" goto take_skip_analysis
if /i "%~1"=="--modal" goto take_modal
if /i "%~1"=="--inference-endpoint" goto take_inference_endpoint
if /i "%~1"=="--inference-auth-provider" goto take_inference_auth_provider
if /i "%~1"=="--restart-server" goto take_restart_server
if /i "%~1"=="--server-port" goto take_server_port
if /i "%~1"=="--server-host" goto take_server_host
if /i "%~1"=="--inference-log" goto take_inference_log
if /i "%~1"=="-h" goto usage_success
if /i "%~1"=="--help" goto usage_success
echo Unknown argument: %~1 1>&2
goto usage

:take_audio_dir
set "AUDIO_DIR=%~2"
shift
shift
goto args_loop

:take_merged_transcription
set "MERGED_TRANSCRIPTION=%~2"
shift
shift
goto args_loop

:take_config
set "CONFIG=%~2"
shift
shift
goto args_loop

:take_context
set "CONTEXT=%~2"
shift
shift
goto args_loop

:take_prior_context
set "PRIOR_CONTEXT=%~2"
shift
shift
goto args_loop

:take_write_context_debug
set "WRITE_CONTEXT_DEBUG=1"
shift
goto args_loop

:take_skip_analysis
set "SKIP_ANALYSIS=1"
shift
goto args_loop

:take_modal
set "USE_MODAL=1"
set "INFERENCE_AUTH_PROVIDER=modal_map"
if not defined TARA_TRANSCRIPTION_PARALLELISM set "TARA_TRANSCRIPTION_PARALLELISM=all"
shift
goto args_loop

:take_inference_endpoint
set "INFERENCE_ENDPOINT=%~2"
shift
shift
goto args_loop

:take_inference_auth_provider
set "INFERENCE_AUTH_PROVIDER=%~2"
shift
shift
goto args_loop

:take_restart_server
set "FORCE_RESTART_SERVER=1"
shift
goto args_loop

:take_server_port
set "SERVER_PORT=%~2"
shift
shift
goto args_loop

:take_server_host
set "SERVER_HOST=%~2"
shift
shift
goto args_loop

:take_inference_log
set "LOG_FILE=%~2"
shift
shift
goto args_loop

:args_done
if "%CONFIG%"=="" (
  if exist "%DEFAULT_CONFIG%" set "CONFIG=%DEFAULT_CONFIG%"
)

if not "%AUDIO_DIR%"=="" goto have_input
if not "%MERGED_TRANSCRIPTION%"=="" goto have_input
echo Error: --audio-dir or --merged-transcription is required. 1>&2
goto usage

:have_input
if not "%AUDIO_DIR%"=="" if not "%MERGED_TRANSCRIPTION%"=="" (
  echo Error: use only one of --audio-dir or --merged-transcription. 1>&2
  exit /b 1
)

for /f "usebackq delims=" %%T in (`powershell -NoProfile -Command "(Get-Date).Ticks"`) do set "RUN_START_TICKS=%%T"
for /f "usebackq delims=" %%T in (`powershell -NoProfile -Command "(Get-Date).ToString('o')"`) do set "RUN_STARTED_ISO=%%T"

if not "%CONFIG%"=="" (
  for /f "usebackq delims=" %%T in (`powershell -NoProfile -Command "try { (Get-Content -Raw '%CONFIG%' | ConvertFrom-Json).transcription.request_timeout_seconds } catch { '' }"`) do (
    if not "%%T"=="" set "INFERENCE_WORKER_TIMEOUT_SECONDS=%%T"
  )
  if "%INFERENCE_ENDPOINT%"=="" (
    for /f "usebackq delims=" %%T in (`powershell -NoProfile -Command "try { (Get-Content -Raw '%CONFIG%' | ConvertFrom-Json).transcription.inference_endpoint } catch { '' }"`) do (
      if not "%%T"=="" set "INFERENCE_ENDPOINT=%%T"
    )
  )
  if "%INFERENCE_AUTH_PROVIDER%"=="" (
    for /f "usebackq delims=" %%T in (`powershell -NoProfile -Command "try { (Get-Content -Raw '%CONFIG%' | ConvertFrom-Json).transcription.inference_auth_provider } catch { '' }"`) do (
      if not "%%T"=="" set "INFERENCE_AUTH_PROVIDER=%%T"
    )
  )
)

if "%USE_MODAL%"=="1" set "INFERENCE_AUTH_PROVIDER=modal_map"
if not "%INFERENCE_ENDPOINT%"=="" set "TARA_INFERENCE_ENDPOINT=%INFERENCE_ENDPOINT%"
if not "%INFERENCE_AUTH_PROVIDER%"=="" set "TARA_INFERENCE_AUTH_PROVIDER=%INFERENCE_AUTH_PROVIDER%"

rem Use parallel Modal Function.spawn when modal_map is configured (with or without --modal).
if /i "%TARA_INFERENCE_AUTH_PROVIDER%"=="modal_map" set "USE_MODAL=1"

rem Auto-upgrade legacy modal_proxy + Modal HTTP endpoint to modal_map.
if /i "%TARA_INFERENCE_AUTH_PROVIDER%"=="modal_proxy" (
  if defined TARA_INFERENCE_ENDPOINT (
    echo !TARA_INFERENCE_ENDPOINT! | findstr /i /c:".modal.run" >nul 2>&1
    if not errorlevel 1 (
      echo Upgrading legacy Modal HTTP configuration to modal_map ^(parallel Function.spawn^).
      set "TARA_INFERENCE_AUTH_PROVIDER=modal_map"
      set "USE_MODAL=1"
      if not defined TARA_TRANSCRIPTION_PARALLELISM set "TARA_TRANSCRIPTION_PARALLELISM=all"
    )
  )
)

set "PYTHONPATH=%SCRIPT_DIR%src;%PYTHONPATH%"
call :resolve_uv_exe
if errorlevel 1 exit /b 1

if not "%AUDIO_DIR%"=="" (
  if not exist "%AUDIO_DIR%\" (
    echo Error: Audio directory does not exist: %AUDIO_DIR% 1>&2
    exit /b 1
  )
  for %%I in ("%AUDIO_DIR%") do set "AUDIO_DIR=%%~fI"
)

if not "%MERGED_TRANSCRIPTION%"=="" (
  if not exist "%MERGED_TRANSCRIPTION%" (
    echo Error: Merged transcription file does not exist: %MERGED_TRANSCRIPTION% 1>&2
    exit /b 1
  )
  for %%I in ("%MERGED_TRANSCRIPTION%") do set "MERGED_TRANSCRIPTION=%%~fI"
)

if not "%AUDIO_DIR%"=="" (
  if "%USE_MODAL%"=="1" (
    echo Using Modal parallel transcription ^(modal_map / Function.spawn^).
    "%UV_EXE%" run --project "%PROJECT_DIR%" --extra deploy python -m tara.modal_preflight
    if errorlevel 1 exit /b 1
    goto inference_ready
  )
  if defined TARA_INFERENCE_ENDPOINT (
    call :is_local_inference_endpoint "!TARA_INFERENCE_ENDPOINT!"
    if "!IS_LOCAL_INFERENCE_ENDPOINT!"=="1" (
      call :ensure_inference_server
      if errorlevel 1 exit /b 1
      set "TARA_INFERENCE_ENDPOINT=!HEALTH_URL:/health=!"
    ) else (
      echo Using remote inference endpoint: !TARA_INFERENCE_ENDPOINT!
    )
  ) else (
    call :ensure_inference_server
    if errorlevel 1 exit /b 1
    set "TARA_INFERENCE_ENDPOINT=!HEALTH_URL:/health=!"
  )
)

:inference_ready
set "TARA_ARGS="
if not "%AUDIO_DIR%"=="" set "TARA_ARGS=!TARA_ARGS! --audio-dir "%AUDIO_DIR%""
if not "%MERGED_TRANSCRIPTION%"=="" set "TARA_ARGS=!TARA_ARGS! --merged-transcription "%MERGED_TRANSCRIPTION%""
if not "%CONFIG%"=="" set "TARA_ARGS=!TARA_ARGS! --config "%CONFIG%""
if not "%CONTEXT%"=="" set "TARA_ARGS=!TARA_ARGS! --context "%CONTEXT%""
if not "%PRIOR_CONTEXT%"=="" set "TARA_ARGS=!TARA_ARGS! --prior-context "%PRIOR_CONTEXT%""
if not "%WRITE_CONTEXT_DEBUG%"=="" set "TARA_ARGS=!TARA_ARGS! --write-context-debug"
if not "%SKIP_ANALYSIS%"=="" set "TARA_ARGS=!TARA_ARGS! --skip-analysis"

set "TARA_RUN_OUTPUT=%TEMP%\tara_last_run.json"
echo.
echo Starting standalone Tara...
echo Using uv: %UV_EXE%
for /f "usebackq delims=" %%T in (`powershell -NoProfile -Command "(Get-Date).Ticks"`) do set "TARA_START_TICKS=%%T"
set "TARA_RUN_REPORTING=bat"
if "%USE_MODAL%"=="1" (
  "%UV_EXE%" run --project "%PROJECT_DIR%" --extra deploy python -m tara !TARA_ARGS! > "%TARA_RUN_OUTPUT%"
) else (
  "%UV_EXE%" run --project "%PROJECT_DIR%" python -m tara !TARA_ARGS! > "%TARA_RUN_OUTPUT%"
)
set "TARA_EXIT_CODE=!ERRORLEVEL!"
set "TARA_RUN_REPORTING="
for /f "usebackq delims=" %%S in (`powershell -NoProfile -Command "$elapsed = [TimeSpan]::FromTicks((Get-Date).Ticks - [int64]$env:TARA_START_TICKS); [Math]::Round($elapsed.TotalSeconds, 1)"`) do set "TARA_RUN_ELAPSED_SECONDS=%%S"
type "%TARA_RUN_OUTPUT%"

call :print_modal_billing_report

for /f "usebackq delims=" %%T in (`powershell -NoProfile -Command "(Get-Date).ToString('o')"`) do set "RUN_ENDED_ISO=%%T"
for /f "usebackq delims=" %%S in (`powershell -NoProfile -Command "$elapsed = [TimeSpan]::FromTicks((Get-Date).Ticks - [int64]$env:RUN_START_TICKS); [Math]::Round($elapsed.TotalSeconds, 1)"`) do set "TARA_GLOBAL_ELAPSED_SECONDS=%%S"
call :print_run_report "%TARA_RUN_OUTPUT%"
set "TARA_RUN_ELAPSED_SECONDS="
set "TARA_GLOBAL_ELAPSED_SECONDS="
set "RUN_ENDED_ISO="

call :cleanup
exit /b !TARA_EXIT_CODE!

:resolve_uv_exe
where uv >nul 2>&1
if errorlevel 1 (
  echo Error: uv not found. Install uv or add it to PATH. 1>&2
  echo Example: powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 ^| iex" 1>&2
  exit /b 1
)
for /f "delims=" %%P in ('where uv 2^>nul') do (
  set "UV_EXE=%%P"
  goto resolve_uv_done
)
:resolve_uv_done
exit /b 0

:load_dotenv
if not exist "%SCRIPT_DIR%.env" exit /b 0
for /f "usebackq eol=# tokens=1* delims==" %%K in ("%SCRIPT_DIR%.env") do (
  if not "%%K"=="" if not "%%L"=="" if not defined %%K set "%%K=%%~L"
)
exit /b 0

:is_local_inference_endpoint
set "IS_LOCAL_INFERENCE_ENDPOINT=0"
set "CHECK_ENDPOINT=%~1"
if /i "%CHECK_ENDPOINT:~0,16%"=="http://localhost" set "IS_LOCAL_INFERENCE_ENDPOINT=1"
if /i "%CHECK_ENDPOINT:~0,17%"=="https://localhost" set "IS_LOCAL_INFERENCE_ENDPOINT=1"
if /i "%CHECK_ENDPOINT:~0,16%"=="http://127.0.0.1" set "IS_LOCAL_INFERENCE_ENDPOINT=1"
if /i "%CHECK_ENDPOINT:~0,17%"=="https://127.0.0.1" set "IS_LOCAL_INFERENCE_ENDPOINT=1"
exit /b 0

:modal_endpoint_required
echo Error: --modal requires a remote Modal endpoint. Set TARA_INFERENCE_ENDPOINT or pass --inference-endpoint https://...modal.run. 1>&2
exit /b 1

:modal_local_endpoint
echo Error: --modal was requested, but TARA_INFERENCE_ENDPOINT points to a local endpoint: %TARA_INFERENCE_ENDPOINT% 1>&2
exit /b 1

:modal_key_required
echo Error: --modal requires TARA_MODAL_PROXY_AUTH_KEY in the current environment. 1>&2
exit /b 1

:modal_secret_required
echo Error: --modal requires TARA_MODAL_PROXY_AUTH_SECRET in the current environment. 1>&2
exit /b 1

:ensure_inference_server
set "HEALTH_HOST=%SERVER_HOST%"
if "%HEALTH_HOST%"=="0.0.0.0" set "HEALTH_HOST=127.0.0.1"
set "HEALTH_URL=http://%HEALTH_HOST%:%SERVER_PORT%/health"
set "SERVER_READY=0"
if "%FORCE_RESTART_SERVER%"=="1" call :stop_existing_inference_server

call :check_health
if "%FORCE_RESTART_SERVER%"=="0" if "!SERVER_READY!"=="1" (
  call :find_server_pid
  if not "!SERVER_PID!"=="" (
    echo Reusing inference server on %SERVER_HOST%:%SERVER_PORT% ^(PID: !SERVER_PID!^).
    exit /b 0
  )
)

echo Starting inference server on %SERVER_HOST%:%SERVER_PORT%...
echo Using uv: %UV_EXE%
echo Inference server log: %LOG_FILE%
pushd "%SCRIPT_DIR%"
start "" /b "%UV_EXE%" run --project "%PROJECT_DIR%" --extra inference python -m uvicorn inference_server.app:app --host %SERVER_HOST% --port %SERVER_PORT% > "%LOG_FILE%" 2>&1
popd

ping -n 2 127.0.0.1 >nul
call :find_server_pid
if "%SERVER_PID%"=="" (
  echo Error: Failed to start inference server. 1>&2
  echo Check log: %LOG_FILE% 1>&2
  exit /b 1
)

echo Inference server started ^(PID: %SERVER_PID%^).
set "SERVER_STARTED_BY_US=1"
set "MAX_ATTEMPTS=30"
echo Waiting for inference server to be ready...
set "SERVER_READY=0"
for /l %%I in (1,1,%MAX_ATTEMPTS%) do (
  ping -n 2 127.0.0.1 >nul
  call :check_health
  if "!SERVER_READY!"=="1" (
    echo Inference server is ready.
    exit /b 0
  )
  set /a mod=%%I %% 5
  if "!mod!"=="0" echo Still waiting for server... ^(attempt %%I/%MAX_ATTEMPTS%^)
)

echo Error: Inference server failed to start within %MAX_ATTEMPTS% attempts. 1>&2
echo Check log: %LOG_FILE% 1>&2
call :cleanup
exit /b 1

:check_health
powershell -NoProfile -Command ^
  "try { $r = Invoke-WebRequest -UseBasicParsing -TimeoutSec 2 -Uri '%HEALTH_URL%'; if ($r.StatusCode -eq 200) { exit 0 } else { exit 1 } } catch { exit 1 }" >nul 2>&1
if %ERRORLEVEL%==0 (
  set "SERVER_READY=1"
) else (
  set "SERVER_READY=0"
)
exit /b 0

:find_server_pid
set "SERVER_PID="
for /f "tokens=2 delims==" %%P in ('wmic process where "CommandLine like '%%uvicorn inference_server.app:app%%' and Name='python.exe'" get ProcessId /value 2^>nul ^| findstr /b "ProcessId="') do (
  set "SERVER_PID=%%P"
)
if not "!SERVER_PID!"=="" set /a SERVER_PID=SERVER_PID+0 2>nul
exit /b 0

:stop_existing_inference_server
call :find_server_pid
if "!SERVER_PID!"=="" exit /b 0
echo Stopping existing inference server ^(PID: !SERVER_PID!^)...
taskkill /PID !SERVER_PID! /F /T >nul 2>&1
set "SERVER_PID="
set "SERVER_READY=0"
ping -n 3 127.0.0.1 >nul
exit /b 0

:cleanup
if "%SERVER_STARTED_BY_US%"=="1" (
  if not "%SERVER_PID%"=="" (
    echo.
    echo Stopping inference server ^(PID: %SERVER_PID%^)...
    taskkill /PID %SERVER_PID% /F /T >nul 2>&1
  )
)
exit /b 0

:print_run_report
set "RUN_JSON=%~1"
if "%RUN_JSON%"=="" exit /b 0
if not exist "%RUN_JSON%" exit /b 0
set "REPORT_ARGS=--project-dir "%PROJECT_DIR%""
if defined TARA_GLOBAL_ELAPSED_SECONDS set "REPORT_ARGS=!REPORT_ARGS! --global-seconds !TARA_GLOBAL_ELAPSED_SECONDS!"
if defined TARA_RUN_ELAPSED_SECONDS set "REPORT_ARGS=!REPORT_ARGS! --tara-seconds !TARA_RUN_ELAPSED_SECONDS!"
if defined RUN_STARTED_ISO set "REPORT_ARGS=!REPORT_ARGS! --run-started-iso "!RUN_STARTED_ISO!""
if defined RUN_ENDED_ISO set "REPORT_ARGS=!REPORT_ARGS! --run-ended-iso "!RUN_ENDED_ISO!""
if "%USE_MODAL%"=="1" set "REPORT_ARGS=!REPORT_ARGS! --include-modal-billing"
"%UV_EXE%" run --project "%PROJECT_DIR%" python -m tara.run_reporting "%RUN_JSON%" !REPORT_ARGS!
exit /b 0

:print_modal_billing_report
if not "%USE_MODAL%"=="1" exit /b 0
for /f "usebackq delims=" %%T in (`powershell -NoProfile -Command "(Get-Date).ToUniversalTime().AddDays(1).ToString('yyyy-MM-dd')"`) do set "RUN_ENDED_DATE_UTC=%%T"
echo.
echo Modal billing report ^(UTC day covering this run^):
"%UV_EXE%" run --project "%PROJECT_DIR%" --extra deploy modal billing report --start %RUN_STARTED_DATE_UTC% --end %RUN_ENDED_DATE_UTC% --resolution h --tz local
if errorlevel 1 (
  echo Unable to fetch Modal billing report automatically. You can run:
  echo uv run --project "%PROJECT_DIR%" --extra deploy modal billing report --start %RUN_STARTED_DATE_UTC% --end %RUN_ENDED_DATE_UTC% --resolution h --tz local
)
echo Note: Modal reports complete billing intervals; the newest usage may appear after Modal finalizes billing data.
exit /b 0

:usage
echo Usage: run_tara.bat [--audio-dir PATH ^| --merged-transcription FILE] [options]
echo.
echo Options:
echo   --config PATH              Path to configuration JSON
echo   --context PATH             General campaign context markdown/text
echo   --prior-context PATH       Previous-session context markdown/text
echo   --write-context-debug      Write redacted context debug artifact
echo   --skip-analysis            Run transcription and processing only
echo   --transcription-only       Alias for --skip-analysis
echo   --modal                    Use Modal inference and never start local inference
echo   --inference-endpoint URL   Remote or local inference endpoint
echo   --inference-auth-provider PROVIDER
echo                              Inference auth provider: none or modal_proxy
echo   --restart-server           Stop any server on the port and start a fresh one
echo   --server-port PORT         Inference server port for audio runs (default: 8000)
echo   --server-host HOST         Inference server host for audio runs (default: localhost)
echo   --inference-log PATH       Inference server log path (default: inference_server.log)
echo   -h, --help                 Show this help
exit /b %USAGE_EXIT_CODE%

:usage_success
set "USAGE_EXIT_CODE=0"
goto usage
