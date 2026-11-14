@echo off
setlocal
cd /d "%~dp0"

where py >nul 2>&1
if not errorlevel 1 (
    set "PYTHON=py -3"
) else (
    where python >nul 2>&1
    if errorlevel 1 (
        echo Python 3 was not found. Install Python and add it to PATH.
        pause
        exit /b 1
    )
    set "PYTHON=python"
)

echo Installing project requirements...
%PYTHON% -m pip install -r requirements.txt
if errorlevel 1 goto build_failed

echo Installing PyInstaller...
%PYTHON% -m pip install pyinstaller
if errorlevel 1 goto build_failed

echo Building AtiChat...
%PYTHON% -m PyInstaller --noconfirm --clean AtiChat.spec
if errorlevel 1 goto build_failed

echo.
echo Build completed: dist\AtiChat\AtiChat.exe
pause
exit /b 0

:build_failed
echo.
echo Build failed. Review the error messages above.
pause
exit /b 1
