@echo off
rem Registers a Windows Task Scheduler task that runs scripts\run_pc.bat every
rem 15 minutes. Run this once. Remove it with:
rem   schtasks /Delete /TN "SP500 Trading Agent" /F
schtasks /Create /F /TN "SP500 Trading Agent" /SC MINUTE /MO 15 /TR "\"%~dp0run_pc.bat\""
if errorlevel 1 (
  echo Could not create the task.
  exit /b 1
)
echo Task "SP500 Trading Agent" created - it runs every 15 minutes while you are logged in.
echo Check logs\pc_runner.log to see what it did.
