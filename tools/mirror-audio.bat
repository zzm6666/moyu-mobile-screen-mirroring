@echo off
cd /d "%~dp0scrcpy-win64-v5.0.1"
adb.exe connect 192.168.137.198:38493 >nul 2>&1
set "PYW=C:\Users\14704\.workbuddy\binaries\python\versions\3.13.12\pythonw.exe"
if not exist "%PYW%" set "PYW=pythonw"
start "" "%PYW%" "%~dp0overlay_close.py" phone-corner
scrcpy.exe -s 192.168.137.198:38493 --window-title=phone-corner --window-borderless --always-on-top --window-x=1636 --window-y=448 --window-width=260 --window-height=572 --max-fps=60 --video-bit-rate=4M -m 720
