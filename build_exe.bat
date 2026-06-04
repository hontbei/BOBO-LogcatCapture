@echo off
setlocal EnableExtensions
title Build BOBOLogcatCapture

cd /d "%~dp0"

set "APP_NAME=BOBOLogcatCapture"
set "ENTRY_FILE=logcat_capture_gui.py"
set "BUILD_ROOT=build"
set "DIST_DIR=%BUILD_ROOT%\%APP_NAME%"
set "WORK_DIR=%BUILD_ROOT%\pyinstaller-work"
set "SPEC_DIR=%BUILD_ROOT%"
set "ICON_FILE=assets\sage.ico"
set "ICON_FILE_ABS=%CD%\%ICON_FILE%"
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
    echo Then run build_exe.bat again.
    echo.
    pause
    exit /b 1
)

if not exist "%ENTRY_FILE%" (
    echo [ERROR] %ENTRY_FILE% was not found.
    echo Please keep build_exe.bat in the project root folder.
    echo.
    pause
    exit /b 1
)

if not exist "requirements.txt" (
    echo [ERROR] requirements.txt was not found.
    echo Please keep requirements.txt in the project root folder.
    echo.
    pause
    exit /b 1
)

if not exist "%ICON_FILE%" (
    echo [ERROR] %ICON_FILE% was not found.
    echo Please keep the project icon at assets\sage.ico.
    echo.
    pause
    exit /b 1
)

echo Checking runtime dependencies...
%PYTHON_CMD% -c "import customtkinter" >nul 2>nul
if errorlevel 1 (
    echo [INFO] Installing app dependencies from requirements.txt...
    %PYTHON_CMD% -m pip install -r "requirements.txt"
    if errorlevel 1 (
        echo.
        echo [ERROR] Failed to install app dependencies.
        echo Please check your network, then run:
        echo %PYTHON_CMD% -m pip install -r requirements.txt
        echo.
        pause
        exit /b 1
    )
)

%PYTHON_CMD% -c "import PyInstaller" >nul 2>nul
if errorlevel 1 (
    echo [INFO] PyInstaller is missing. Installing PyInstaller...
    %PYTHON_CMD% -m pip install pyinstaller
    if errorlevel 1 (
        echo.
        echo [ERROR] Failed to install PyInstaller.
        echo Please check your network, then run:
        echo %PYTHON_CMD% -m pip install pyinstaller
        echo.
        pause
        exit /b 1
    )
)

if not exist "%BUILD_ROOT%" mkdir "%BUILD_ROOT%"

echo.
echo Building %APP_NAME%.exe...
echo Icon:
echo %ICON_FILE_ABS%
%PYTHON_CMD% -m PyInstaller ^
    --noconfirm ^
    --clean ^
    --onefile ^
    --windowed ^
    --name "%APP_NAME%" ^
    --icon "%ICON_FILE_ABS%" ^
    --add-data "%ICON_FILE_ABS%;assets" ^
    --distpath "%DIST_DIR%" ^
    --workpath "%WORK_DIR%" ^
    --specpath "%SPEC_DIR%" ^
    --collect-all customtkinter ^
    "%ENTRY_FILE%"

if errorlevel 1 (
    echo.
    echo [ERROR] Build failed.
    echo The output above may contain the reason.
    echo.
    pause
    exit /b 1
)

echo.
echo [OK] Build completed.
echo Output:
echo %CD%\%DIST_DIR%\%APP_NAME%.exe
echo.
pause
endlocal
