@echo off
chcp 65001 >nul
cd /d "%~dp0"
set IMAGE=jokermelove/huangguo-collector:1.11.3
set TAR=huangguo-collector-1.11.3.tar

echo Building %IMAGE% ...
docker build --platform linux/amd64 -t %IMAGE% .
if errorlevel 1 (
  echo Build failed.
  pause
  exit /b 1
)

echo Saving image to %TAR% ...
docker save %IMAGE% -o %TAR%
echo Done.
echo Import on NAS: docker load -i %TAR%
pause




