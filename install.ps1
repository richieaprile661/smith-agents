# Install Smith Agents into a private virtual environment on Windows.
& {
    $ErrorActionPreference = "Stop"
    if (-not (Get-Command py -ErrorAction SilentlyContinue)) {
        throw "Install Python 3.10 or newer from python.org with the Python launcher, then run this command again."
    }
    & py -3 -c "import sys; sys.exit(sys.version_info < (3, 10))"
    if ($LASTEXITCODE -ne 0) { throw "Smith Agents needs Python 3.10 or newer." }

    $smithInstallDir = $env:SMITH_AGENTS_INSTALL_DIR
    if (-not $smithInstallDir) { $smithInstallDir = Join-Path $env:LOCALAPPDATA "Smith Agents" }
    $smithPackage = $env:SMITH_AGENTS_PACKAGE
    if (-not $smithPackage) {
        $smithPackage = "https://github.com/richieaprile661/smith-agents/archive/refs/heads/master.zip"
    }
    $smithVenv = Join-Path $smithInstallDir "venv"
    New-Item -ItemType Directory -Path $smithInstallDir -Force | Out-Null
    & py -3 -m venv $smithVenv
    if ($LASTEXITCODE -ne 0) { throw "Could not create the Smith Agents environment." }
    $smithPython = Join-Path $smithVenv "Scripts\python.exe"
    # master can contain newer code with the same package version.
    & $smithPython -m pip install --upgrade --force-reinstall $smithPackage
    if ($LASTEXITCODE -ne 0) { throw "Installation failed. Check the error above and try again." }
    & $smithPython -c "import smith_agents"
    if ($LASTEXITCODE -ne 0) { throw "The installed package does not contain Smith Agents." }

    $smithLauncher = Join-Path $smithVenv "Scripts\smith-agents.exe"
    $smithPrograms = [Environment]::GetFolderPath("Programs")
    New-Item -ItemType Directory -Path $smithPrograms -Force | Out-Null
    $smithShell = New-Object -ComObject WScript.Shell
    $smithShortcut = $smithShell.CreateShortcut((Join-Path $smithPrograms "Smith Agents.lnk"))
    $smithShortcut.TargetPath = $smithLauncher
    $smithShortcut.WorkingDirectory = $smithInstallDir
    $smithShortcut.Description = "Smith Agents"
    $smithIcon = & $smithPython -c "from pathlib import Path; import smith_agents; print(Path(smith_agents.__file__).parent / 'assets' / 'smith-agents.ico')"
    if ($LASTEXITCODE -ne 0) { throw "Could not locate the app icon." }
    $smithShortcut.IconLocation = $smithIcon.Trim()
    $smithShortcut.Save()
    Write-Host "Smith Agents installed. You can open it from the Start Menu."
    if ($env:SMITH_AGENTS_NO_LAUNCH -ne "1") { Start-Process -FilePath $smithLauncher }
}
