# AI Revenue Recovery - Startup Script
# Run this script to start both backend and frontend

Write-Host "🚀 Starting AI Revenue Recovery Orchestrator..." -ForegroundColor Cyan
Write-Host ""

# Start Backend
Write-Host "📦 Starting Backend Server..." -ForegroundColor Yellow
$backendPath = Join-Path $PSScriptRoot "backend"
Start-Process powershell -ArgumentList "-NoExit", "-Command", "Set-Location '$backendPath'; python -m uvicorn app.main:app --reload --host 0.0.0.0 --port 8000"

# Wait a bit for backend to start
Start-Sleep -Seconds 3

# Start Frontend
Write-Host "🎨 Starting Frontend Server..." -ForegroundColor Yellow
Start-Process powershell -ArgumentList "-NoExit", "-Command", "cd '$PSScriptRoot\frontend'; python -m http.server 3000"

Write-Host ""
Write-Host "✅ Services Started!" -ForegroundColor Green
Write-Host ""
Write-Host "📊 Dashboard URLs:" -ForegroundColor Cyan
Write-Host "   Frontend:  http://localhost:3000" -ForegroundColor White
Write-Host "   Backend:   http://localhost:8000/dashboard/" -ForegroundColor White
Write-Host "   API Docs:  http://localhost:8000/docs" -ForegroundColor White
Write-Host ""
Write-Host "⚠️  Press Ctrl+C in each terminal window to stop servers" -ForegroundColor Yellow
