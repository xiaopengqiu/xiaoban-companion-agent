param([switch]$Install, [string]$PublicAppUrl = 'https://xiaoban-companion.abloom-haven-3343.chatgpt.site')
$ErrorActionPreference = 'Stop'
$taskRoot = Split-Path $PSScriptRoot -Parent
$taskPython = Join-Path $taskRoot '.venv-desktop\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $taskPython)) {
    & python -m venv (Join-Path $taskRoot '.venv-desktop')
    if ($LASTEXITCODE -ne 0) { throw 'Failed to create desktop build environment' }
    $Install = $true
}
Push-Location $taskRoot
try {
    if ($Install) {
        & $taskPython -m pip install -r requirements-desktop.txt
        if ($LASTEXITCODE -ne 0) { throw 'Desktop dependencies failed' }
    }
    & $taskPython -c 'from desktop.runtime import validate_url; import sys; validate_url(sys.argv[1])' $PublicAppUrl
    if ($LASTEXITCODE -ne 0) { throw 'Online URL must use HTTPS' }
    $taskDefaults = @{public_app_url=$PublicAppUrl} | ConvertTo-Json -Compress
    [IO.File]::WriteAllText((Join-Path $taskRoot 'desktop\defaults.json'), $taskDefaults + "`n", [Text.UTF8Encoding]::new($false))
    & $taskPython -m pytest tests/test_desktop.py -q
    if ($LASTEXITCODE -ne 0) { throw 'Desktop regression tests failed' }
    & $taskPython -m PyInstaller --noconfirm --clean --distpath desktop/dist --workpath desktop/build desktop/xiaoban.spec
    if ($LASTEXITCODE -ne 0) { throw 'Windows packaging failed' }
    $taskExe = Join-Path $taskRoot 'desktop\dist\Xiaoban-Windows.exe'
    $taskReport = Join-Path $taskRoot 'desktop\dist\packaged-smoke.json'
    $taskProcess = Start-Process -FilePath $taskExe -ArgumentList @('--self-test','--report',('"' + $taskReport + '"')) -WindowStyle Hidden -PassThru
    if (-not $taskProcess.WaitForExit(120000)) { Stop-Process -Id $taskProcess.Id -Force; throw 'Packaged smoke timed out' }
    if ($taskProcess.ExitCode -ne 0) { throw 'Packaged smoke failed; inspect desktop log and report' }
    $taskEvidence = Get-Content -LiteralPath $taskReport -Raw | ConvertFrom-Json
    if ($taskEvidence.status -ne 'passed' -or -not $taskEvidence.frozen -or -not $taskEvidence.server_stopped) { throw 'Packaged evidence incomplete' }
    $taskUiReport = Join-Path $taskRoot 'desktop\dist\ui-smoke.json'
    $taskUiData = Join-Path ([IO.Path]::GetTempPath()) ('xiaoban-release-ui-' + [guid]::NewGuid().ToString('N'))
    $taskUiProcess = Start-Process -FilePath $taskExe -ArgumentList @('--ui-smoke','--data-dir',('"' + $taskUiData + '"'),'--report',('"' + $taskUiReport + '"')) -WindowStyle Hidden -PassThru
    if (-not $taskUiProcess.WaitForExit(90000)) { Stop-Process -Id $taskUiProcess.Id -Force; throw 'Packaged WebView2 UI smoke timed out' }
    if ($taskUiProcess.ExitCode -ne 0) { throw 'Packaged WebView2 UI smoke failed' }
    $taskUiEvidence = Get-Content -LiteralPath $taskUiReport -Raw | ConvertFrom-Json
    if ($taskUiEvidence.status -ne 'passed' -or -not $taskUiEvidence.frozen -or -not $taskUiEvidence.server_stopped) { throw 'Packaged GUI evidence incomplete' }
    $taskRestartReport = Join-Path $taskRoot 'desktop\dist\ui-restart-smoke.json'
    $taskRestartProcess = Start-Process -FilePath $taskExe -ArgumentList @('--ui-smoke','--reuse-login','--data-dir',('"' + $taskUiData + '"'),'--report',('"' + $taskRestartReport + '"')) -WindowStyle Hidden -PassThru
    if (-not $taskRestartProcess.WaitForExit(90000)) { Stop-Process -Id $taskRestartProcess.Id -Force; throw 'Packaged restart smoke timed out' }
    if ($taskRestartProcess.ExitCode -ne 0) { throw 'Packaged login persistence failed' }
    $taskRestartEvidence = Get-Content -LiteralPath $taskRestartReport -Raw | ConvertFrom-Json
    if ($taskRestartEvidence.status -ne 'passed' -or -not $taskRestartEvidence.restored_login -or -not $taskRestartEvidence.server_stopped -or $taskRestartEvidence.port -ne $taskUiEvidence.port) { throw 'Packaged login persistence evidence incomplete' }
    Copy-Item -LiteralPath (Join-Path $taskRoot 'desktop\README.md') -Destination (Join-Path $taskRoot 'desktop\dist\README.txt') -Force
    & $taskPython -m desktop.collect_licenses
    if ($LASTEXITCODE -ne 0) { throw 'Third-party license collection failed' }
    $taskLicenses = Join-Path $taskRoot 'desktop\dist\LICENSES.txt'
    Copy-Item -LiteralPath (Join-Path $taskRoot 'desktop\LICENSES.txt') -Destination $taskLicenses -Force
    Compress-Archive -LiteralPath @($taskExe, (Join-Path $taskRoot 'desktop\dist\README.txt'), $taskLicenses) -DestinationPath (Join-Path $taskRoot 'desktop\dist\Xiaoban-Windows-portable.zip') -Force
    Get-FileHash -LiteralPath $taskExe -Algorithm SHA256 | Select-Object Path,Hash
} finally {
    Pop-Location
}
