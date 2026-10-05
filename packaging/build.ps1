# Build Farever Book: icon -> executable -> installer.
#
#   powershell -ExecutionPolicy Bypass -File packaging\build.ps1
#
# Produces:
#   dist\FareverBook\                    the windowed build (runnable as-is)
#   dist\FareverBook-<version>-Setup.exe the installer to attach to a release
#
# Run from the project root. One-time prerequisites:
#   py -m pip install pyinstaller pillow frida==17.18.0 pywebview
#   winget install JRSoftware.InnoSetup

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

# The version is meter\common.py's VERSION (what the update check compares).
$meter = Get-Content "meter\common.py" -Raw
if ($meter -notmatch '(?m)^VERSION\s*=\s*"([^"]+)"') {
    throw "Couldn't find VERSION in meter\common.py"
}
$version = $Matches[1]
Write-Host "==> Building Farever Book $version" -ForegroundColor Cyan

Write-Host "==> Icon" -ForegroundColor Cyan
py packaging\make_icon.py

Write-Host "==> PyInstaller" -ForegroundColor Cyan
py -m PyInstaller --clean --noconfirm --distpath dist --workpath build `
    packaging\FareverBook.spec
if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed" }

$exe = "dist\FareverBook\FareverBook.exe"
if (-not (Test-Path $exe)) { throw "Expected $exe, but it wasn't produced" }

# Inno Setup: per-user (LOCALAPPDATA) or machine-wide (Program Files) install
$isccCandidates = @(
    "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe",
    "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe",
    "$env:ProgramFiles\Inno Setup 6\ISCC.exe"
)
$iscc = $isccCandidates | Where-Object { Test-Path $_ } | Select-Object -First 1
if (-not $iscc) {
    Write-Warning ("Inno Setup not found, so no installer was built. " +
                   "The build in dist\FareverBook still runs. " +
                   "Install it with: winget install JRSoftware.InnoSetup")
    exit 0
}

Write-Host "==> Inno Setup" -ForegroundColor Cyan
& $iscc "/DAppVersion=$version" "packaging\FareverBook.iss"
if ($LASTEXITCODE -ne 0) { throw "ISCC failed" }

$setup = "dist\FareverBook-$version-Setup.exe"
$mb = [math]::Round((Get-Item $setup).Length / 1MB, 1)
Write-Host ""
Write-Host "Done." -ForegroundColor Green
Write-Host "  $setup  ($mb MB)"
Write-Host ""
Write-Host "Before publishing: tag the repo $version to match VERSION, or the"
Write-Host "update check will tell everyone they're out of date."
