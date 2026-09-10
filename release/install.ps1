<#
    Smith Agents - install.

    Unzip this folder anywhere, then from inside it:

        powershell -ExecutionPolicy Bypass -File .\install.ps1

    It installs the widget for the current user, adds it to the Start Menu,
    sets it to start with Windows and launches it. Nothing is written outside
    your own profile and nothing needs administrator rights.

    It needs Python 3.10 or newer with the py launcher. If that is missing it
    says so and offers to install it with winget, rather than failing with a
    stack trace - that is the one thing that actually goes wrong.
#>
param(
    # The wheel to install. By default the one sitting beside this script; a
    # path or a URL both work, since pip takes either.
    [string]$Wheel,
    [switch]$NoStartup,
    [switch]$NoLaunch
)

$ErrorActionPreference = "Stop"
$AppName = "Smith Agents"

function Say($text)  { Write-Host $text }
function Step($text) { Write-Host "  $text" -ForegroundColor DarkGray }
function Warn($text) { Write-Host $text -ForegroundColor Yellow }

Say ""
Say "$AppName"
Say ""

# --- 0. The wheel ----------------------------------------------------------
if (-not $Wheel) {
    $here = Split-Path -Parent $MyInvocation.MyCommand.Path
    $found = Get-ChildItem (Join-Path $here "smith_agents-*.whl") -ErrorAction SilentlyContinue |
             Sort-Object Name | Select-Object -Last 1
    if (-not $found) {
        Warn "No current Smith Agents wheel found next to this script."
        Warn "Keep install.ps1 and the wheel in the same folder, or pass -Wheel <path>."
        return
    }
    $Wheel = $found.FullName
}
Step ("installing " + (Split-Path $Wheel -Leaf))

# --- 1. Python -------------------------------------------------------------
# "py" is what the widget itself uses to find its interpreter, so it is what
# gets checked here. Python from the Microsoft Store does not ship it.
$py = Get-Command py -ErrorAction SilentlyContinue
if (-not $py) {
    Warn "Python's py launcher was not found."
    $winget = Get-Command winget -ErrorAction SilentlyContinue
    if ($winget) {
        $answer = Read-Host "Install Python 3.12 now with winget? [Y/n]"
        if ($answer -eq "" -or $answer -match "^[Yy]") {
            Step "installing Python..."
            winget install --id Python.Python.3.12 --accept-source-agreements --accept-package-agreements
            $env:Path = [Environment]::GetEnvironmentVariable("Path", "Machine") + ";" +
                        [Environment]::GetEnvironmentVariable("Path", "User")
            $py = Get-Command py -ErrorAction SilentlyContinue
        }
    }
    if (-not $py) {
        Warn ""
        Warn "Install Python 3.10 or newer from https://www.python.org/downloads/"
        Warn 'and keep "py launcher" ticked in the installer, then run this again.'
        Warn "The Microsoft Store build will not do: it has no py launcher."
        return
    }
}

$version = (& py -c "import sys;print('%d.%d' % sys.version_info[:2])").Trim()
Step "Python $version"

# --- 2. The widget ---------------------------------------------------------
Step "installing Smith Agents and its dependencies..."
& py -m pip install --quiet --upgrade --disable-pip-version-check $Wheel
if ($LASTEXITCODE -ne 0) { throw "pip could not install $Wheel" }

# where pip put the command it created for us
$scripts = (& py -c "import sysconfig;print(sysconfig.get_path('scripts'))").Trim()
$exe = Join-Path $scripts "smith-agents.exe"
if (-not (Test-Path $exe)) { throw "installed, but $exe is missing" }
$icon = (& py -c "import os,smith_agents;print(os.path.join(os.path.dirname(smith_agents.__file__),'assets','smith-agents.ico'))").Trim()

# --- 3. Shortcuts ----------------------------------------------------------
function New-Shortcut($path, $target, $iconPath) {
    $shell = New-Object -ComObject WScript.Shell
    $link = $shell.CreateShortcut($path)
    $link.TargetPath = $target
    $link.WorkingDirectory = Split-Path $target
    $link.WindowStyle = 7
    if ($iconPath -and (Test-Path $iconPath)) { $link.IconLocation = $iconPath }
    $link.Save()
}

function Remove-LegacyShortcut($path) {
    if (-not (Test-Path $path)) { return }
    $shell = New-Object -ComObject WScript.Shell
    $link = $shell.CreateShortcut($path)
    $targetName = Split-Path $link.TargetPath -Leaf
    if ($targetName -in @("claude-widget.exe", "smith-agents.exe")) {
        Remove-Item -LiteralPath $path
    }
}

$menu = Join-Path $env:APPDATA "Microsoft\Windows\Start Menu\Programs\$AppName.lnk"
New-Shortcut $menu $exe $icon
Remove-LegacyShortcut (Join-Path $env:APPDATA "Microsoft\Windows\Start Menu\Programs\Claude Usage.lnk")
Step "added to the Start Menu"

if (-not $NoStartup) {
    $startup = Join-Path $env:APPDATA "Microsoft\Windows\Start Menu\Programs\Startup\Smith Agents.lnk"
    New-Shortcut $startup $exe $icon
    Remove-LegacyShortcut (Join-Path $env:APPDATA "Microsoft\Windows\Start Menu\Programs\Startup\ClaudeUsageWidget.lnk")
    Step "starts with Windows"
}

# --- 4. Off it goes --------------------------------------------------------
if (-not $NoLaunch) {
    Start-Process -FilePath $exe
    Step "started"
}

Say ""
Say "Done. The small figure in your notification area is the widget."
Say "  left click     refresh now"
Say "  right click    themes, size, dock, tuck to edge, quit"
Say "  the console    click readings for used %, remaining %, or reset time"
Say ""
Say "Sign in through Claude Code or Codex for that provider's usage readings."
Say "The agent list supports both providers."
Say ""
Say "To remove it:  py -m pip uninstall smith-agents"
Say ""
