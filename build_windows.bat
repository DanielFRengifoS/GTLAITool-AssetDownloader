@echo off
setlocal EnableExtensions EnableDelayedExpansion
cd /d "%~dp0"
title GTL AI Asset Downloader - Automatic Windows Build

set "APP_NAME=Asset Downloader"
set "LOG_FILE=%~dp0build.log"
set "PYTHON_EXE="

echo ============================================================
echo   GTL AI ASSET DOWNLOADER - AUTOMATIC WINDOWS BUILD
echo ============================================================
echo.
echo This builder will:
echo   1. Find Python 3.11 or install it using winget.
echo   2. Prepare the build environment.
echo   3. Create Asset Downloader.exe with its custom icon.
echo.
echo The final app will be: dist\Asset Downloader.exe
echo.
echo Build log: "%LOG_FILE%"
echo.

> "%LOG_FILE%" echo GTL AI Asset Downloader build log
>> "%LOG_FILE%" echo Started: %DATE% %TIME%
>> "%LOG_FILE%" echo Project: %CD%
>> "%LOG_FILE%" echo.

echo [1/5] Looking for Python 3.11...

REM First choice: Python launcher.
where py >nul 2>nul
if not errorlevel 1 (
    for /f "delims=" %%P in ('py -3.11 -c "import sys; print(sys.executable)" 2^>nul') do set "PYTHON_EXE=%%P"
)

REM Second choice: common per-user and system installation paths.
if not defined PYTHON_EXE if exist "%LocalAppData%\Programs\Python\Python311\python.exe" set "PYTHON_EXE=%LocalAppData%\Programs\Python\Python311\python.exe"
if not defined PYTHON_EXE if exist "%LocalAppData%\Programs\Python\Python311-64\python.exe" set "PYTHON_EXE=%LocalAppData%\Programs\Python\Python311-64\python.exe"
if not defined PYTHON_EXE if exist "%ProgramFiles%\Python311\python.exe" set "PYTHON_EXE=%ProgramFiles%\Python311\python.exe"
if not defined PYTHON_EXE if exist "%ProgramFiles(x86)%\Python311\python.exe" set "PYTHON_EXE=%ProgramFiles(x86)%\Python311\python.exe"

REM If not found, bootstrap Python 3.11 through Windows Package Manager.
if not defined PYTHON_EXE (
    echo Python 3.11 was not found.
    echo.
    where winget >nul 2>nul
    if errorlevel 1 goto :no_winget

    echo [2/5] Installing Python 3.11 automatically with winget...
    echo This may take a few minutes. Windows may ask for permission.
    >> "%LOG_FILE%" echo Python 3.11 not found. Installing with winget.
    winget install --id Python.Python.3.11 --exact --scope user --silent --accept-package-agreements --accept-source-agreements >> "%LOG_FILE%" 2>&1
    if errorlevel 1 (
        echo.
        echo winget could not install Python automatically.
        echo See build.log for details.
        goto :show_error
    )

    REM Refresh detection after installation.
    if exist "%LocalAppData%\Programs\Python\Python311\python.exe" set "PYTHON_EXE=%LocalAppData%\Programs\Python\Python311\python.exe"
    if not defined PYTHON_EXE if exist "%LocalAppData%\Programs\Python\Python311-64\python.exe" set "PYTHON_EXE=%LocalAppData%\Programs\Python\Python311-64\python.exe"
    if not defined PYTHON_EXE (
        where py >nul 2>nul
        if not errorlevel 1 (
            for /f "delims=" %%P in ('py -3.11 -c "import sys; print(sys.executable)" 2^>nul') do set "PYTHON_EXE=%%P"
        )
    )
)

if not defined PYTHON_EXE goto :python_install_failed

echo Found Python:
echo "%PYTHON_EXE%"
>> "%LOG_FILE%" echo Python executable: %PYTHON_EXE%
"%PYTHON_EXE%" --version
if errorlevel 1 goto :failed

echo.
echo [3/5] Preparing isolated build environment...
if not exist ".venv\Scripts\python.exe" (
    "%PYTHON_EXE%" -m venv .venv >> "%LOG_FILE%" 2>&1
    if errorlevel 1 goto :failed
)

set "BUILD_PYTHON=%CD%\.venv\Scripts\python.exe"

echo.
echo [4/5] Installing build dependencies...
echo This is normally needed only on the first build.
"%BUILD_PYTHON%" -m pip install --upgrade pip >> "%LOG_FILE%" 2>&1
if errorlevel 1 goto :failed
"%BUILD_PYTHON%" -m pip install --no-cache-dir --upgrade --force-reinstall -r requirements.txt >> "%LOG_FILE%" 2>&1
if errorlevel 1 goto :failed
"%BUILD_PYTHON%" -m pip show gallery-dl >> "%LOG_FILE%" 2>&1
>> "%LOG_FILE%" echo gallery-dl source: https://codeberg.org/mikf/gallery-dl/archive/master.tar.gz
if errorlevel 1 goto :failed

echo.
echo [5/5] Building Asset Downloader.exe...
echo This can take several minutes.
"%BUILD_PYTHON%" -m unittest discover -s tests -v >> "%LOG_FILE%" 2>&1
if errorlevel 1 goto :failed
"%BUILD_PYTHON%" -m PyInstaller --noconfirm --clean --onefile --windowed ^
    --name "%APP_NAME%" ^
    --distpath "dist" ^
    --workpath "build" ^
    --specpath "build" ^
    --icon "%CD%\assets\GTL_AI_Asset_Downloader.ico" ^
    --add-data "%CD%\assets;assets" ^
    --collect-all gallery_dl ^
    --collect-all requests ^
    --collect-all certifi ^
    --collect-all PIL ^
    --hidden-import gallery_dl.__main__ ^
    --hidden-import httpx ^
    main.py >> "%LOG_FILE%" 2>&1
if errorlevel 1 goto :failed

if not exist "dist\Asset Downloader.exe" goto :failed

echo.
echo ============================================================
echo   BUILD COMPLETE
echo ============================================================
echo.
echo Your application is ready:
echo "%CD%\dist\Asset Downloader.exe"
echo.
echo This is the actual app. Double-click it to open the GUI.
echo It includes the custom GTL AI icon and does not need Python.
echo.
echo Opening the output folder...
explorer "%CD%\dist" >nul 2>nul
echo.
echo Press any key to close this window.
pause >nul
exit /b 0

:no_winget
echo Windows Package Manager (winget) was not found.
echo.
echo Please install Python 3.11 x64 from:
echo https://www.python.org/downloads/release/python-3119/
echo During setup, enable "Add python.exe to PATH".
echo Then run this builder again.
>> "%LOG_FILE%" echo ERROR: winget not found; automatic Python installation unavailable.
goto :show_error

:python_install_failed
echo Python installation did not complete or its executable could not be located.
echo Check build.log. You can rerun this script after installing Python 3.11 x64.
>> "%LOG_FILE%" echo ERROR: Python 3.11 executable not found after winget installation.
goto :show_error

:failed
echo.
echo BUILD FAILED. The detailed error is saved in:
echo "%LOG_FILE%"
>> "%LOG_FILE%" echo.
>> "%LOG_FILE%" echo Build failed at: %DATE% %TIME%
goto :show_error

:show_error
echo.
echo Press any key to close this window.
pause >nul
exit /b 1
