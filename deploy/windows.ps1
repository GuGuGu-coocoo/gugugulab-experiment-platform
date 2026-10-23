param(
    [ValidateSet('Start','Stop','Status','Credentials')][string]$Action = 'Start',
    [string]$Image = '',
    [ValidateRange(1024,65535)][int]$Port = 8080
)
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$envPath = Join-Path $root '.env'
$composePath = Join-Path $root 'compose.yaml'
$dockerCommand = Get-Command docker -ErrorAction SilentlyContinue
if ($dockerCommand) { $docker = $dockerCommand.Source }
else {
    $docker = @(
        "$env:LOCALAPPDATA\Programs\DockerDesktop\resources\bin\docker.exe",
        'C:\Program Files\Docker\Docker\resources\bin\docker.exe'
    ) | Where-Object { Test-Path -LiteralPath $_ } | Select-Object -First 1
}
if (-not $docker) { throw 'Install Docker Desktop, then start its Linux container engine.' }
if (-not (Test-Path -LiteralPath $composePath)) { throw 'Keep this script inside the Docker deployment folder.' }

function Invoke-Docker {
    param([Parameter(ValueFromRemainingArguments=$true)][string[]]$Arguments)
    # Windows PowerShell 5.1 treats normal Docker progress on stderr as error
    # records. Judge the native exit code, not the progress stream.
    $before = $ErrorActionPreference
    try {
        $ErrorActionPreference = 'Continue'
        & $docker @Arguments
        $code = $LASTEXITCODE
    } finally { $ErrorActionPreference = $before }
    if ($code -ne 0) { throw "Docker failed (exit $code). Existing data was not removed." }
}
function Invoke-Compose {
    param([Parameter(ValueFromRemainingArguments=$true)][string[]]$Arguments)
    Invoke-Docker compose --project-directory $root --env-file $envPath -f $composePath @Arguments
}

$engine = Invoke-Docker info --format '{{.OSType}}'
if (($engine | Out-String).Trim() -ne 'linux') { throw 'Select Linux containers in Docker Desktop.' }
if (-not (Test-Path -LiteralPath $envPath)) {
    if ($Action -ne 'Start') { throw 'No instance is configured here. Use Start for a new instance.' }
    $manifest = Join-Path $root 'docker-release.json'
    if (-not $Image -and (Test-Path -LiteralPath $manifest)) {
        $Image = (Get-Content -LiteralPath $manifest -Raw | ConvertFrom-Json).image
    }
    if (-not $Image) { $Image = 'gep:0.1.1' }
    $images = @(Invoke-Docker image ls --format '{{.Repository}}:{{.Tag}}')
    if ($Image -notin $images) {
        $archive = Join-Path $root 'gep-image.tar'
        if (-not (Test-Path -LiteralPath $archive)) { throw "Image $Image is missing. Load the Docker release image first." }
        Invoke-Docker image load --input $archive
    }
    $project = 'gep_' + [guid]::NewGuid().ToString('N').Substring(0,12)
    $lines = @(
        "GEP_IMAGE=$Image", "GEP_PROJECT_NAME=$project", "GEP_VOLUME=$($project)_data",
        "GEP_EXPECTED_INSTANCE=$([guid]::NewGuid().ToString())", 'GEP_SETUP_ENABLED=1', 'GEP_BIND_ADDRESS=127.0.0.1', "GEP_PORT=$Port",
        'GEP_ADMIN_HOST=admin.localhost', 'GEP_EXPERIMENT_HOST=experiment.localhost', 'GEP_WWW_HOST=www.localhost',
        "GEP_ADMIN_ORIGIN=http://admin.localhost:$Port", "GEP_PUBLIC_API=http://experiment.localhost:$Port", 'GEP_HTTPS=0'
    )
    # Keep the selected volume recorded even if initialization is interrupted.
    # A later Start never silently reinitializes or substitutes another volume.
    [IO.File]::WriteAllLines($envPath, $lines, [Text.UTF8Encoding]::new($false))
}

switch ($Action) {
    'Start' {
        Invoke-Compose up -d --wait --wait-timeout 90
        Write-Host 'GEP is running. The admin and participant addresses are in .env.'
        Write-Host 'On a new instance, open the admin address and create your Owner account in the first-run page.'
    }
    'Stop' { Invoke-Compose stop }
    'Status' { Invoke-Compose ps }
    'Credentials' {
        Write-Host 'First-run passwords are chosen by you and are not saved as plaintext.'
        Write-Host 'Legacy synthetic initial login (if present): do not share this console.'
        Invoke-Compose run --rm --no-deps gep sh -c 'if [ -f /data/dev_credentials.json ]; then cat /data/dev_credentials.json; else echo "No generated credentials. Use the password you chose."; fi'
    }
}
