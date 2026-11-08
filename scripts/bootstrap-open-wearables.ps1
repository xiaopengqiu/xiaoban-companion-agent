[CmdletBinding()]
param([switch]$SkipClone)

$ErrorActionPreference = 'Stop'
$workspacePath = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$vendorRoot = [IO.Path]::GetFullPath((Join-Path $workspacePath 'vendor'))
$upstreamPath = [IO.Path]::GetFullPath((Join-Path $vendorRoot 'open-wearables'))
$upstreamCommit = '183e8cc4f6de897dbd288c93d49a691f2cf721ae'
$upstreamUrl = 'https://github.com/the-momentum/open-wearables.git'

# Fixed paths only. Never clean, reset, remove or overwrite an existing checkout.
if (-not $upstreamPath.StartsWith($vendorRoot + [IO.Path]::DirectorySeparatorChar, [StringComparison]::OrdinalIgnoreCase)) {
    throw 'The upstream checkout must remain under this workspace vendor directory.'
}
if (-not $SkipClone) {
    if (-not (Get-Command git -ErrorAction SilentlyContinue)) { throw 'Install Git before bootstrapping the upstream checkout.' }
    if (Test-Path -LiteralPath $upstreamPath) {
        if (-not (Test-Path -LiteralPath (Join-Path $upstreamPath '.git'))) { throw 'vendor/open-wearables exists and is not a Git checkout. Existing files were preserved.' }
        $currentCommit = (& git -C $upstreamPath rev-parse HEAD).Trim()
        if ($LASTEXITCODE -ne 0 -or $currentCommit -ne $upstreamCommit) { throw 'Existing upstream checkout is on another commit. It was preserved; use a separate checkout or reconcile it manually.' }
        $remoteUrl = (& git -C $upstreamPath remote get-url origin).Trim()
        if ($remoteUrl -ne $upstreamUrl) { throw 'Existing upstream origin differs; checkout was preserved.' }
        Write-Output 'Pinned upstream checkout already exists; all local modifications preserved.'
    } else {
        New-Item -ItemType Directory -Path $vendorRoot -Force | Out-Null
        & git clone --branch 0.11.0 --single-branch --depth 1 $upstreamUrl $upstreamPath
        if ($LASTEXITCODE -ne 0) { throw 'Upstream clone failed. Any partially created files were preserved.' }
        $clonedCommit = (& git -C $upstreamPath rev-parse HEAD).Trim()
        if ($LASTEXITCODE -ne 0 -or $clonedCommit -ne $upstreamCommit) { throw 'Upstream release tag changed from the inspected commit. Do not deploy this checkout.' }
        Write-Output "Upstream source and original MIT LICENSE are pinned at $upstreamCommit."
    }
}

function New-TaskSecret {
    $bytes = New-Object byte[] 48
    $generator = [Security.Cryptography.RandomNumberGenerator]::Create()
    try { $generator.GetBytes($bytes) } finally { $generator.Dispose() }
    return [Convert]::ToBase64String($bytes).Replace('+', '-').Replace('/', '_').TrimEnd('=')
}

$deploymentPath = Join-Path $workspacePath 'deploy'
$configPath = Join-Path $deploymentPath '.env'
if (-not (Test-Path -LiteralPath $configPath)) {
    $templatePath = Join-Path $deploymentPath '.env.example'
    $configContent = [IO.File]::ReadAllText($templatePath)
    $configContent = $configContent.Replace('GENERATE_DB_PASSWORD', (New-TaskSecret))
    $configContent = $configContent.Replace('GENERATE_SECRET_KEY', (New-TaskSecret))
    $configContent = $configContent.Replace('GENERATE_ADMIN_PASSWORD', (New-TaskSecret))
    [IO.File]::WriteAllText($configPath, $configContent, (New-Object Text.UTF8Encoding $false))
    Write-Output 'Created ignored deploy/.env with generated secrets. Read ADMIN_PASSWORD locally; no secrets are printed.'
} else {
    Write-Output 'Existing deploy/.env preserved.'
}
Write-Output 'Start when Docker is available:'
Write-Output 'docker compose --env-file deploy/.env -f deploy/compose.open-wearables.yml up -d'
Write-Output 'No services were started by this script.'
