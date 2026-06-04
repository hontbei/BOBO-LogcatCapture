@echo off
setlocal EnableExtensions
title Logcat Capture Launcher

cd /d "%~dp0"

set "PYTHON_CMD="

py -3 --version >nul 2>nul
if not errorlevel 1 set "PYTHON_CMD=py -3"

if "%PYTHON_CMD%"=="" (
    python --version >nul 2>nul
    if not errorlevel 1 set "PYTHON_CMD=python"
)

if "%PYTHON_CMD%"=="" (
    echo [ERROR] Python 3 was not found.
    echo.
    echo Please install Python 3 and enable "Add python.exe to PATH".
    echo Then double-click run_logcat_capture.bat again.
    echo.
    pause
    exit /b 1
)

if not exist "logcat_capture_gui.py" (
    echo [ERROR] logcat_capture_gui.py was not found.
    echo Please keep this bat file in the same folder as logcat_capture_gui.py.
    echo.
    pause
    exit /b 1
)

%PYTHON_CMD% -c "import customtkinter" >nul 2>nul
if errorlevel 1 (
    echo [INFO] customtkinter is missing in the selected Python environment.
    echo.
    echo This launcher can install dependencies from requirements.txt.
    echo The install may fail if this computer cannot access the network.
    echo.
    choice /C YN /N /M "Install dependencies now? [Y/N]: "
    if errorlevel 2 (
        echo.
        echo Install cancelled. You can run this manually:
        echo %PYTHON_CMD% -m pip install -r requirements.txt
        echo.
        pause
        exit /b 1
    )

    if not exist "requirements.txt" (
        echo.
        echo [ERROR] requirements.txt was not found.
        echo Please keep requirements.txt in the same folder as this bat file.
        echo.
        pause
        exit /b 1
    )

    echo.
    echo Installing dependencies, please wait...
    %PYTHON_CMD% -m pip install -r "requirements.txt"
    if errorlevel 1 (
        echo.
        echo [ERROR] Dependency installation failed.
        echo Please check your network, or run this manually:
        echo %PYTHON_CMD% -m pip install -r requirements.txt
        echo.
        pause
        exit /b 1
    )
)

echo Starting Logcat Capture...
%PYTHON_CMD% "logcat_capture_gui.py"
if errorlevel 1 (
    echo.
    echo [ERROR] The application exited with an error.
    echo The output above may contain the reason.
    echo.
    pause
    exit /b 1
)

endlocal
