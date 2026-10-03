param([string]$Executable = "dist/release/EventSeat.exe")
$ErrorActionPreference = "Stop"
Push-Location (Split-Path $PSScriptRoot -Parent)
$previousPassword = $env:EVENTSEAT_QA_PASSWORD
try {
    $binary = (Resolve-Path -LiteralPath $Executable).Path
    $runFolder = Join-Path (Get-Location) (".qa/release-" + [Guid]::NewGuid().ToString('N'))
    New-Item -ItemType Directory -Path $runFolder | Out-Null
    $env:EVENTSEAT_QA_PASSWORD = [Guid]::NewGuid().ToString('N')
    foreach ($phase in @("create", "resume")) {
        $verification = Start-Process -FilePath $binary -ArgumentList @('--verify-ui', ('"' + $runFolder + '"'), '--phase', $phase) -WindowStyle Hidden -PassThru
        if (-not $verification.WaitForExit(120000)) {
            Stop-Process -Id $verification.Id -Force
            throw "Превышено время проверки $phase."
        }
        $report = Get-Content -LiteralPath (Join-Path $runFolder "report-$phase.json") -Raw | ConvertFrom-Json
        if (-not $report.success) { throw $report.error }
        Write-Output ("Проверка {0}: успешно, проверок {1}." -f $phase, $report.checks.Count)
    }
    Write-Output "Отчёты и снимки: $runFolder"
} finally {
    $env:EVENTSEAT_QA_PASSWORD = $previousPassword
    Pop-Location
}
