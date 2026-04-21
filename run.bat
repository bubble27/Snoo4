@echo off
:loop
python main.py %*
if %errorlevel% == 42 (
    echo Restarting...
    goto loop
)
pause
