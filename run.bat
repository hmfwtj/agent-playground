@echo off
chcp 65001 >nul
cd /d "%~dp0"

echo.
echo ============================================
echo   agent-playground
echo ============================================
echo.

where python >nul 2>nul
if errorlevel 1 (
    echo [ERROR] "python" is not found in PATH.
    echo.
    echo Try these instead:
    echo   1^) open a new terminal and run: python --version
    echo   2^) if it opens Microsoft Store, Python is not installed for this user
    echo.
    pause
    exit /b 1
)

rem Use run.py instead of "python -m agent_playground".
rem run.py inserts the project root into sys.path by itself, so it works
rem even if the current directory is not the project root.
python "%~dp0run.py"
set EXITCODE=%ERRORLEVEL%

echo.
echo ============================================
if not "%EXITCODE%"=="0" (
    echo   Exit code: %EXITCODE%
    echo   Something went wrong. The error is above.
    echo   Copy the whole text back to me.
) else (
    echo   Closed normally.
)
echo ============================================
echo.
pause
