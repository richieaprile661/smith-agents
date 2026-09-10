@echo off
rem Launch the widget from this source tree, with no console window.
rem "pyw" on PATH can resolve to a different interpreter than "py", so ask "py"
rem which Python it is and use the pythonw.exe sitting next to it. That pairing
rem matters: "py -m pip install" writes the dependencies into the same one.
rem
rem An installed copy has a "smith-agents" command instead; this is for
rem running the code where it lives.
setlocal
set "PYW="
for /f "usebackq delims=" %%i in (`py -c "import os,sys;print(os.path.join(os.path.dirname(sys.executable),'pythonw.exe'))" 2^>nul`) do set "PYW=%%i"
if not defined PYW goto :nolauncher
if not exist "%PYW%" goto :nolauncher
start "" /d "%~dp0" "%PYW%" -m smith_agents
exit /b 0

:nolauncher
echo.
echo Could not find the Python launcher (py.exe).
echo.
echo Install Python 3.10 or newer from https://www.python.org/downloads/
echo and keep "py launcher" ticked under the installer's optional features,
echo then run this again. Python from the Microsoft Store ships without it.
echo.
pause
exit /b 1
