# GPR Analyzer launcher for Windows. Started by START-GPR-ANALYZER.bat (double-click).
#
# First run: finds Python 3.10+ (installs Python 3.12 for this user with winget if none),
# makes a private environment in %LOCALAPPDATA%\GPR-Analyzer and installs the program into it.
# Every run: reinstalls only if the program files changed (e.g. a newer ZIP was unpacked here),
# then starts the local app and opens it in the browser. Jobs are kept in Documents\GPR-Analyzer-jobs.
#
# Options: -Reinstall (force a clean install), -NoBrowser, -Port 8765, -SmokeTest (install, start,
# open the demo job through the API, stop; used by CI on a real Windows machine).
# Messages are English on purpose: Windows PowerShell 5.1 consoles do not show Thai reliably.

param([switch]$Reinstall, [switch]$NoBrowser, [switch]$SmokeTest, [int]$Port = 8765)

$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"
$Root = Split-Path -Parent $PSScriptRoot
$AppHome = Join-Path $env:LOCALAPPDATA "GPR-Analyzer"
$Venv = Join-Path $AppHome "venv"
$VenvPy = Join-Path $Venv "Scripts\python.exe"
$Stamp = Join-Path $AppHome "installed.stamp"
$Jobs = Join-Path ([Environment]::GetFolderPath("MyDocuments")) "GPR-Analyzer-jobs"
$Url = "http://127.0.0.1:$Port/"

function Say($msg, $color = "Gray") { Write-Host $msg -ForegroundColor $color }
function Fail($msg) {
    Say ""
    Say "PROBLEM: $msg" "Red"
    Say "Send a photo of this window to the developer if you are stuck." "Yellow"
    exit 1
}

function Test-Python($exe, [string[]]$pre) {
    try {
        $v = & $exe @pre -c "import sys; print('%d.%d' % sys.version_info[:2])" 2>$null
        if ($LASTEXITCODE -eq 0 -and $v -and ([version]$v -ge [version]"3.10")) { return $true }
    } catch { }
    return $false
}

function Find-Python {
    # The py launcher first: the "python" on PATH may be the Microsoft Store stub.
    foreach ($ver in "-3.12", "-3.11", "-3.13", "-3.10") {
        if ((Get-Command py -ErrorAction SilentlyContinue) -and (Test-Python "py" @($ver))) { return @("py", $ver) }
    }
    foreach ($p in @("$env:LOCALAPPDATA\Programs\Python\Python312\python.exe",
                     "$env:LOCALAPPDATA\Programs\Python\Python311\python.exe",
                     "$env:ProgramFiles\Python312\python.exe")) {
        if ((Test-Path $p) -and (Test-Python $p @())) { return @($p) }
    }
    if ((Get-Command python -ErrorAction SilentlyContinue) -and (Test-Python "python" @())) { return @("python") }
    return $null
}

function Get-SourceStamp {
    $files = @(Get-Item (Join-Path $Root "pyproject.toml")) +
             @(Get-ChildItem (Join-Path $Root "gpr_analyzer") -Recurse -File |
               Where-Object { $_.FullName -notmatch "__pycache__" })
    $text = ($files | Sort-Object FullName | ForEach-Object {
        "$($_.FullName.Substring($Root.Length))|$($_.Length)|$($_.LastWriteTimeUtc.Ticks)" }) -join "`n"
    $sha = [System.Security.Cryptography.SHA256]::Create()
    return [BitConverter]::ToString($sha.ComputeHash([Text.Encoding]::UTF8.GetBytes($text))).Replace("-", "")
}

function Test-AppRunning {
    try { Invoke-WebRequest -UseBasicParsing -TimeoutSec 2 "$($Url)api/jobs" | Out-Null; return $true } catch { return $false }
}

Say "GPR Analyzer" "Cyan"
Say "Program folder: $Root"

if (-not (Test-Path (Join-Path $Root "pyproject.toml"))) {
    Fail "pyproject.toml not found next to the 'windows' folder. Unpack the whole ZIP first, then run START-GPR-ANALYZER.bat from the unpacked folder."
}

if (-not $SmokeTest -and (Test-AppRunning)) {
    Say "The app is already running; opening it in the browser." "Green"
    if (-not $NoBrowser) { Start-Process $Url }
    exit 0
}

# ---------------------------------------------------------------- Python
if ($Reinstall -and (Test-Path $Venv)) {
    Say "Removing the old environment..."
    Remove-Item -Recurse -Force $Venv
}
if (-not (Test-Path $VenvPy)) {
    $py = Find-Python
    if (-not $py) {
        Say "Python 3.10 or newer was not found." "Yellow"
        if (Get-Command winget -ErrorAction SilentlyContinue) {
            Say "Installing Python 3.12 for this Windows user (about 2-5 minutes, no admin rights needed)..."
            winget install -e --id Python.Python.3.12 --scope user --silent --accept-package-agreements --accept-source-agreements
            $py = Find-Python
        }
        if (-not $py) {
            Start-Process "https://www.python.org/downloads/windows/"
            Fail "Please install Python 3.12 from the page that just opened (tick 'Add python.exe to PATH'), then double-click START-GPR-ANALYZER.bat again."
        }
    }
    Say "Using Python: $($py -join ' ')"
    New-Item -ItemType Directory -Force $AppHome | Out-Null
    Say "Creating a private environment in $Venv ..."
    $exe = $py[0]; $pre = @($py | Select-Object -Skip 1)
    & $exe @pre -m venv $Venv
    if ($LASTEXITCODE -ne 0 -or -not (Test-Path $VenvPy)) { Fail "could not create the Python environment." }
    if (Test-Path $Stamp) { Remove-Item $Stamp }
}

# ---------------------------------------------------------------- program
$want = Get-SourceStamp
$have = if (Test-Path $Stamp) { (Get-Content $Stamp -Raw).Trim() } else { "" }
if ($want -ne $have) {
    Say "Installing the program and its libraries (first time: about 3-10 minutes, needs internet)..." "Yellow"
    & $VenvPy -m pip install --disable-pip-version-check --upgrade pip | Out-Null
    Push-Location $Root
    try {
        & $VenvPy -m pip install --disable-pip-version-check ".[app]"
        $code = $LASTEXITCODE
    } finally { Pop-Location }
    if ($code -ne 0) { Fail "installing the program failed (see the messages above). Check the internet connection and try again." }
    Set-Content -Path $Stamp -Value $want -Encoding ascii
    Say "Installed." "Green"
}

# ---------------------------------------------------------------- shortcut
$lnk = Join-Path ([Environment]::GetFolderPath("Desktop")) "GPR Analyzer.lnk"
if (-not $SmokeTest -and -not (Test-Path $lnk)) {
    try {
        $sc = (New-Object -ComObject WScript.Shell).CreateShortcut($lnk)
        $sc.TargetPath = Join-Path $Root "START-GPR-ANALYZER.bat"
        $sc.WorkingDirectory = $Root
        $sc.Description = "GPR Analyzer (local)"
        $sc.Save()
        Say "Added a 'GPR Analyzer' shortcut to the desktop."
    } catch { }
}

# ---------------------------------------------------------------- run
New-Item -ItemType Directory -Force $Jobs | Out-Null
$appArgs = @("-m", "gpr_analyzer.app", "--port", "$Port", "--workdir", $Jobs)

if ($SmokeTest) {
    $proc = Start-Process -FilePath $VenvPy -ArgumentList ($appArgs + "--no-browser") -PassThru -WindowStyle Hidden
    try {
        $ok = $false
        foreach ($i in 1..60) { Start-Sleep 1; if (Test-AppRunning) { $ok = $true; break } }
        if (-not $ok) { Fail "the app did not start within 60 s." }
        $job = Invoke-RestMethod -Method Post "$($Url)api/demo" -ContentType "application/json" -Body "{}"
        if ($job.lines.Count -lt 1) { Fail "demo job has no lines." }
        Invoke-WebRequest -UseBasicParsing "$($Url)api/jobs/$($job.job_id)/export/report.pdf" -OutFile (Join-Path $env:TEMP "gpr-smoke.pdf")
        Say "Smoke test passed: $($job.lines.Count) lines, report written." "Green"
    } finally { Stop-Process -Id $proc.Id -Force -ErrorAction SilentlyContinue }
    exit 0
}

Say ""
Say "Starting... the browser opens by itself at $Url" "Green"
Say "Your jobs are saved in: $Jobs"
Say "KEEP THIS WINDOW OPEN while you use the app. Close it (or press Ctrl+C) to stop." "Yellow"
Say ""
if ($NoBrowser) { $appArgs += "--no-browser" }
& $VenvPy @appArgs
