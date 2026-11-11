#!/bin/bash
echo "☀️ Starting GlowBoard..."
echo ""
echo "Installing dependencies (first time only)..."
pip install -r requirements.txt --quiet

echo ""
echo "Starting server..."
echo "Open your browser to: http://localhost:8000"
echo ""
echo "Press Ctrl+C to stop"
echo ""

cd backend
python main.py
