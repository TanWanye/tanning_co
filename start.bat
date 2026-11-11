@echo off
echo.
echo ☀️ Starting The Tanning Co. Coventry App...
echo.
echo Installing dependencies (first time only, please wait)...
pip install -r requirements.txt --quiet
echo.
echo Starting server...
echo.
echo Open your browser and go to: http://localhost:8000
echo.
echo Press Ctrl+C to stop the app
echo.
cd backend
python main.py
pause
