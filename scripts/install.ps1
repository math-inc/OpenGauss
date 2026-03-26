# ============================================================================
# Open Gauss WSL Installer Bootstrap
# ============================================================================
# Windows convenience wrapper that installs Open Gauss inside WSL2 using the
# shared installer flow.
#
# Usage:
#   .\scripts\install.ps1
#   .\scripts\install.ps1 -WithWorkspace
#   .\scripts\install.ps1 -Distro Ubuntu
#   .\scripts\install.ps1 -LinuxRepoDir "~/OpenGauss"
#
# ============================================================================

param(
    [switch]$WithWorkspace,
    [string]$Distro = "",
    [string]$LinuxRepoDir = "~/OpenGauss",
    [string]$Branch = ""
)

$ErrorActionPreference = "Stop"

$DefaultRepoUrl = "https://github.com/math-inc/OpenGauss.git"
$script:ResolvedDistro = $null

function Write-Banner {
    Write-Host ""
    Write-Host "┌─────────────────────────────────────────────────────────┐" -ForegroundColor Magenta
    Write-Host "│              Open Gauss WSL Installer                  │" -ForegroundColor Magenta
    Write-Host "├─────────────────────────────────────────────────────────┤" -ForegroundColor Magenta
    Write-Host "│   Windows uses WSL2 and the shared installer flow      │" -ForegroundColor Magenta
    Write-Host "└─────────────────────────────────────────────────────────┘" -ForegroundColor Magenta
    Write-Host ""
    Write-Host "[note] This install can take up to 10 minutes." -ForegroundColor Yellow
    Write-Host "[note] For a setup in under 10 seconds, try: https://morph.new/opengauss" -ForegroundColor Yellow
    Write-Host ""
}

function Write-Info {
    param([string]$Message)
    Write-Host "-> $Message" -ForegroundColor Cyan
}

function Write-Success {
    param([string]$Message)
    Write-Host "[ok] $Message" -ForegroundColor Green
}

function Write-Warn {
    param([string]$Message)
    Write-Host "[warn] $Message" -ForegroundColor Yellow
}

function Write-Err {
    param([string]$Message)
    Write-Host "[err] $Message" -ForegroundColor Red
}

function Resolve-Branch {
    if ($Branch) {
        return $Branch
    }

    try {
        $current = git rev-parse --abbrev-ref HEAD 2>$null
        if ($LASTEXITCODE -eq 0 -and $current) {
            $trimmed = $current.Trim()
            if ($trimmed -and $trimmed -ne "HEAD") {
                return $trimmed
            }
        }
    } catch {
    }

    return "main"
}

function Get-RerunCommand {
    $parts = @(".\scripts\install.ps1")
    if ($WithWorkspace) {
        $parts += "-WithWorkspace"
    }
    if ($Distro) {
        $parts += "-Distro"
        $parts += "`"$Distro`""
    }
    if ($LinuxRepoDir -and $LinuxRepoDir -ne "~/OpenGauss") {
        $parts += "-LinuxRepoDir"
        $parts += "`"$LinuxRepoDir`""
    }
    if ($Branch) {
        $parts += "-Branch"
        $parts += "`"$Branch`""
    }
    return ($parts -join " ")
}

function Install-WSLDistro {
    param([string]$TargetDistro)

    $rerunCommand = Get-RerunCommand

    Write-Warn "No initialized WSL distro is available yet."
    Write-Info "Installing the WSL distro '$TargetDistro' now."
    Write-Warn "Windows may prompt for elevation, enable WSL features, or require a restart."
    Write-Warn "If WSL drops you into the new Linux shell, type 'exit' to return here, then rerun:"
    Write-Host "  $rerunCommand" -ForegroundColor Yellow

    & wsl.exe --install -d $TargetDistro
    if ($LASTEXITCODE -ne 0) {
        throw "WSL distro install failed with code $LASTEXITCODE"
    }

    Write-Host ""
    Write-Warn "WSL reported a successful install command."
    Write-Warn "If the distro setup opened a Linux shell, type 'exit' there to return to PowerShell."
    Write-Warn "If Windows asks you to restart or finish distro setup, do that first and then rerun:"
    Write-Host "  $rerunCommand" -ForegroundColor Yellow
}

function Get-InstalledWSLDistros {
    $listOutput = & wsl.exe --list --quiet 2>$null
    if ($LASTEXITCODE -ne 0) {
        return @()
    }

    $distros = @()
    foreach ($line in ($listOutput | Out-String).Split("`n")) {
        $trimmed = $line.Trim()
        if (-not $trimmed) {
            continue
        }
        $cleaned = ($trimmed -replace "`0", "").Trim()
        if ($cleaned) {
            $distros += $cleaned
        }
    }
    return $distros
}

function Test-WSLBash {
    param([string]$TargetDistro)

    $probeArgs = @()
    if ($TargetDistro) {
        $probeArgs += @("-d", $TargetDistro)
    }
    $probeArgs += @("--", "bash", "-lc", "printf ok")

    $probeOutput = & wsl.exe @probeArgs 2>$null
    $probeText = ($probeOutput | Out-String).Trim()
    return ($LASTEXITCODE -eq 0 -and $probeText -eq "ok")
}

function Ensure-WSL {
    if (-not (Get-Command wsl.exe -ErrorAction SilentlyContinue)) {
        Write-Err "WSL is not installed on this machine."
        Write-Info "Install it with:"
        Write-Host "  wsl --install -d Ubuntu" -ForegroundColor Yellow
        throw "WSL is required"
    }

    if ($Distro) {
        $installedDistros = Get-InstalledWSLDistros
        if ($installedDistros -notcontains $Distro) {
            Install-WSLDistro -TargetDistro $Distro
        }
        if (-not (Test-WSLBash -TargetDistro $Distro)) {
            Write-Err "Could not start the WSL distro '$Distro'."
            Write-Info "Open that distro once to finish first-run setup, then rerun this installer."
            throw "WSL shell unavailable"
        }
        $script:ResolvedDistro = $Distro
        return
    }

    if (Test-WSLBash -TargetDistro "") {
        $script:ResolvedDistro = $null
        return
    }

    $installedDistros = Get-InstalledWSLDistros
    if ($installedDistros.Count -gt 0) {
        $preferredDistro = if ($installedDistros -contains "Ubuntu") { "Ubuntu" } else { $installedDistros[0] }
        Write-Info "Found existing WSL distro: $preferredDistro"
        if (-not (Test-WSLBash -TargetDistro $preferredDistro)) {
            Write-Err "Could not start the existing WSL distro '$preferredDistro'."
            Write-Info "Open that distro once to finish first-run setup, then rerun this installer."
            throw "WSL shell unavailable"
        }
        $script:ResolvedDistro = $preferredDistro
        return
    }

    Install-WSLDistro -TargetDistro "Ubuntu"

    if (-not (Test-WSLBash -TargetDistro "Ubuntu")) {
        Write-Err "Could not start a WSL bash shell after installing 'Ubuntu'."
        Write-Info "Finish any Windows restart or first-run distro setup, then rerun this installer."
        throw "WSL shell unavailable"
    }

    $script:ResolvedDistro = "Ubuntu"
}

function Build-InstallScript {
    return @'
set -euo pipefail

REPO_URL="${1:?missing REPO_URL}"
REQUESTED_BRANCH="${2:?missing REQUESTED_BRANCH}"
TARGET_DIR_RAW="${3:?missing TARGET_DIR_RAW}"
CREATE_WORKSPACE="${4:?missing CREATE_WORKSPACE}"

case "$TARGET_DIR_RAW" in
  "~")
    TARGET_DIR="$HOME"
    ;;
  "~/"*)
    TARGET_DIR="$HOME/${TARGET_DIR_RAW#~/}"
    ;;
  *)
    TARGET_DIR="$TARGET_DIR_RAW"
    ;;
esac

if ! command -v git >/dev/null 2>&1; then
  if command -v apt-get >/dev/null 2>&1 && command -v sudo >/dev/null 2>&1; then
    export DEBIAN_FRONTEND=noninteractive
    sudo apt-get update
    sudo apt-get install -y git
  else
    echo "✗ git is required inside WSL before Open Gauss can be installed."
    exit 1
  fi
fi

CHOSEN_BRANCH="$REQUESTED_BRANCH"
if ! git ls-remote --exit-code --heads "$REPO_URL" "$REQUESTED_BRANCH" >/dev/null 2>&1; then
  if [ "$REQUESTED_BRANCH" != "main" ]; then
    echo "→ Branch $REQUESTED_BRANCH is not published on origin; falling back to main."
  fi
  CHOSEN_BRANCH="main"
fi

mkdir -p "$(dirname "$TARGET_DIR")"

if [ -d "$TARGET_DIR/.git" ]; then
  if [ -n "$(git -C "$TARGET_DIR" status --porcelain 2>/dev/null)" ]; then
    echo "✗ Existing WSL checkout at $TARGET_DIR has local changes."
    echo "  Commit or stash them, then rerun this installer."
    exit 1
  fi
  git -C "$TARGET_DIR" remote set-url origin "$REPO_URL" || true
  git -C "$TARGET_DIR" fetch origin "$CHOSEN_BRANCH" --tags
  if git -C "$TARGET_DIR" show-ref --verify --quiet "refs/heads/$CHOSEN_BRANCH"; then
    git -C "$TARGET_DIR" switch "$CHOSEN_BRANCH"
  else
    git -C "$TARGET_DIR" switch -c "$CHOSEN_BRANCH" --track "origin/$CHOSEN_BRANCH"
  fi
  git -C "$TARGET_DIR" pull --ff-only origin "$CHOSEN_BRANCH"
  git -C "$TARGET_DIR" submodule update --init --recursive
else
  git clone --branch "$CHOSEN_BRANCH" --recurse-submodules "$REPO_URL" "$TARGET_DIR"
fi

cd "$TARGET_DIR"
if [ "$CREATE_WORKSPACE" = "1" ]; then
  echo "→ -WithWorkspace is accepted for compatibility."
  echo "→ The shared Open Gauss template already provisions the workspace."
fi
./scripts/install.sh
'@
}

function Resolve-WSLPath {
    param([string]$WindowsPath)

    $normalizedPath = $WindowsPath -replace '\\', '/'

    $pathArgs = @()
    if ($script:ResolvedDistro) {
        $pathArgs += @("-d", $script:ResolvedDistro)
    }
    $pathArgs += @("--", "wslpath", "-u", "-a", $normalizedPath)

    $wslPath = (& wsl.exe @pathArgs | Out-String).Trim()
    if ($LASTEXITCODE -ne 0 -or -not $wslPath) {
        throw "Could not translate Windows path into a WSL path: $WindowsPath"
    }
    return $wslPath
}

function Main {
    Write-Banner
    Write-Info "Open Gauss on Windows runs through WSL2."
    Write-Info "This bootstrap clones into your WSL home and then runs ./scripts/install.sh there."
    Ensure-WSL

    $resolvedBranch = Resolve-Branch
    Write-Info "Using repository branch: $resolvedBranch"
    if ($script:ResolvedDistro) {
        Write-Info "Using WSL distro: $script:ResolvedDistro"
    } else {
        Write-Info "Using your default WSL distro"
    }
    Write-Info "Using WSL repo path: $LinuxRepoDir"

    $bashScript = (Build-InstallScript).Replace("`r`n", "`n").Replace("`r", "`n")
    $workspaceFlag = if ($WithWorkspace.IsPresent) { "1" } else { "0" }
    $tempDir = Join-Path $env:TEMP "opengauss-wsl-bootstrap"
    $tempScriptPath = Join-Path $tempDir "install.sh"
    New-Item -ItemType Directory -Force -Path $tempDir | Out-Null
    [System.IO.File]::WriteAllText(
        $tempScriptPath,
        $bashScript,
        [System.Text.UTF8Encoding]::new($false)
    )

    try {
        $wslScriptPath = Resolve-WSLPath -WindowsPath $tempScriptPath

        $wslArgs = @()
        if ($script:ResolvedDistro) {
            $wslArgs += @("-d", $script:ResolvedDistro)
        }
        $wslArgs += @(
            "--",
            "bash",
            $wslScriptPath,
            $DefaultRepoUrl,
            $resolvedBranch,
            $LinuxRepoDir,
            $workspaceFlag
        )

        & wsl.exe @wslArgs
        if ($LASTEXITCODE -ne 0) {
            throw "WSL install flow exited with code $LASTEXITCODE"
        }
    } finally {
        Remove-Item -Force $tempScriptPath -ErrorAction SilentlyContinue
    }

    Write-Host ""
    Write-Success "Open Gauss is installed in WSL."
    Write-Info "For daily use, open your WSL shell and run:"
    Write-Host "  cd $LinuxRepoDir" -ForegroundColor Yellow
    Write-Host "  gauss" -ForegroundColor Yellow
    Write-Host ""
}

try {
    Main
} catch {
    Write-Host ""
    Write-Err "$_"
    Write-Host ""
    exit 1
}
