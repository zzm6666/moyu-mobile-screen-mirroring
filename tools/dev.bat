@echo off
cd /d "%~dp0"
set "PYW=C:\Users\14704\.workbuddy\binaries\python\versions\3.13.12\pythonw.exe"
if not exist "%PYW%" set "PYW=pythonw"
start "" "%PYW%" "%~dp0mirror_app.py"
