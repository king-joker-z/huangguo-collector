@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo ============================================================
echo   Huangguo Collector  /  黄果采集器
echo   http://127.0.0.1:8765
echo ============================================================
echo.
where ffmpeg >nul 2>nul
if errorlevel 1 (
  echo [WARN] ffmpeg not found in PATH. Downloads will fail.
  echo        Install: winget install Gyan.FFmpeg
  echo.
)
start "" /b powershell -NoProfile -WindowStyle Hidden -Command "Start-Sleep -Seconds 2; Start-Process 'http://127.0.0.1:8765'"
python -m app
pause
