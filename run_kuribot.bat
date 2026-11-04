@echo off
rem Always run from the folder this script lives in, even when started from elsewhere.
cd /d "%~dp0"

if not exist .venv (
    echo Creating virtual environment...
    python -m venv .venv
    call .venv\Scripts\activate
    python -m pip install --upgrade pip
    pip install -r requirements.txt
) else (
    call .venv\Scripts\activate
    rem Quick no-op when everything is already installed.
    pip install -q -r requirements.txt
)

python bot.py
pause
