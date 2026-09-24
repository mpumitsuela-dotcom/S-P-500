@echo off
rem Backup runner for your Windows PC. Task Scheduler calls this every 15
rem minutes (see scripts\install_pc_task.bat). It only trades when a session is
rem due AND the cloud runner hasn't already done it - see scheduler\run_shared.py.
cd /d "%~dp0.."
if not exist logs mkdir logs
set SP500_RUNNER_ROLE=backup
if "%SP500_RUNNER_NAME%"=="" set SP500_RUNNER_NAME=pc-%COMPUTERNAME%
if exist .venv\Scripts\python.exe (
  .venv\Scripts\python.exe scheduler\run_shared.py >> logs\pc_runner.log 2>&1
) else (
  python scheduler\run_shared.py >> logs\pc_runner.log 2>&1
)
