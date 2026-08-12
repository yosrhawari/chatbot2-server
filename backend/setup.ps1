param(
    [string]$Model = "qwen2.5:3b"
)

$ErrorActionPreference = "Stop"
$BackendDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$ProjectDir = Split-Path -Parent $BackendDir
$ModelsDir = "$BackendDir\models"
$OllamaModelsDir = "$ModelsDir\ollama"

Write-Host "[1/4] Creating model directories..." -ForegroundColor Cyan
New-Item -ItemType Directory -Path $ModelsDir -Force | Out-Null
New-Item -ItemType Directory -Path $OllamaModelsDir -Force | Out-Null

Write-Host "[2/4] Setting OLLAMA_MODELS to $OllamaModelsDir..." -ForegroundColor Cyan
try {
    [Environment]::SetEnvironmentVariable("OLLAMA_MODELS", $OllamaModelsDir, "User")
} catch {
    Write-Host "  Warning: Unable to set user environment variable." -ForegroundColor Yellow
}
$env:OLLAMA_MODELS = $OllamaModelsDir

Write-Host "[3/4] Checking for Ollama..." -ForegroundColor Cyan
$ollamaExe = Get-Command "ollama" -ErrorAction SilentlyContinue
if (-not $ollamaExe) {
    Write-Host "  Ollama not found. Downloading and installing..." -ForegroundColor Yellow
    $installer = "$env:TEMP\OllamaSetup.exe"
    Invoke-WebRequest -Uri "https://ollama.com/download/OllamaSetup.exe" -OutFile $installer
    Start-Process -Wait -FilePath $installer -ArgumentList "/S"
    Remove-Item $installer -Force
    # Refresh PATH so ollama is available
    $env:Path = [Environment]::GetEnvironmentVariable("Path", "Machine") + ";" + [Environment]::GetEnvironmentVariable("Path", "User")
    Write-Host "  Ollama installed." -ForegroundColor Green
} else {
    Write-Host "  Ollama already installed." -ForegroundColor Green
}

Write-Host "[4/4] Pulling model: $Model..." -ForegroundColor Cyan
& "ollama" pull $Model

Write-Host "`nDone! Model storage layout:" -ForegroundColor Cyan
Write-Host "  HuggingFace models : $ModelsDir\huggingface\"
Write-Host "  Ollama models      : $OllamaModelsDir"
Write-Host "`nNext steps:"
Write-Host "  1. Copy .env.example to .env (or just use defaults)"
Write-Host "  2. Run the backend: uvicorn app:app --reload"
