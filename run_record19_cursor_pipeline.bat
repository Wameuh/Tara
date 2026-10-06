@echo off
setlocal enabledelayedexpansion

rem Run Tara Record23 analysis through the Cursor CLI backend.
rem This script is intentionally self-contained so it can be launched manually.

set "SCRIPT_DIR=%~dp0"
set "REPO_DIR=%SCRIPT_DIR:~0,-1%"
set "MERGED_TRANSCRIPTION=%USERPROFILE%\Documents\Projets\DM_Assistant\Record_session\Record23\transcriptions\merged_transcription.json"
set "CONTEXT_FILE=%USERPROFILE%\Documents\Projets\DM_Assistant\Record_session\context.md"
set "PRIOR_CONTEXT_FILE=%USERPROFILE%\Documents\Projets\DM_Assistant\Record_session\Resume_parties_precedentes.md"
set "CURSOR_AGENT=cursor-agent"
set "CONDABAT=%USERPROFILE%\anaconda3\condabin\conda.bat"
set "TEMP_CONFIG=%TEMP%\tara_cursor_pipeline_%RANDOM%.json"

echo.
echo Tara Record23 Cursor CLI pipeline
echo Repo: "%REPO_DIR%"
echo Transcript: "%MERGED_TRANSCRIPTION%"
echo Context: "%CONTEXT_FILE%"
echo Prior context: "%PRIOR_CONTEXT_FILE%"
echo.

if not exist "%MERGED_TRANSCRIPTION%" (
  echo ERROR: merged transcription not found.
  exit /b 1
)

if not exist "%CONTEXT_FILE%" (
  echo ERROR: context file not found.
  exit /b 1
)

if not exist "%PRIOR_CONTEXT_FILE%" (
  echo ERROR: prior context file not found.
  exit /b 1
)

if not exist "%CURSOR_AGENT%" (
  echo ERROR: Cursor Agent command not found: "%CURSOR_AGENT%"
  exit /b 1
)

if exist "%CONDABAT%" (
  call "%CONDABAT%" activate DM
  if errorlevel 1 (
    echo ERROR: failed to activate conda environment DM.
    exit /b 1
  )
)

> "%TEMP_CONFIG%" echo {
>> "%TEMP_CONFIG%" echo   "analysis": {
>> "%TEMP_CONFIG%" echo     "enabled": true,
>> "%TEMP_CONFIG%" echo     "pipeline": "blackboard_v1",
>> "%TEMP_CONFIG%" echo     "output_dir": "analysis",
>> "%TEMP_CONFIG%" echo     "max_audit_attempts": 3,
>> "%TEMP_CONFIG%" echo     "target_window_seconds": 90,
>> "%TEMP_CONFIG%" echo     "overlap_seconds": 20,
>> "%TEMP_CONFIG%" echo     "llm": {
>> "%TEMP_CONFIG%" echo       "backend": "cursor_cli",
>> "%TEMP_CONFIG%" echo       "model": "Auto",
>> "%TEMP_CONFIG%" echo       "cursor_command": "cursor-agent",
>> "%TEMP_CONFIG%" echo       "cursor_args": ["-p"],
>> "%TEMP_CONFIG%" echo       "timeout_seconds": 900,
>> "%TEMP_CONFIG%" echo       "retries": 2,
>> "%TEMP_CONFIG%" echo       "cursor_cli_probe": true
>> "%TEMP_CONFIG%" echo     }
>> "%TEMP_CONFIG%" echo   }
>> "%TEMP_CONFIG%" echo }

pushd "%REPO_DIR%"
set "PYTHONPATH=src"

python -m tara ^
  --config "%TEMP_CONFIG%" ^
  --merged-transcription "%MERGED_TRANSCRIPTION%" ^
  --context "%CONTEXT_FILE%" ^
  --prior-context "%PRIOR_CONTEXT_FILE%" ^
  --analysis-backend cursor_cli ^
  --cursor-cli-probe

set "TARA_EXIT=%ERRORLEVEL%"
popd
del /q "%TEMP_CONFIG%" >nul 2>&1

echo.
if "%TARA_EXIT%"=="0" (
  echo Done.
  echo Markdown summary:
  echo %USERPROFILE%\Documents\Projets\DM_Assistant\Record_session\Record23\transcriptions\analysis\session_summary.md
  echo YAML summary:
  echo %USERPROFILE%\Documents\Projets\DM_Assistant\Record_session\Record23\transcriptions\analysis\session_summary.yaml
) else (
  echo Tara failed with exit code %TARA_EXIT%.
  echo If Cursor reports an internal error, run:
  echo "%CURSOR_AGENT%" status
  echo "%CURSOR_AGENT%" models
)

exit /b %TARA_EXIT%
