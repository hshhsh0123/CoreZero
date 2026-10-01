@echo off
chcp 65001 >nul
cd /d "%~dp0"

where python >nul 2>nul
if errorlevel 1 (
  echo Python이 안 보여요. https://www.python.org 에서 설치할 때 "Add python.exe to PATH"를 체크해 주세요.
  pause
  exit /b 1
)
if "%DEEPSEEK_API_KEY%"=="" (
  echo DEEPSEEK_API_KEY가 없어요. 명령 프롬프트에서 아래처럼 한 번 설정한 뒤, 새 창에서 다시 실행해 주세요.
  echo   setx DEEPSEEK_API_KEY sk-여기에키
  pause
  exit /b 1
)

python -m pip install -q -r requirements.txt
echo.
echo 실시간 감시를 시작해요. 대시보드: http://127.0.0.1:8765  (끄려면 이 창에서 Ctrl+C)
echo 한국장은 09:00~15:30, 미국장은 밤 22:30~05:00(서머타임)이에요. 그 시간에 컴퓨터가 절전에 안 들어가게 해 주세요.
echo.
start "" http://127.0.0.1:8765
python -m trader live --serve 8765
pause
