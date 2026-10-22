# Machina native Windows installer (PowerShell 5.1+).
#
# One command (no WSL):
#   irm https://raw.githubusercontent.com/kiazh/Machina-Harness/main/install.ps1 | iex
#
# Optional environment overrides:
#   $env:MACHINA_INSTALL_DIR = "C:\Users\me\machina"
#   $env:MACHINA_NO_UI = "1"
#   $env:MACHINA_DEV = "1"
#   $env:MACHINA_SKIP_INIT = "1"
#
# Requires Git for Windows, Python 3.11+ (with venv), and Node.js 22.19+ for
# the terminal UI. PostgreSQL+pgvector or Docker Desktop is required for the
# database. The installer never uses a shell string to execute user input.

$ErrorActionPreference = "Stop"
$RepoUrl = if ($env:REPO_URL) { $env:REPO_URL } else { "https://github.com/kiazh/Machina-Harness.git" }
$InstallDir = if ($env:MACHINA_INSTALL_DIR) { $env:MACHINA_INSTALL_DIR } else { Join-Path (Get-Location) "machina" }
$NoUI = ($env:MACHINA_NO_UI -eq "1")
$Dev = ($env:MACHINA_DEV -eq "1")
$SkipInit = ($env:MACHINA_SKIP_INIT -eq "1")
$DefaultDb = "postgresql://postgres:postgres@localhost:5432/machina"

function Write-Step($Message) { Write-Host "[install] $Message" -ForegroundColor Cyan }
function Write-Warn($Message) { Write-Host "[warn] $Message" -ForegroundColor Yellow }
function Fail($Message) { Write-Host "[error] $Message" -ForegroundColor Red; throw $Message }

function Write-Banner {
    Write-Host ""
    Write-Host "  MACHINA" -ForegroundColor Cyan -NoNewline
    Write-Host "  native Windows installer"
    Write-Host "  install | configure | run" -ForegroundColor DarkGray
    Write-Host ""
}

function Invoke-GitClone($Url, $Destination) {
    for ($attempt = 1; $attempt -le 3; $attempt++) {
        if (Test-Path $Destination) { Remove-Item -Recurse -Force $Destination -ErrorAction SilentlyContinue }
        Write-Step "Downloading Machina (attempt $attempt/3)"
        $job = Start-Job -ScriptBlock {
            param($u, $d)
            & git clone --depth 1 --progress $u $d 2>&1
        } -ArgumentList $Url, $Destination
        $tick = 0
        while ($job.State -eq "Running") {
            $frames = @("◜", "◝", "◞", "◟")
            Write-Progress -Activity "Downloading Machina" -Status "$($frames[$tick % 4]) fetching repository" -PercentComplete -1
            $tick++
            Start-Sleep -Milliseconds 150
        }
        $output = Receive-Job -Job $job -Wait -AutoRemoveJob
        Write-Progress -Activity "Downloading Machina" -Completed
        if ($LASTEXITCODE -eq 0 -and (Test-Path (Join-Path $Destination "pyproject.toml"))) {
            Write-Step "Repository downloaded"
            return
        }
        $output | ForEach-Object { Write-Host $_ -ForegroundColor DarkGray }
        if ($attempt -lt 3) { Write-Warn "Clone failed; retrying in $attempt second(s)"; Start-Sleep -Seconds $attempt }
    }
    Fail "git clone failed after 3 attempts. Check your network/proxy and retry."
}

function Find-Python {
    $candidates = @(
        (Join-Path $env:LOCALAPPDATA "Programs\Python\Python312\python.exe"),
        (Join-Path $env:LOCALAPPDATA "Programs\Python\Python313\python.exe"),
        (Join-Path $env:LOCALAPPDATA "Programs\Python\Python314\python.exe"),
        (Join-Path $env:ProgramFiles "Python312\python.exe"),
        (Join-Path $env:ProgramFiles "Python311\python.exe")
    )
    $candidates += @("py", "python", "python3")
    foreach ($candidate in $candidates) {
        if (Test-Path $candidate) { $command = $candidate } else {
            $found = Get-Command $candidate -ErrorAction SilentlyContinue
            if (-not $found) { continue }
            $command = $found.Source
        }
        try {
            $version = & $command -c "import sys; print('%d.%d' % sys.version_info[:2])" 2>$null
            if ($LASTEXITCODE -eq 0 -and [version]$version -ge [version]"3.11") { return $command }
        } catch { }
    }
    return $null
}

function Find-Node {
    $candidates = @(
        (Join-Path $env:ProgramFiles "nodejs\node.exe"),
        (Join-Path $env:LOCALAPPDATA "Programs\nodejs\node.exe")
    )
    $candidates += @("node")
    foreach ($candidate in $candidates) {
        if (Test-Path $candidate) { $command = $candidate } else {
            $found = Get-Command $candidate -ErrorAction SilentlyContinue
            if (-not $found) { continue }
            $command = $found.Source
        }
        try {
            $version = (& $command --version).TrimStart("v")
            if ([version]$version -ge [version]"22.19") { return $command }
        } catch { }
    }
    return $null
}

function Get-EnvValue($Path, $Key) {
    if (-not (Test-Path $Path)) { return $null }
    foreach ($line in Get-Content -LiteralPath $Path) {
        if ($line -match "^\s*$([regex]::Escape($Key))\s*=(.*)$") { return $matches[1].Trim() }
    }
    return $null
}

function Set-EnvValue($Path, $Key, $Value) {
    $lines = if (Test-Path $Path) { @(Get-Content -LiteralPath $Path) } else { @() }
    $pattern = "^\s*$([regex]::Escape($Key))\s*="
    $found = $false
    $updated = foreach ($line in $lines) {
        if ($line -match $pattern) { $found = $true; "$Key=$Value" } else { $line }
    }
    if (-not $found) { $updated = @($updated) + "$Key=$Value" }
    $updated | Set-Content -LiteralPath $Path -Encoding utf8
}

function New-HexSecret([int]$Bytes = 32) {
    $buffer = [byte[]]::new($Bytes)
    [System.Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($buffer)
    return ($buffer | ForEach-Object { $_.ToString("x2") }) -join ""
}

function New-Base64Secret([int]$Bytes = 32) {
    $buffer = [byte[]]::new($Bytes)
    [System.Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($buffer)
    return [Convert]::ToBase64String($buffer)
}

function Install-WingetPackage($Id, $Name) {
    $winget = Get-Command winget -ErrorAction SilentlyContinue
    if (-not $winget) { return $false }
    Write-Step "Installing $Name with winget"
    & winget install --id $Id -e --silent --accept-package-agreements --accept-source-agreements | Out-Host
    return ($LASTEXITCODE -eq 0)
}

function Find-ToolPath($Name, $Candidates) {
    $cmd = Get-Command $Name -ErrorAction SilentlyContinue
    if ($cmd) { return $cmd.Source }
    foreach ($candidate in $Candidates) {
        if (Test-Path $candidate) { return $candidate }
    }
    return $null
}

function Refresh-ToolPaths {
    $env:Path = [Environment]::GetEnvironmentVariable("Path", "Machine") + ";" +
        [Environment]::GetEnvironmentVariable("Path", "User")
}

function Ensure-UserPath($Directory) {
    $userPath = [Environment]::GetEnvironmentVariable("Path", "User")
    $parts = @($userPath -split ";" | Where-Object { $_ })
    if ($parts -notcontains $Directory) {
        [Environment]::SetEnvironmentVariable("Path", (($parts + $Directory) -join ";"), "User")
        Write-Step "Added $Directory to the user PATH (new terminals will see it)"
    }
    if (($env:Path -split ";") -notcontains $Directory) { $env:Path = "$env:Path;$Directory" }
}

function Start-DockerDatabase($EnvFile) {
    $docker = Get-Command docker -ErrorAction SilentlyContinue
    if (-not $docker) { return $false }
    & docker info *> $null
    if ($LASTEXITCODE -ne 0) { return $false }
    $name = "machina-db"
    $exists = (& docker ps -a --filter "name=^/$name$" --format "{{.Names}}").Trim()
    if (-not $exists) {
        $password = New-HexSecret 16
        Write-Step "Starting PostgreSQL with Docker"
        & docker run -d --name $name -e POSTGRES_USER=postgres -e "POSTGRES_PASSWORD=$password" -e POSTGRES_DB=machina -p 127.0.0.1:5432:5432 pgvector/pgvector:pg16 | Out-Null
        if ($LASTEXITCODE -ne 0) { return $false }
    } else {
        & docker start $name *> $null
        $inspect = (& docker inspect -f "{{range .Config.Env}}{{println .}}{{end}}" $name)
        $password = ($inspect | Where-Object { $_ -match "^POSTGRES_PASSWORD=" } | ForEach-Object { $_.Substring(18) } | Select-Object -First 1)
    }
    if (-not $password) { return $false }
    Set-EnvValue $EnvFile "DATABASE_URL" "postgresql://postgres:$password@127.0.0.1:5432/machina"
    for ($i = 0; $i -lt 40; $i++) {
        & docker exec $name pg_isready -U postgres *> $null
        if ($LASTEXITCODE -eq 0) { return $true }
        Start-Sleep -Seconds 1
    }
    return $false
}

Write-Banner
Write-Step "Detected OS: Windows"
Write-Step "PowerShell $($PSVersionTable.PSVersion)"

if (-not (Get-Command git -ErrorAction SilentlyContinue)) {
    Write-Warn "Git for Windows is required; trying winget"
    Install-WingetPackage "Git.Git" "Git for Windows" | Out-Null
    Refresh-ToolPaths
}
if (-not (Get-Command git -ErrorAction SilentlyContinue)) {
    Fail "Git for Windows is required: winget install --id Git.Git -e"
}

$python = Find-Python
if (-not $python) {
    Write-Warn "Python 3.11+ was not found; trying winget"
    Install-WingetPackage "Python.Python.3.12" "Python 3.12" | Out-Null
    Refresh-ToolPaths
    $python = Find-Python
}
if (-not $python) { Fail "Python 3.11+ is required: winget install --id Python.Python.3.12 -e" }
Write-Step "Python: $python"

$node = Find-Node
if (-not $node -and -not $NoUI) {
    Write-Warn "Node.js 22.19+ was not found; trying winget"
    Install-WingetPackage "OpenJS.NodeJS.LTS" "Node.js LTS" | Out-Null
    Refresh-ToolPaths
    $node = Find-Node
}
if (-not $node) {
    if (-not $NoUI) {
        Write-Warn "Node.js 22.19+ is unavailable; installing the backend without the terminal UI."
        $NoUI = $true
    }
} else {
    Write-Step "Node.js: $(& $node --version)"
}

$cwdLooksLikeRepo = (Test-Path ".\pyproject.toml") -and (Test-Path ".\machina") -and (Test-Path ".\ui")
if ($cwdLooksLikeRepo) {
    $AppDir = (Get-Location).Path
    Write-Step "Using current checkout: $AppDir"
} else {
    $AppDir = [System.IO.Path]::GetFullPath($InstallDir)
    if (Test-Path $AppDir) {
        if (Test-Path (Join-Path $AppDir "pyproject.toml")) {
            Write-Step "Using existing checkout: $AppDir"
            Push-Location $AppDir
            & git pull --ff-only 2>$null | Out-Null
            Pop-Location
        } else {
            Fail "Install directory exists but is not a Machina checkout: $AppDir"
        }
    } else {
        Invoke-GitClone $RepoUrl $AppDir
    }
}
Set-Location $AppDir
if (-not (Test-Path ".\pyproject.toml")) { Fail "Machina checkout is missing pyproject.toml" }

$venv = Join-Path $AppDir ".venv"
$venvPython = Join-Path $venv "Scripts\python.exe"
if (-not (Test-Path $venvPython)) {
    Write-Step "Creating virtualenv (.venv)"
    & $python -m venv $venv
    if ($LASTEXITCODE -ne 0 -or -not (Test-Path $venvPython)) {
        Fail "Could not create .venv. Run 'py -3.12 -m venv .venv' and retry; Python must include venv/ensurepip support."
    }
}
Write-Step "Installing Machina"
& $venvPython -m pip install --quiet --upgrade pip
if ($LASTEXITCODE -ne 0) { Fail "pip upgrade failed" }
if ($Dev) {
    & $venvPython -m pip install -e ".[dev]"
} else {
    & $venvPython -m pip install -e .
}
if ($LASTEXITCODE -ne 0) { Fail "Machina dependency installation failed" }

$machina = Join-Path $venv "Scripts\machina.exe"
if (-not (Test-Path $machina)) { Fail "Installation completed but machina.exe was not created" }

if (-not $NoUI) {
    Write-Step "Installing UI dependencies"
    $npm = Join-Path (Split-Path $node) "npm.cmd"
    if (-not (Test-Path $npm)) { $npm = "npm" }
    & $npm ci --ignore-scripts --prefix ui
    if ($LASTEXITCODE -ne 0) { Fail "UI dependency installation failed; retry with `$env:MACHINA_NO_UI=1` for backend only." }
}

$envFile = if ($env:MACHINA_ENV_FILE) { [System.IO.Path]::GetFullPath($env:MACHINA_ENV_FILE) } else { Join-Path $AppDir ".env" }
if (-not (Test-Path $envFile)) {
    Copy-Item ".env.example" $envFile
    Write-Step "Created $envFile"
} else {
    Write-Step "Keeping existing $envFile (filling missing values only)"
}
foreach ($key in @("MACHINA_API_KEY", "MACHINA_PROVENANCE_KEY")) {
    $value = Get-EnvValue $envFile $key
    if (-not $value -or $value -in @("replace-with-a-long-random-key", "replace-with-a-separate-long-random-key", "sk-or-...", "sk-...")) {
        Set-EnvValue $envFile $key (New-HexSecret)
        Write-Step "Generated random $key"
    }
}
$oauth = Get-EnvValue $envFile "MACHINA_OAUTH_MASTER_KEY"
if (-not $oauth -or $oauth -eq "replace-with-a-base64-32-byte-key") {
    Set-EnvValue $envFile "MACHINA_OAUTH_MASTER_KEY" (New-Base64Secret)
    Write-Step "Generated MACHINA_OAUTH_MASTER_KEY"
}
if (-not (Get-EnvValue $envFile "DATABASE_URL")) { Set-EnvValue $envFile "DATABASE_URL" $DefaultDb }
$env:MACHINA_ENV_FILE = $envFile

if (-not $SkipInit) {
    Write-Step "Initializing the database schema"
    & $machina init
    if ($LASTEXITCODE -ne 0) {
        if (Start-DockerDatabase $envFile) {
            Write-Step "Docker PostgreSQL ready; retrying schema initialization"
            & $machina init
        }
        if ($LASTEXITCODE -ne 0) {
            Write-Warn "machina init failed. Start PostgreSQL+pgvector (or Docker Desktop) and set DATABASE_URL in $envFile."
            Write-Warn "Docker example:"
            Write-Warn "  docker run -d --name machina-db -e POSTGRES_USER=postgres -e POSTGRES_PASSWORD=postgres -e POSTGRES_DB=machina -p 127.0.0.1:5432:5432 pgvector/pgvector:pg16"
            Write-Warn "Then run: `"$machina`" init"
        }
    } else {
        Write-Step "Database schema ready"
    }
} else {
    Write-Step "Skipping database initialization"
}

& $machina doctor
Ensure-UserPath (Join-Path $venv "Scripts")
Write-Host ""
Write-Host "Machina is installed." -ForegroundColor Green
Write-Host "Next:"
Write-Host "  machina setup"
Write-Host "  machina"
Write-Host ""
Write-Host "If this PowerShell window does not know 'machina' yet, open a new terminal."
Write-Host "Docs: https://github.com/kiazh/Machina-Harness#get-started"
