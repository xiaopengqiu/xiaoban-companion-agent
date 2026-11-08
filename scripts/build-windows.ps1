param(
    [switch]$Install,
    [string]$PublicAppUrl = 'https://xiaoban-companion.abloom-haven-3343.chatgpt.site',
    [string]$AppVersion,
    [string]$OutputDir
)
$ErrorActionPreference = 'Stop'
$taskRoot = Split-Path $PSScriptRoot -Parent
$taskUpdateConfig = Get-Content -LiteralPath (Join-Path $taskRoot 'desktop\update-config.json') -Raw | ConvertFrom-Json
if (-not $AppVersion) { $AppVersion = $taskUpdateConfig.version }
if ($AppVersion -notmatch '^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$') { throw 'AppVersion must be three nonnegative integers, for example 0.4.0' }
$taskVersionParts = @($AppVersion.Split('.') | ForEach-Object { [int]$_ })
if (@($taskVersionParts | Where-Object { $_ -gt 65535 }).Count) { throw 'Version components must fit Windows file-version fields (0..65535)' }
if (-not $OutputDir) { $OutputDir = Join-Path $taskRoot ('desktop\dist\' + $AppVersion) }
if (-not [IO.Path]::IsPathRooted($OutputDir)) { $OutputDir = Join-Path $taskRoot $OutputDir }
$taskOutput = [IO.Path]::GetFullPath($OutputDir)
if ($taskOutput.TrimEnd('\') -eq (Join-Path $taskRoot 'desktop\dist')) { throw 'Use a versioned OutputDir to preserve existing portable releases' }
$taskExe = Join-Path $taskOutput 'Xiaoban-Windows.exe'
foreach ($taskExistingProcess in @(Get-Process -Name 'Xiaoban-Windows' -ErrorAction SilentlyContinue)) {
    if ($taskExistingProcess.Path -and [IO.Path]::GetFullPath($taskExistingProcess.Path) -eq $taskExe) { throw 'Output executable is running; choose another OutputDir' }
}
$taskBuild = Join-Path $taskRoot ('desktop\build\' + $AppVersion)
$taskResources = Join-Path $taskBuild 'resources'
New-Item -ItemType Directory -Path $taskOutput, $taskResources -Force | Out-Null
$taskPreviousResources = $env:XIAOBAN_BUILD_RESOURCES
$taskPreviousVersionFile = $env:XIAOBAN_BUILD_VERSION_FILE
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
    [IO.File]::WriteAllText((Join-Path $taskResources 'defaults.json'), $taskDefaults + "`n", [Text.UTF8Encoding]::new($false))
    $taskUpdateConfig.version = $AppVersion
    [IO.File]::WriteAllText((Join-Path $taskResources 'update-config.json'), ($taskUpdateConfig | ConvertTo-Json -Compress) + "`n", [Text.UTF8Encoding]::new($false))
    $taskVersionFile = Join-Path $taskResources 'version-info.txt'
    $taskVersionTuple = ($taskVersionParts + @(0)) -join ', '
    $taskVersionInfo = @"
VSVersionInfo(ffi=FixedFileInfo(filevers=($taskVersionTuple), prodvers=($taskVersionTuple), mask=0x3f, flags=0x0, OS=0x40004, fileType=0x1, subtype=0x0, date=(0,0)), kids=[StringFileInfo([StringTable('080404B0', [StringStruct('FileDescription','Xiaoban Companion'), StringStruct('FileVersion','$AppVersion'), StringStruct('ProductName','Xiaoban'), StringStruct('ProductVersion','$AppVersion'), StringStruct('OriginalFilename','Xiaoban-Windows.exe')])]), VarFileInfo([VarStruct('Translation',[2052,1200])])])
"@
    [IO.File]::WriteAllText($taskVersionFile, $taskVersionInfo, [Text.UTF8Encoding]::new($false))
    $env:XIAOBAN_BUILD_RESOURCES = $taskResources
    $env:XIAOBAN_BUILD_VERSION_FILE = $taskVersionFile
    & $taskPython -m pytest tests/test_desktop.py tests/test_update_ui.py tests/test_updater.py -q
    if ($LASTEXITCODE -ne 0) { throw 'Desktop regression tests failed' }
    & $taskPython -m PyInstaller --noconfirm --clean --distpath $taskOutput --workpath $taskBuild desktop/xiaoban.spec
    if ($LASTEXITCODE -ne 0) { throw 'Windows packaging failed' }
    $taskReport = Join-Path $taskOutput 'packaged-smoke.json'
    $taskProcess = Start-Process -FilePath $taskExe -ArgumentList @('--self-test','--report',('"' + $taskReport + '"')) -WindowStyle Hidden -PassThru
    if (-not $taskProcess.WaitForExit(120000)) { Stop-Process -Id $taskProcess.Id -Force; throw 'Packaged smoke timed out' }
    if ($taskProcess.ExitCode -ne 0) { throw 'Packaged smoke failed; inspect desktop log and report' }
    $taskEvidence = Get-Content -LiteralPath $taskReport -Raw | ConvertFrom-Json
    if ($taskEvidence.status -ne 'passed' -or -not $taskEvidence.frozen -or -not $taskEvidence.server_stopped) { throw 'Packaged evidence incomplete' }
    if ($taskEvidence.update_version -ne $AppVersion -or -not $taskEvidence.public_updates_disabled) { throw 'Packaged update configuration/version evidence incomplete' }
    foreach ($taskCheck in @('bundled-js-modules','context-default-expiry-inactive-retrieval-renewal','thread-create-pause-complete-and-preferences','restart-sqlite-thread-memory-preferences-chat-state','thread-delete-account-cleanup')) {
        if ($taskEvidence.checks -notcontains $taskCheck) { throw ('Packaged v0.3 evidence missing: ' + $taskCheck) }
    }
    if ($taskEvidence.port -ne $taskEvidence.restart_port) { throw 'Packaged HTTP restart origin changed' }
    $taskUiReport = Join-Path $taskOutput 'ui-smoke.json'
    $taskUiData = Join-Path ([IO.Path]::GetTempPath()) ('xiaoban-release-ui-' + [guid]::NewGuid().ToString('N'))
    $taskUiProcess = Start-Process -FilePath $taskExe -ArgumentList @('--ui-smoke','--data-dir',('"' + $taskUiData + '"'),'--report',('"' + $taskUiReport + '"')) -WindowStyle Hidden -PassThru
    if (-not $taskUiProcess.WaitForExit(90000)) { Stop-Process -Id $taskUiProcess.Id -Force; throw 'Packaged WebView2 UI smoke timed out' }
    if ($taskUiProcess.ExitCode -ne 0) { throw 'Packaged WebView2 UI smoke failed' }
    $taskUiEvidence = Get-Content -LiteralPath $taskUiReport -Raw | ConvertFrom-Json
    if ($taskUiEvidence.status -ne 'passed' -or -not $taskUiEvidence.frozen -or -not $taskUiEvidence.server_stopped) { throw 'Packaged GUI evidence incomplete' }
    foreach ($taskCheck in @('separate-update-window', 'main-window-has-no-native-update-api', 'smoke-public-updates-disabled')) {
        if ($taskUiEvidence.checks -notcontains $taskCheck) { throw ('Packaged updater GUI evidence missing: ' + $taskCheck) }
    }
    if ($taskUiEvidence.update_version -ne $AppVersion) { throw 'Packaged updater GUI version mismatch' }
    $taskRestartReport = Join-Path $taskOutput 'ui-restart-smoke.json'
    $taskRestartProcess = Start-Process -FilePath $taskExe -ArgumentList @('--ui-smoke','--reuse-login','--data-dir',('"' + $taskUiData + '"'),'--report',('"' + $taskRestartReport + '"')) -WindowStyle Hidden -PassThru
    if (-not $taskRestartProcess.WaitForExit(90000)) { Stop-Process -Id $taskRestartProcess.Id -Force; throw 'Packaged restart smoke timed out' }
    if ($taskRestartProcess.ExitCode -ne 0) { throw 'Packaged login persistence failed' }
    $taskRestartEvidence = Get-Content -LiteralPath $taskRestartReport -Raw | ConvertFrom-Json
    if ($taskRestartEvidence.status -ne 'passed' -or -not $taskRestartEvidence.restored_login -or -not $taskRestartEvidence.server_stopped -or $taskRestartEvidence.port -ne $taskUiEvidence.port) { throw 'Packaged login persistence evidence incomplete' }
    Copy-Item -LiteralPath (Join-Path $taskRoot 'desktop\README.md') -Destination (Join-Path $taskOutput 'README.txt') -Force
    & $taskPython -m desktop.collect_licenses
    if ($LASTEXITCODE -ne 0) { throw 'Third-party license collection failed' }
    $taskLicenses = Join-Path $taskOutput 'LICENSES.txt'
    Copy-Item -LiteralPath (Join-Path $taskRoot 'desktop\LICENSES.txt') -Destination $taskLicenses -Force
    Compress-Archive -LiteralPath @($taskExe, (Join-Path $taskOutput 'README.txt'), $taskLicenses) -DestinationPath (Join-Path $taskOutput 'Xiaoban-Windows-portable.zip') -Force
    foreach ($taskArtifact in @($taskExe, (Join-Path $taskOutput 'Xiaoban-Windows-portable.zip'))) {
        [pscustomobject]@{Path=$taskArtifact; Bytes=(Get-Item -LiteralPath $taskArtifact).Length; SHA256=(Get-FileHash -LiteralPath $taskArtifact -Algorithm SHA256).Hash}
    }
} finally {
    $env:XIAOBAN_BUILD_RESOURCES = $taskPreviousResources
    $env:XIAOBAN_BUILD_VERSION_FILE = $taskPreviousVersionFile
    Pop-Location
}
