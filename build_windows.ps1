param([switch]$NonInteractive)

$ErrorActionPreference = "Stop"
$BuildExitCode = 0
Set-Location $PSScriptRoot
$AppName = "Asset Downloader"
$LogPath = Join-Path $PSScriptRoot "build.log"

try {
    $Python = $null

    if (Get-Command py -ErrorAction SilentlyContinue) {
        try {
            $Candidate = (& py -3.11 -c "import sys; print(sys.executable)" 2>$null | Select-Object -First 1)
            if ($LASTEXITCODE -eq 0 -and (Test-Path $Candidate)) { $Python = $Candidate }
        } catch {}
    }

    if (-not $Python) {
        $Candidates = @(
            "$env:LOCALAPPDATA\Programs\Python\Python311\python.exe",
            "$env:LOCALAPPDATA\Programs\Python\Python311-64\python.exe",
            "$env:ProgramFiles\Python311\python.exe"
        )
        foreach ($Candidate in $Candidates) {
            if (Test-Path $Candidate) { $Python = $Candidate; break }
        }
    }

    if (-not $Python) {
        if (-not (Get-Command winget -ErrorAction SilentlyContinue)) {
            throw "Python 3.11 and winget were not found. Install Python 3.11 x64 from https://www.python.org/downloads/ and rerun."
        }
        Write-Host "Python 3.11 not found. Installing it with winget..."
        winget install --id Python.Python.3.11 --exact --scope user --silent --accept-package-agreements --accept-source-agreements
        if ($LASTEXITCODE -ne 0) { throw "winget could not install Python 3.11. See console output." }

        $Candidate = "$env:LOCALAPPDATA\Programs\Python\Python311\python.exe"
        if (Test-Path $Candidate) { $Python = $Candidate }
        if (-not $Python -and (Get-Command py -ErrorAction SilentlyContinue)) {
            $Candidate = (& py -3.11 -c "import sys; print(sys.executable)" 2>$null | Select-Object -First 1)
            if ($LASTEXITCODE -eq 0 -and (Test-Path $Candidate)) { $Python = $Candidate }
        }
    }

    if (-not $Python) { throw "Python installation completed, but python.exe could not be located. Check build.log." }

    Write-Host "Using Python: $Python"
    if (-not (Test-Path ".venv\Scripts\python.exe")) {
        & $Python -m venv .venv
        if ($LASTEXITCODE -ne 0) { throw "Could not create virtual environment." }
    }

    $BuildPython = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"
    Write-Host "Installing dependencies..."
    & $BuildPython -m pip install --upgrade pip
    if ($LASTEXITCODE -ne 0) { throw "pip upgrade failed." }
    & $BuildPython -m pip install --no-cache-dir --upgrade --force-reinstall -r requirements.txt
    if ($LASTEXITCODE -ne 0) { throw "Dependency installation failed." }
    & $BuildPython -m pip show gallery-dl
    Write-Host "gallery-dl source: https://codeberg.org/mikf/gallery-dl/archive/master.tar.gz"

    Write-Host "Running regression tests..."
    & $BuildPython -m unittest discover -s tests -v
    if ($LASTEXITCODE -ne 0) { throw "Regression tests failed; the application was not rebuilt." }

    Write-Host "Building Asset Downloader.exe..."
    & $BuildPython -m PyInstaller --noconfirm --clean --onefile --windowed `
        --name $AppName `
        --distpath "dist" `
        --workpath "build" `
        --specpath "build" `
        --icon "$($PSScriptRoot)\assets\GTL_AI_Asset_Downloader.ico" `
        --add-data "$($PSScriptRoot)\assets;assets" `
        --collect-all gallery_dl `
        --collect-all requests `
        --collect-all certifi `
        --collect-all PIL `
        --hidden-import gallery_dl.__main__ `
        --hidden-import httpx `
        main.py
    if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed. Review build.log or console output." }

    $Exe = Join-Path $PSScriptRoot "dist\Asset Downloader.exe"
    if (-not (Test-Path $Exe)) { throw "Build finished, but the EXE was not found at $Exe" }

    Write-Host "BUILD COMPLETE: $Exe" -ForegroundColor Green
    if (-not $NonInteractive) {
        Start-Process explorer.exe (Join-Path $PSScriptRoot "dist") -WindowStyle Hidden
    }
}
catch {
    $BuildExitCode = 1
    Write-Host "BUILD FAILED: $_" -ForegroundColor Red
}
finally {
    if (-not $NonInteractive) { Read-Host "Press Enter to close this window" }
}
exit $BuildExitCode
