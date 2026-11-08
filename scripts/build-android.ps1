param(
    [string]$AppVersion,
    [int]$BuildNumber = 0,
    [string]$PublicAppUrl = 'https://xiaoban-companion.abloom-haven-3343.chatgpt.site',
    [string]$OutputDir,
    [string]$PreviousApk,
    [string]$FlutterPath,
    [string]$AndroidSdk,
    [string]$JavaHome
)
$ErrorActionPreference = 'Stop'
$taskRoot = Split-Path $PSScriptRoot -Parent
if (-not $AppVersion) { $AppVersion = (Get-Content -LiteralPath (Join-Path $taskRoot 'desktop\update-config.json') -Raw | ConvertFrom-Json).version }
if ($AppVersion -notmatch '^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$') { throw 'AppVersion must contain three nonnegative integers' }
$taskUrl = $null
if (-not [Uri]::TryCreate($PublicAppUrl, [UriKind]::Absolute, [ref]$taskUrl) -or $taskUrl.Scheme -ne 'https' -or -not $taskUrl.Host -or $taskUrl.UserInfo -or $taskUrl.Query -or $taskUrl.Fragment) { throw 'PublicAppUrl must use HTTPS without credentials, query or fragment' }
if ($BuildNumber -lt 1 -or $BuildNumber -gt 2100000000) { throw 'BuildNumber must be supplied as an increasing Android versionCode (1..2100000000)' }
$PublicAppUrl = $PublicAppUrl.TrimEnd('/')
if (-not $OutputDir) { $OutputDir = Join-Path $taskRoot ('artifacts\android\' + $AppVersion) }
if (-not [IO.Path]::IsPathRooted($OutputDir)) { $OutputDir = Join-Path $taskRoot $OutputDir }
$taskOutput = [IO.Path]::GetFullPath($OutputDir)
if (-not $FlutterPath) { $FlutterPath = Join-Path $env:LOCALAPPDATA 'CodexTools\flutter\bin\flutter.bat' }
if (-not $AndroidSdk) { $AndroidSdk = if ($env:ANDROID_HOME) { $env:ANDROID_HOME } else { Join-Path $env:LOCALAPPDATA 'CodexTools\AndroidSdk' } }
if (-not $JavaHome) { $JavaHome = if ($env:JAVA_HOME) { $env:JAVA_HOME } else { Join-Path $env:ProgramFiles 'Java\jdk-21' } }
foreach ($taskRequired in @($FlutterPath, (Join-Path $AndroidSdk 'build-tools'), (Join-Path $JavaHome 'bin\java.exe'))) {
    if (-not (Test-Path -LiteralPath $taskRequired)) { throw ('Required Android build tool is missing: ' + $taskRequired) }
}
$taskPython = Join-Path $taskRoot '.venv-desktop\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $taskPython)) { $taskPython = Join-Path $taskRoot '.venv\Scripts\python.exe' }
if (-not (Test-Path -LiteralPath $taskPython)) { $taskPython = (Get-Command python -ErrorAction Stop).Source }
if (-not $PreviousApk) {
    $taskLegacy = Join-Path $taskRoot 'artifacts\companion-android-debug.apk'
    if (Test-Path -LiteralPath $taskLegacy) { $PreviousApk = $taskLegacy }
}
if ($PreviousApk -and -not [IO.Path]::IsPathRooted($PreviousApk)) { $PreviousApk = Join-Path $taskRoot $PreviousApk }
if ($PreviousApk -and -not (Test-Path -LiteralPath $PreviousApk -PathType Leaf)) { throw 'PreviousApk does not exist' }
$taskMobile = [IO.Path]::GetFullPath((Join-Path $taskRoot 'mobile'))
if (-not ('Xiaoban.AndroidBuildNativeMethods' -as [type])) {
    Add-Type -TypeDefinition @'
using System;
using System.Text;
using System.Runtime.InteropServices;
namespace Xiaoban {
    public static class AndroidBuildNativeMethods {
        [DllImport("kernel32.dll", CharSet=CharSet.Unicode, SetLastError=true)]
        public static extern uint QueryDosDevice(string name, StringBuilder target, int max);
    }
}
'@
}
$taskDrive = $null
foreach ($taskLetter in @('V','W','X','Y','Z','U','T','S','R')) {
    if (-not (Get-PSDrive -Name $taskLetter -ErrorAction SilentlyContinue) -and -not (Test-Path ($taskLetter + ':\'))) { $taskDrive = $taskLetter + ':'; break }
}
if (-not $taskDrive) { throw 'No free temporary ASCII drive is available for Flutter tooling' }
New-Item -ItemType Directory -Path $taskOutput -Force | Out-Null
$taskBeforeInputs = Join-Path $taskOutput 'source-inputs-before.json'
$taskReportHelper = Join-Path $taskRoot 'scripts\android_build_report.py'
Push-Location $taskRoot
try {
    $taskSourceCommit = (git rev-parse HEAD).Trim()
    if ($LASTEXITCODE -ne 0) { throw 'Cannot record source commit' }
    & $taskPython -X utf8 $taskReportHelper --snapshot $taskBeforeInputs
    if ($LASTEXITCODE -ne 0) { throw 'Cannot fingerprint mobile production inputs' }
} finally { Pop-Location }
$taskSavedEnvironment = @{}
foreach ($taskName in @('JAVA_HOME','ANDROID_HOME','ANDROID_SDK_ROOT','Path')) { $taskSavedEnvironment[$taskName] = [Environment]::GetEnvironmentVariable($taskName, 'Process') }
$taskMapped = $false
try {
    $env:JAVA_HOME = $JavaHome
    $env:ANDROID_HOME = $AndroidSdk
    $env:ANDROID_SDK_ROOT = $AndroidSdk
    $env:Path = (Split-Path $FlutterPath -Parent) + ';' + (Join-Path $JavaHome 'bin') + ';' + $env:Path
    if ($PreviousApk) {
        & $taskPython -X utf8 $taskReportHelper --validate-previous-version-code --version-code $BuildNumber --previous-apk $PreviousApk
        if ($LASTEXITCODE -ne 0) { throw 'BuildNumber must increase over PreviousApk versionCode' }
    }
    & subst.exe $taskDrive $taskMobile
    if ($LASTEXITCODE -ne 0) { throw 'Cannot create isolated ASCII Flutter drive mapping' }
    $taskMapped = $true
    Push-Location ($taskDrive + '\')
    try {
        # Flutter's depfile contains absolute output paths. A previous temporary
        # drive may no longer exist, causing Gradle to recurse past that root.
        # Clear project-generated output with Flutter's own command before
        # restoring dependencies; global SDK, Pub and Gradle caches stay intact.
        & $FlutterPath clean
        if ($LASTEXITCODE -ne 0) { throw 'Flutter project output cleanup failed' }
        & $FlutterPath pub get
        if ($LASTEXITCODE -ne 0) { throw 'Flutter dependency resolution failed' }
        & $FlutterPath analyze --no-pub
        if ($LASTEXITCODE -ne 0) { throw 'Flutter static analysis failed' }
        & $FlutterPath test --no-pub
        if ($LASTEXITCODE -ne 0) { throw 'Flutter tests failed' }
        & $FlutterPath build apk --debug --no-pub --build-name $AppVersion --build-number $BuildNumber ('--dart-define=COMPANION_URL=' + $PublicAppUrl)
        if ($LASTEXITCODE -ne 0) { throw 'Android APK build failed' }
    } finally { Pop-Location }
    $taskBuiltApk = Join-Path $taskMobile 'build\app\outputs\flutter-apk\app-debug.apk'
    $taskApk = Join-Path $taskOutput 'Xiaoban-Android-debug.apk'
    Copy-Item -LiteralPath $taskBuiltApk -Destination $taskApk -Force
    $taskReportArgs = @('-X','utf8',$taskReportHelper,'--apk',$taskApk,'--version',$AppVersion,'--version-code',$BuildNumber,'--default-url',$PublicAppUrl,'--source-commit',$taskSourceCommit,'--before-inputs',$taskBeforeInputs,'--output',(Join-Path $taskOutput 'build-report.json'),'--flutter-analyze-passed','--flutter-test-passed')
    if ($PreviousApk) { $taskReportArgs += @('--previous-apk',$PreviousApk) }
    & $taskPython @taskReportArgs
    if ($LASTEXITCODE -ne 0) { throw 'Built APK package/signature/default URL/source verification failed' }
} finally {
    if ($taskMapped) {
        # Unmap only the drive we created, never an existing user mapping.
        $taskMapping = [Text.StringBuilder]::new(32768)
        $taskMappingResult = [Xiaoban.AndroidBuildNativeMethods]::QueryDosDevice($taskDrive, $taskMapping, 32768)
        if ($taskMappingResult -ne 0 -and $taskMapping.ToString().TrimEnd('\') -eq ('\??\' + $taskMobile.TrimEnd('\'))) { & subst.exe $taskDrive /D }
        else { Write-Warning ('Temporary mapping changed; leaving ' + $taskDrive + ' untouched') }
    }
    foreach ($taskName in $taskSavedEnvironment.Keys) { [Environment]::SetEnvironmentVariable($taskName, $taskSavedEnvironment[$taskName], 'Process') }
}
