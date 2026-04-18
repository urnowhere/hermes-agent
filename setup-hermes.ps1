# ============================================================================
# Hermes Agent Setup Script for Windows (PowerShell)
# ============================================================================
# Quick setup for developers who cloned the repo manually.
# Uses uv for fast Python provisioning and package management.
#
# Usage:
#   .\setup-hermes.ps1
#
# This script:
# 1. Checks for / installs uv
# 2. Ensures Python 3.11 is available
# 3. Creates a virtual environment
# 4. Installs dependencies
# 5. Seeds bundled skills
# 6. Adds venv Scripts to PATH for current session
# ============================================================================

param(
    [switch]$SkipSetupWizard
)

$ErrorActionPreference = "Stop"

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Definition
Set-Location $ScriptDir

$PythonVersion = "3.11"

function Write-Info {
    param([string]$Message)
    Write-Host "→ $Message" -ForegroundColor Cyan
}

function Write-Success {
    param([string]$Message)
    Write-Host "✓ $Message" -ForegroundColor Green
}

function Write-Warn {
    param([string]$Message)
    Write-Host "⚠ $Message" -ForegroundColor Yellow
}

function Write-Err {
    param([string]$Message)
    Write-Host "✗ $Message" -ForegroundColor Red
}

Write-Host ""
Write-Host "⚕ Hermes Agent Setup (Windows)" -ForegroundColor Cyan
Write-Host ""

# ============================================================================
# Install / locate uv
# ============================================================================

Write-Info "Checking for uv..."

$UvCmd = $null
if (Get-Command uv -ErrorAction SilentlyContinue) {
    $UvCmd = "uv"
} else {
    $uvPaths = @(
        "$env:USERPROFILE\.local\bin\uv.exe",
        "$env:USERPROFILE\.cargo\bin\uv.exe"
    )
    foreach ($uvPath in $uvPaths) {
        if (Test-Path $uvPath) {
            $UvCmd = $uvPath
            break
        }
    }
}

if ($UvCmd) {
    $uvVersion = & $UvCmd --version 2>$null
    Write-Success "uv found ($uvVersion)"
} else {
    Write-Info "Installing uv..."
    try {
        powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex" 2>&1 | Out-Null
        $uvExe = "$env:USERPROFILE\.local\bin\uv.exe"
        if (-not (Test-Path $uvExe)) {
            $uvExe = "$env:USERPROFILE\.cargo\bin\uv.exe"
        }
        if (Test-Path $uvExe) {
            $UvCmd = $uvExe
            $uvVersion = & $UvCmd --version 2>$null
            Write-Success "uv installed ($uvVersion)"
        } else {
            Write-Err "uv installed but not found. Add ~\.local\bin to PATH and retry."
            exit 1
        }
    } catch {
        Write-Err "Failed to install uv. Visit https://docs.astral.sh/uv/"
        exit 1
    }
}

# ============================================================================
# Python check (uv can provision it automatically)
# ============================================================================

Write-Info "Checking Python $PythonVersion..."

try {
    $pythonPath = & $UvCmd python find $PythonVersion 2>$null
    if ($pythonPath) {
        $pyVersion = & $pythonPath --version 2>$null
        Write-Success "$pyVersion found"
    } else {
        Write-Info "Python $PythonVersion not found, installing via uv..."
        & $UvCmd python install $PythonVersion
        $pythonPath = & $UvCmd python find $PythonVersion 2>$null
        $pyVersion = & $pythonPath --version 2>$null
        Write-Success "$pyVersion installed"
    }
} catch {
    Write-Err "Failed to find or install Python $PythonVersion"
    exit 1
}

# ============================================================================
# Virtual environment
# ============================================================================

Write-Info "Setting up virtual environment..."

if (Test-Path "venv") {
    Write-Info "Removing old venv..."
    Remove-Item -Recurse -Force "venv"
}

& $UvCmd venv venv --python $PythonVersion
Write-Success "venv created (Python $PythonVersion)"

$env:VIRTUAL_ENV = "$ScriptDir\venv"
$setupPython = "$ScriptDir\venv\Scripts\python.exe"

# ============================================================================
# Dependencies
# ============================================================================

Write-Info "Installing dependencies..."

if (Test-Path "uv.lock") {
    Write-Info "Using uv.lock for hash-verified installation..."
    $env:UV_PROJECT_ENVIRONMENT = "$ScriptDir\venv"
    & $UvCmd sync --all-extras --locked 2>$null
    if ($LASTEXITCODE -eq 0) {
        Write-Success "Dependencies installed (lockfile verified)"
    } else {
        Write-Warn "Lockfile install failed (may be outdated), falling back to pip install..."
        & $UvCmd pip install -e ".[all]" 2>$null
        if ($LASTEXITCODE -ne 0) {
            & $UvCmd pip install -e "." 2>$null
        }
        Write-Success "Dependencies installed"
    }
} else {
    & $UvCmd pip install -e ".[all]" 2>$null
    if ($LASTEXITCODE -ne 0) {
        & $UvCmd pip install -e "." 2>$null
    }
    Write-Success "Dependencies installed"
}

# ============================================================================
# Submodules (terminal backend + RL training)
# ============================================================================

Write-Info "Installing optional submodules..."

if (Test-Path "tinker-atropos\pyproject.toml") {
    & $UvCmd pip install -e ".\tinker-atropos" 2>$null
    if ($LASTEXITCODE -eq 0) {
        Write-Success "tinker-atropos installed"
    } else {
        Write-Warn "tinker-atropos install failed (RL tools may not work)"
    }
} else {
    Write-Warn "tinker-atropos not found (run: git submodule update --init --recursive)"
}

# ============================================================================
# Environment file
# ============================================================================

if (-not (Test-Path ".env")) {
    if (Test-Path ".env.example") {
        Copy-Item ".env.example" ".env"
        Write-Success "Created .env from template"
    }
} else {
    Write-Success ".env exists"
}

# ============================================================================
# PATH setup — ensure venv Scripts dir is available
# ============================================================================

Write-Info "Setting up hermes command..."

$hermesBin = "$ScriptDir\venv\Scripts"
if ($env:Path -notlike "*$hermesBin*") {
    $env:Path = "$hermesBin;$env:Path"
    Write-Success "Added venv Scripts to PATH for this session"
} else {
    Write-Success "venv Scripts already on PATH"
}

# ============================================================================
# Seed bundled skills into ~/.hermes/skills/
# ============================================================================

$hermesHome = if ($env:HERMES_HOME) { $env:HERMES_HOME } else { "$env:USERPROFILE\.hermes" }
$hermesSkillsDir = "$hermesHome\skills"
New-Item -ItemType Directory -Force -Path $hermesSkillsDir | Out-Null

Write-Info "Syncing bundled skills to ~/.hermes/skills/ ..."
try {
    & $setupPython "$ScriptDir\tools\skills_sync.py" 2>$null
    Write-Success "Skills synced"
} catch {
    if (Test-Path "$ScriptDir\skills") {
        Copy-Item -Path "$ScriptDir\skills\*" -Destination $hermesSkillsDir -Recurse -Force -ErrorAction SilentlyContinue
        Write-Success "Skills copied"
    }
}

# ============================================================================
# Done
# ============================================================================

Write-Host ""
Write-Success "Setup complete!"
Write-Host ""
Write-Host "Next steps:" -ForegroundColor Cyan
Write-Host ""
Write-Host "  1. Run the setup wizard to configure API keys:" -ForegroundColor Yellow
Write-Host "     hermes setup"
Write-Host ""
Write-Host "  2. Start chatting:" -ForegroundColor Yellow
Write-Host "     hermes"
Write-Host ""
Write-Host "Other commands:" -ForegroundColor Yellow
Write-Host "  hermes status        # Check configuration"
Write-Host "  hermes gateway install # Install gateway service"
Write-Host "  hermes cron list     # View scheduled jobs"
Write-Host "  hermes doctor        # Diagnose issues"
Write-Host ""

if (-not $SkipSetupWizard) {
    $response = Read-Host "Would you like to run the setup wizard now? [Y/n]"
    if ($response -eq "" -or $response -match "^[Yy]") {
        Write-Host ""
        & $setupPython -m hermes_cli.main setup
    }
}
