@echo off
rem uvic-course-history one-liner.
rem   run.bat                -> build cache if needed, refresh, open report.html
rem   run.bat CSC 370        -> terminal card for that course
setlocal
cd /d "%~dp0"

where python >nul 2>nul
if %errorlevel%==0 (set "PY=python") else (
  where py >nul 2>nul
  if %errorlevel%==0 (set "PY=py") else (echo python 3 required & exit /b 1)
)

if not exist "data\offerings.json" (
  echo first run: syncing ~10 years of UVic banner data, few minutes, one time
  %PY% uvic_courses.py sync
) else (
  %PY% uvic_courses.py sync --years 3 >nul
)

if "%~1"=="" (
  %PY% uvic_courses.py report
  start "" "report.html"
) else (
  %PY% uvic_courses.py check %*
)
