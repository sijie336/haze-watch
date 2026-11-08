<#
Build and run HazeWatch on Windows without Docker.
Requires Python 3.12 and Node.js 24 already installed.
Usage: .\start.ps1 [-Mode demo|live] [-Port 3000] [-BuildOnly]
#>
[CmdletBinding()]
param(
    [ValidateSet('demo', 'live')]
    [string]$Mode = 'demo',
    [ValidateRange(1024, 65535)]
    [int]$Port = 3000,
    [switch]$BuildOnly
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

function Invoke-Checked {
    param([string]$Program, [string[]]$Arguments)
    & $Program @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "Command failed (exit $LASTEXITCODE): $Program $($Arguments -join ' ')"
    }
}

$ProjectRoot = $PSScriptRoot
$Python = Join-Path $ProjectRoot '.venv\Scripts\python.exe'
$PreviousEnvironment = @{}
foreach ($Name in @('DATA_MODE', 'DATABASE_URL', 'PUBLIC_URL', 'PORT')) {
    $PreviousEnvironment[$Name] = [Environment]::GetEnvironmentVariable($Name, 'Process')
}

Push-Location $ProjectRoot
try {
    Write-Host "`nHazeWatch | Angular + Python | No Docker`n" -ForegroundColor Green
    foreach ($RequiredFile in @('backend\requirements.txt', 'frontend\package-lock.json', 'scripts\serve.py')) {
        if (-not (Test-Path (Join-Path $ProjectRoot $RequiredFile))) {
            throw "Missing $RequiredFile. Extract the entire project before running start.ps1."
        }
    }

    Write-Host '[1/5] Checking Node.js and Python...'
    $Node = Get-Command node.exe -ErrorAction SilentlyContinue
    $Npm = Get-Command npm.cmd -ErrorAction SilentlyContinue
    if (-not $Node -or -not $Npm) {
        throw 'Install Node.js 24 (including npm), reopen PowerShell, and run this script again.'
    }
    $NodeMajor = & $Node.Source -p 'process.versions.node.split(String.fromCharCode(46))[0]'
    if ($LASTEXITCODE -ne 0 -or "$NodeMajor".Trim() -ne '24') {
        throw 'This launcher supports Node.js 24. Install it, reopen PowerShell, and retry.'
    }

    if (-not (Test-Path $Python)) {
        if (Test-Path (Join-Path $ProjectRoot '.venv')) {
            throw 'An incompatible .venv folder exists. Rename it, then rerun this script to create a Windows environment.'
        }
        $Launcher = Get-Command py.exe -ErrorAction SilentlyContinue
        if ($Launcher) {
            Invoke-Checked -Program $Launcher.Source -Arguments @('-3.12', '-m', 'venv', '.venv')
        } else {
            $SystemPython = Get-Command python.exe -ErrorAction SilentlyContinue
            if (-not $SystemPython) {
                throw 'Install Python 3.12 with the Python launcher or add it to PATH, reopen PowerShell, and retry.'
            }
            Invoke-Checked -Program $SystemPython.Source -Arguments @('-c', 'import sys; assert sys.version_info[:2] == (3, 12)')
            Invoke-Checked -Program $SystemPython.Source -Arguments @('-m', 'venv', '.venv')
        }
    }
    Invoke-Checked -Program $Python -Arguments @('-c', 'import sys; assert sys.version_info[:2] == (3, 12)')

    Write-Host "`n[2/5] Installing Python dependencies..."
    Invoke-Checked -Program $Python -Arguments @('-m', 'pip', 'install', '-r', 'backend\requirements.txt')

    Write-Host "`n[3/5] Installing Angular dependencies..."
    Invoke-Checked -Program $Npm.Source -Arguments @('--prefix', 'frontend', 'ci', '--no-audit', '--no-fund')

    Write-Host "`n[4/5] Building Angular..."
    Invoke-Checked -Program $Npm.Source -Arguments @('--prefix', 'frontend', 'run', 'build')

    if ($BuildOnly) {
        Write-Host "`nBuild complete. Run .\start.ps1 to start the application." -ForegroundColor Green
    } else {
        $env:DATA_MODE = $Mode
        $env:PORT = "$Port"
        $env:PUBLIC_URL = "http://localhost:$Port"
        # Separate databases keep demo subscriptions away from live observations.
        $DatabasePath = (Join-Path $ProjectRoot "haze-$Mode.db").Replace('\', '/')
        $env:DATABASE_URL = "sqlite:///$DatabasePath"

        Write-Host "`n[5/5] Starting the application and background worker..."
        Write-Host "Mode: $Mode | Database: haze-$Mode.db"
        Write-Host "Open $env:PUBLIC_URL once the server reports startup complete." -ForegroundColor Green
        Write-Host 'Keep this window open. Press Ctrl+C to stop the application.'
        Write-Host 'Email requires SMTP settings in this shell; the dashboard works without SMTP.'
        Invoke-Checked -Program $Python -Arguments @('scripts\serve.py')
    }
} finally {
    foreach ($Name in $PreviousEnvironment.Keys) {
        [Environment]::SetEnvironmentVariable($Name, $PreviousEnvironment[$Name], 'Process')
    }
    Pop-Location
}
