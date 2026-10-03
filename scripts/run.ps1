param([string]$Uv = "uv")
$ErrorActionPreference = "Stop"
Push-Location (Split-Path $PSScriptRoot -Parent)
try {
    & $Uv sync --frozen
    if ($LASTEXITCODE -ne 0) { throw "Не удалось установить зависимости." }
    & $Uv run --frozen python main.py
    if ($LASTEXITCODE -ne 0) { throw "EventSeat завершился с ошибкой." }
} finally { Pop-Location }
