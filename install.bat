@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo Installing Python dependencies...
python -m pip install -r requirements.txt
echo.
where ffmpeg >nul 2>nul
if errorlevel 1 (
  echo [WARN] ffmpeg not found. Install it with:
  echo        winget install Gyan.FFmpeg
) else (
  echo ffmpeg OK
)
echo.
echo Done. Double-click start.bat to launch.
pause
