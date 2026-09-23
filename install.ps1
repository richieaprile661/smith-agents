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
    # Install the published release, not master: the wheel is pinned by version and checksum.
    $smithVersion = "1.1.4"
    $smithSha256 = "67ff59f9821bd25e17db07a2fe807895becb71a16981a29f8428b94cdc2abab3"
    $smithWheel = "smith_agents-$smithVersion-py3-none-any.whl"
    New-Item -ItemType Directory -Path $smithInstallDir -Force | Out-Null
    $smithPackage = $env:SMITH_AGENTS_PACKAGE
    $smithDownload = $null
    if (-not $smithPackage) {
        $smithDownload = Join-Path ([IO.Path]::GetTempPath()) ([IO.Path]::GetRandomFileName())
        New-Item -ItemType Directory -Path $smithDownload -Force | Out-Null
        $smithPackage = Join-Path $smithDownload $smithWheel
        Invoke-WebRequest -UseBasicParsing -Uri "https://github.com/richieaprile661/smith-agents/releases/download/v$smithVersion/$smithWheel" -OutFile $smithPackage
        if ((Get-FileHash -Algorithm SHA256 -LiteralPath $smithPackage).Hash -ne $smithSha256) {
            Remove-Item -LiteralPath $smithDownload -Recurse -Force
            throw "The downloaded Smith Agents $smithVersion package failed its checksum. Nothing was installed."
        }
    }
    $smithVenv = Join-Path $smithInstallDir "venv"
    & py -3 -m venv $smithVenv
    if ($LASTEXITCODE -ne 0) { throw "Could not create the Smith Agents environment." }
    $smithPython = Join-Path $smithVenv "Scripts\python.exe"
    # Reinstall even when the version matches, so a repeat run repairs the environment.
    & $smithPython -m pip install --upgrade --force-reinstall $smithPackage
    $smithPipExit = $LASTEXITCODE
    if ($smithDownload) { Remove-Item -LiteralPath $smithDownload -Recurse -Force -ErrorAction SilentlyContinue }
    if ($smithPipExit -ne 0) { throw "Installation failed. Check the error above and try again." }
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
    # Keep existing desktop and login shortcuts on the same installation.
    # Only touch shortcuts that actually launch our current or legacy command.
    foreach ($smithFolder in @([Environment]::GetFolderPath("Desktop"), [Environment]::GetFolderPath("Startup"), $smithPrograms)) {
        foreach ($smithName in @("Smith Agents.lnk", "Claude Usage.lnk", "ClaudeUsageWidget.lnk")) {
            $smithPath = Join-Path $smithFolder $smithName
            if (-not (Test-Path -LiteralPath $smithPath)) { continue }
            $smithLink = $smithShell.CreateShortcut($smithPath)
            if ((Split-Path $smithLink.TargetPath -Leaf) -notin @("smith-agents.exe", "claude-widget.exe")) { continue }
            $smithLink.TargetPath = $smithLauncher
            $smithLink.Arguments = ""
            $smithLink.WorkingDirectory = $smithInstallDir
            $smithLink.IconLocation = $smithIcon.Trim()
            $smithLink.Save()
        }
    }
    Write-Host "Smith Agents installed. You can open it from the Start Menu."
    if ($env:SMITH_AGENTS_NO_LAUNCH -ne "1") { Start-Process -FilePath $smithLauncher }
}
