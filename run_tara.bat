@echo off
setlocal enableextensions enabledelayedexpansion

rem Launches the local transcription inference server and standalone Tara.

set "AUDIO_DIR="
set "MERGED_TRANSCRIPTION="
set "CONFIG="
set "SERVER_PORT=8000"
set "SERVER_HOST=localhost"
set "SCRIPT_DIR=%~dp0"
set "LOG_FILE=%SCRIPT_DIR%inference_server.log"
set "SERVER_PID="
set "SERVER_CMD_FILE="
set "DEFAULT_CONFIG=%SCRIPT_DIR%config\configuration.json"
set "OLD_TARA_DIR=%SCRIPT_DIR%..\Tara"

if "%~1"=="" goto :usage

:parse_args
if "%~1"=="" goto :args_done
if /i "%~1"=="--audio-dir" (
  set "AUDIO_DIR=%~2"
  shift
  shift
  goto :parse_args
)
if /i "%~1"=="--merged-transcription" (
  set "MERGED_TRANSCRIPTION=%~2"
  shift
  shift
  goto :parse_args
)
if /i "%~1"=="--config" (
  set "CONFIG=%~2"
  shift
  shift
  goto :parse_args
)
if /i "%~1"=="--server-port" (
  set "SERVER_PORT=%~2"
  shift
  shift
  goto :parse_args
)
if /i "%~1"=="--server-host" (
  set "SERVER_HOST=%~2"
  shift
  shift
  goto :parse_args
)
if /i "%~1"=="-h" goto :usage
if /i "%~1"=="--help" goto :usage

echo Unknown argument: %~1 1>&2
goto :usage

:args_done
if "%CONFIG%"=="" (
  if exist "%DEFAULT_CONFIG%" set "CONFIG=%DEFAULT_CONFIG%"
)

if "%AUDIO_DIR%"=="" if "%MERGED_TRANSCRIPTION%"=="" (
  echo Error: --audio-dir or --merged-transcription is required. 1>&2
  goto :usage
)
if not "%AUDIO_DIR%"=="" if not "%MERGED_TRANSCRIPTION%"=="" (
  echo Error: use only one of --audio-dir or --merged-transcription. 1>&2
  exit /b 1
)

set "PYTHONPATH=%SCRIPT_DIR%src;%PYTHONPATH%"
if not "%AUDIO_DIR%"=="" (
  if not exist "%AUDIO_DIR%\" (
    echo Error: Audio directory does not exist: %AUDIO_DIR% 1>&2
    exit /b 1
  )
  for %%I in ("%AUDIO_DIR%") do set "AUDIO_DIR=%%~fI"
  call :start_server
)

set "TARA_ARGS="
if not "%AUDIO_DIR%"=="" set "TARA_ARGS=!TARA_ARGS! --audio-dir "%AUDIO_DIR%""
if not "%MERGED_TRANSCRIPTION%"=="" set "TARA_ARGS=!TARA_ARGS! --merged-transcription "%MERGED_TRANSCRIPTION%""
if not "%CONFIG%"=="" set "TARA_ARGS=!TARA_ARGS! --config "%CONFIG%""

echo.
echo Starting standalone Tara...
python -m tara !TARA_ARGS!
set "TARA_EXIT_CODE=%ERRORLEVEL%"

call :cleanup
exit /b %TARA_EXIT_CODE%

:start_server
echo Starting inference server on %SERVER_HOST%:%SERVER_PORT%...
if not exist "%OLD_TARA_DIR%\" (
  echo Error: old Tara reference directory not found at %OLD_TARA_DIR% 1>&2
  exit /b 1
)
set "SERVER_CMD_FILE=%TEMP%\run_tara_repo_inference_%RANDOM%.cmd"
(
  echo @echo off
  echo set "PYTHONPATH=%OLD_TARA_DIR%\src;%PYTHONPATH%"
  echo cd /d "%OLD_TARA_DIR%"
  echo python -m uvicorn inference_server.app:app --host %SERVER_HOST% --port %SERVER_PORT% ^> "%LOG_FILE%" 2^>^&1
) > "%SERVER_CMD_FILE%"
start "" /b "%SERVER_CMD_FILE%"

set "HEALTH_HOST=%SERVER_HOST%"
if "%HEALTH_HOST%"=="0.0.0.0" set "HEALTH_HOST=127.0.0.1"
set "HEALTH_URL=http://%HEALTH_HOST%:%SERVER_PORT%/health"
set "MAX_ATTEMPTS=30"
for /l %%I in (1,1,%MAX_ATTEMPTS%) do (
  ping -n 2 127.0.0.1 >nul
  call :check_health
  if "!SERVER_READY!"=="1" goto :server_ready
)

echo Error: Inference server failed to start. 1>&2
call :cleanup
exit /b 1

:server_ready
call :find_server_pid
echo Inference server is ready.
exit /b 0

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
for /f "tokens=2 delims== " %%P in ('wmic process where "CommandLine like '%%uvicorn inference_server.app:app%%' and Name='python.exe'" get ProcessId /value ^| find "="') do (
  set "SERVER_PID=%%P"
)
exit /b 0

:cleanup
if not "%SERVER_PID%"=="" (
  echo Stopping inference server (PID: %SERVER_PID%)...
  taskkill /PID %SERVER_PID% /F /T >nul 2>&1
)
if not "%SERVER_CMD_FILE%"=="" if exist "%SERVER_CMD_FILE%" del /f /q "%SERVER_CMD_FILE%" >nul 2>&1
exit /b 0

:usage
echo Usage: run_tara.bat [--audio-dir PATH ^| --merged-transcription FILE] [options]
echo.
echo Options:
echo   --config PATH              Path to configuration JSON
echo   --server-port PORT         Inference server port for audio runs (default: 8000)
echo   --server-host HOST         Inference server host for audio runs (default: localhost)
echo   -h, --help                 Show this help
exit /b 1
