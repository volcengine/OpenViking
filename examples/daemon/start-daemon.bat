@echo off
REM Start OpenViking with Active Daemon enabled on Windows

set OV_DAEMON_ENABLED=true
set OV_DAEMON_WATCH_DIR=%USERPROFILE%\.claude\projects
set OV_DAEMON_BATCH_LINES=50
set OV_DAEMON_BATCH_SECONDS=300

echo Starting OpenViking with Active Daemon...
openviking serve %*
