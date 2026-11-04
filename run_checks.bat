@echo off
rem KuriBot offline checks: battles in every mode, then a click-through of every player
rem command's menus, on a sandbox copy of the database. Nothing reaches Discord or players.
rem Run this after changing the bot's code, before restarting the real bot.
cd /d "%~dp0"

if not exist .venv (
    echo Run run_kuribot.bat once first, so the Python environment exists.
    pause
    exit /b 1
)
call .venv\Scripts\activate

set FAILED=0
python devtools\tests\battle_check.py || set FAILED=1
python devtools\tests\menu_check.py || set FAILED=1

echo.
if %FAILED%==0 (echo All checks passed.) else (echo Some checks FAILED - see the output above.)
pause
