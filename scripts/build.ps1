param([string]$Uv = "uv")
$ErrorActionPreference = "Stop"
Push-Location (Split-Path $PSScriptRoot -Parent)
try {
    & $Uv sync --frozen
    if ($LASTEXITCODE -ne 0) { throw "Ошибка зависимостей." }
    & $Uv run --frozen ruff check .
    if ($LASTEXITCODE -ne 0) { throw "Ruff обнаружил ошибки." }
    & $Uv run --frozen ruff format --check .
    if ($LASTEXITCODE -ne 0) { throw "Проверьте форматирование." }
    & $Uv run --frozen pytest -q
    if ($LASTEXITCODE -ne 0) { throw "Тесты не пройдены." }
    & $Uv run --frozen flet pack main.py --name EventSeat --icon assets/eventseat.ico --add-data "assets:assets" --product-name EventSeat --product-version 1.0.0 --file-version 1.0.0.0 --file-description EventSeat --distpath dist/release --yes
    if ($LASTEXITCODE -ne 0) { throw "Ошибка сборки." }
    $digest = Get-FileHash dist/release/EventSeat.exe -Algorithm SHA256
    ($digest.Hash.ToLower() + "  EventSeat.exe") | Set-Content dist/release/SHA256SUMS.txt -Encoding ascii
    $digest | Format-List
} finally { Pop-Location }
