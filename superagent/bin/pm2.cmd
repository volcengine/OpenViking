@echo off
rem Copyright © 2026 Volcano Engine (火山引擎) and the OpenViking contributors.
rem SPDX-License-Identifier: AGPL-3.0
rem
rem The release's pinned pm2, run by the release's own Node when it has one
rem (an installed Windows release ships runtime\, a POSIX one node\bin\;
rem a source checkout uses the node on PATH). Bin\pm2 does the same on POSIX.
setlocal
set "ROOT=%~dp0.."
set "NODE=%ROOT%\runtime\node.exe"
if not exist "%NODE%" set "NODE=%ROOT%\node\bin\node.exe"
if not exist "%NODE%" set "NODE=node"
"%NODE%" "%ROOT%\node_modules\pm2\bin\pm2" %*
