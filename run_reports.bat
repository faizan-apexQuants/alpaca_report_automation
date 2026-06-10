@echo off
REM ============================================================
REM  Apex Reports - Client Performance PDF Generator
REM ============================================================
REM  Interactive wrapper around run_reports.py. Handles venv
REM  activation, dependency check, period selection, and opens
REM  the output folder when the run completes.
REM ============================================================

setlocal EnableDelayedExpansion
chcp 65001 >nul
pushd "%~dp0"

echo.
echo ============================================================
echo   APEX REPORTS  -  Client Performance PDF
echo ============================================================

REM ---- Locate and activate venv (.venv preferred, venv fallback)
set "VENV_ACTIVATE="
if exist ".venv\Scripts\activate.bat"  set "VENV_ACTIVATE=.venv\Scripts\activate.bat"
if not defined VENV_ACTIVATE if exist "venv\Scripts\activate.bat" set "VENV_ACTIVATE=venv\Scripts\activate.bat"

if not defined VENV_ACTIVATE (
    echo [ERROR] No virtualenv found ^(.venv\ or venv\^).
    echo         Create one and install requirements:
    echo             python -m venv .venv
    echo             .venv\Scripts\activate
    echo             pip install -r requirements.txt
    echo             python -m playwright install chromium
    goto :END_FAIL
)
echo Activating venv: %VENV_ACTIVATE%
call "%VENV_ACTIVATE%"

REM ---- Sanity-check .env
if not exist ".env" (
    echo.
    echo [WARN] No .env file found. Copy .env.example to .env and set API_KEY.
    set /p CONT="Continue anyway? (y/N): "
    if /I not "!CONT!"=="y" goto :END_FAIL
)

REM ---- Period selection menu
echo.
echo Select reporting period:
echo   [1] Daily              (last 1 day)
echo   [2] Weekly             (last 7 days)
echo   [3] Monthly            (last 30 days)   [default]
echo   [4] Last Month         (previous full calendar month)
echo   [5] All History        (lifetime)
echo   [6] Custom Range       (you supply --from and --to)
echo   [7] Specific Month     (YYYY-MM)
echo.
set "CHOICE=3"
set /p CHOICE="Choice [1-7] (default 3): "

set "PERIOD_ARGS="
if "%CHOICE%"=="1" set "PERIOD_ARGS=--period daily"
if "%CHOICE%"=="2" set "PERIOD_ARGS=--period weekly"
if "%CHOICE%"=="3" set "PERIOD_ARGS=--period monthly"
if "%CHOICE%"=="4" set "PERIOD_ARGS=--period last-month"
if "%CHOICE%"=="5" set "PERIOD_ARGS=--period all"

if "%CHOICE%"=="6" (
    set "DATE_FROM="
    set "DATE_TO="
    set /p DATE_FROM="  From date (YYYY-MM-DD): "
    set /p DATE_TO="  To   date (YYYY-MM-DD): "
    if "!DATE_FROM!"=="" goto :BAD_INPUT
    if "!DATE_TO!"==""   goto :BAD_INPUT
    set "PERIOD_ARGS=--period custom --from !DATE_FROM! --to !DATE_TO!"
)

if "%CHOICE%"=="7" (
    set "MONTH_VAL="
    set /p MONTH_VAL="  Month (YYYY-MM): "
    if "!MONTH_VAL!"=="" goto :BAD_INPUT
    set "PERIOD_ARGS=--period monthly --month !MONTH_VAL!"
)

if not defined PERIOD_ARGS (
    echo [ERROR] Invalid choice: %CHOICE%
    goto :END_FAIL
)

REM ---- Optional: theme
echo.
set "THEME=purple"
set /p THEME="Theme (purple/yellow) [default purple]: "
if /I not "!THEME!"=="yellow" set "THEME=purple"

REM ---- Optional: single client filter
echo.
set "CLIENT_ARG="
set /p CLIENT_NAME="Client name (blank = all clients): "
if not "!CLIENT_NAME!"=="" set "CLIENT_ARG=--client ""!CLIENT_NAME!"""

REM ---- Optional: output directory
echo.
set "OUTPUT_DIR="
set /p OUTPUT_DIR="Output dir (blank = default ./out): "
set "OUTPUT_ARG="
if not "!OUTPUT_DIR!"=="" set "OUTPUT_ARG=--output-dir ""!OUTPUT_DIR!"""

REM ---- Summary before run
echo.
echo ------------------------------------------------------------
echo   Period args : %PERIOD_ARGS%
echo   Theme       : %THEME%
echo   Client      : %CLIENT_NAME%  ^(blank = all^)
echo   Output dir  : %OUTPUT_DIR%   ^(blank = default ./out^)
echo ------------------------------------------------------------
echo.

set "CMD=python run_reports.py %PERIOD_ARGS% --theme %THEME% %CLIENT_ARG% %OUTPUT_ARG%"
echo Running: %CMD%
echo.

%CMD%
set "EXIT_CODE=%ERRORLEVEL%"

if not "%EXIT_CODE%"=="0" (
    echo.
    echo [ERROR] run_reports.py exited with code %EXIT_CODE%.
    goto :END_FAIL
)

REM ---- Open output folder
set "OPEN_DIR=%OUTPUT_DIR%"
if "%OPEN_DIR%"=="" set "OPEN_DIR=out"
if exist "%OPEN_DIR%" (
    echo.
    echo Opening output folder: %OPEN_DIR%
    start "" "%OPEN_DIR%"
)

echo.
echo ============================================================
echo   DONE.
echo ============================================================
goto :END_OK

:BAD_INPUT
echo [ERROR] Missing required input for the selected period.
goto :END_FAIL

:END_FAIL
popd
echo.
pause
endlocal
exit /b 1

:END_OK
popd
echo.
pause
endlocal
exit /b 0
