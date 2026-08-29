[CmdletBinding()]
param(
    [string]$Endpoint = $null,
    [string]$RepoRoot = $null,
    [string]$HostName = "127.0.0.1",
    [int]$Port = 8876,
    [ValidateSet("active", "history", "evidence", "all")]
    [string]$Mode = "all",
    [int]$Limit = 120,
    [double]$PollSeconds = 2.0,
    [int]$StartupTimeoutSeconds = 25,
    [string]$Python = $env:SHUJUAN_PYTHON,
    [switch]$NoOpen,
    [switch]$NoStart,
    [switch]$PassThru
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$scriptRoot = $PSScriptRoot
if (-not $scriptRoot) {
    $scriptRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
}
if ([string]::IsNullOrWhiteSpace($RepoRoot)) {
    $RepoRoot = (Resolve-Path (Join-Path $scriptRoot "..\..")).Path
}

function Resolve-PythonCommand {
    if ($Python) {
        return $Python
    }
    foreach ($candidate in @("python", "py")) {
        $command = Get-Command $candidate -ErrorAction SilentlyContinue
        if ($command) {
            return $command.Source
        }
    }
    throw "Python was not found. Set SHUJUAN_PYTHON or install Python on PATH."
}

function Get-ProjectName {
    param([string]$Repository)

    return (Split-Path -Leaf (Resolve-Path $Repository).Path)
}

function Test-AutomaticEndpointValue {
    param([string]$Value)

    if ([string]::IsNullOrWhiteSpace($Value)) {
        return $true
    }
    $normalized = $Value.Trim().ToLowerInvariant()
    return @("auto", "@auto", "default", "@default", "project", "@project", "project-workbench", "@project.workbench") -contains $normalized
}

function Resolve-EndpointCandidate {
    param(
        [string]$PythonCommand,
        [string]$Repository,
        [string]$Candidate
    )

    $output = & $PythonCommand -m shujuan --repo $Repository report endpoint $Candidate --active-only --json 2>$null
    if ($LASTEXITCODE -ne 0) {
        return $null
    }
    $jsonText = ($output | Out-String).Trim()
    if ([string]::IsNullOrWhiteSpace($jsonText)) {
        return $null
    }
    try {
        $payload = $jsonText | ConvertFrom-Json
    }
    catch {
        return $null
    }
    if ($payload.ok -and $payload.endpoint) {
        return [string]$payload.endpoint
    }
    return $null
}

function Resolve-WorkbenchEndpoint {
    param(
        [string]$PythonCommand,
        [string]$Repository,
        [string]$RequestedEndpoint
    )

    if (-not (Test-AutomaticEndpointValue -Value $RequestedEndpoint)) {
        $resolved = Resolve-EndpointCandidate -PythonCommand $PythonCommand -Repository $Repository -Candidate $RequestedEndpoint
        if ($resolved) {
            return $resolved
        }
        return $RequestedEndpoint
    }

    $projectName = Get-ProjectName -Repository $Repository
    $candidates = @(
        "$projectName-endpoint-workbench",
        "$projectName-workbench",
        "workbench",
        "@last.endpoint",
        "@current.endpoint"
    )
    foreach ($candidate in $candidates) {
        $resolved = Resolve-EndpointCandidate -PythonCommand $PythonCommand -Repository $Repository -Candidate $candidate
        if ($resolved) {
            return $resolved
        }
    }
    throw "Could not resolve a project workbench endpoint. Pass -Endpoint <name> for historical inspection or create a project workbench endpoint."
}

function Test-WorkbenchService {
    param(
        [string]$BaseUrl,
        [string]$ExpectedEndpoint,
        [string]$ProjectionMode
    )

    $modeValue = [uri]::EscapeDataString($ProjectionMode)
    $cacheBust = [uri]::EscapeDataString([DateTimeOffset]::UtcNow.ToUnixTimeMilliseconds().ToString())
    $uri = "$BaseUrl/api/projection?mode=$modeValue&limit=1&_=$cacheBust"
    $shellUri = "$BaseUrl/workbench?mode=$modeValue&limit=1&_=$cacheBust"
    try {
        $payload = Invoke-RestMethod -Uri $uri -TimeoutSec 3 -Headers @{ "Cache-Control" = "no-store" }
        $payloadEndpoint = [string]$payload.endpoint
        $healthy = $payloadEndpoint -eq $ExpectedEndpoint
        $errorText = $null
        if (-not $healthy) {
            $errorText = "Projection endpoint mismatch. Expected '$ExpectedEndpoint' but service reported '$payloadEndpoint'."
        }
        if ($healthy) {
            $shellResponse = Invoke-WebRequest -Uri $shellUri -TimeoutSec 3 -Headers @{ "Cache-Control" = "no-store" } -UseBasicParsing
            $shellHtml = [string]$shellResponse.Content
            $escapedMode = [regex]::Escape($ProjectionMode)
            $requestedModeSelected = [regex]::IsMatch($shellHtml, "<option\b(?=[^>]*\bvalue=`"$escapedMode`")(?=[^>]*\bselected\b)[^>]*>", [System.Text.RegularExpressions.RegexOptions]::IgnoreCase)
            $activeSelected = [regex]::IsMatch($shellHtml, "<option\b(?=[^>]*\bvalue=`"active`")(?=[^>]*\bselected\b)[^>]*>", [System.Text.RegularExpressions.RegexOptions]::IgnoreCase)
            if (-not $requestedModeSelected) {
                $healthy = $false
                $errorText = "Workbench shell did not select requested mode '$ProjectionMode'."
            }
            elseif ($ProjectionMode -eq "all" -and $activeSelected) {
                $healthy = $false
                $errorText = "Workbench shell selected stale active mode for all-mode request."
            }
        }
        return [pscustomobject]@{
            Healthy = $healthy
            Endpoint = $payloadEndpoint
            Error = $errorText
            GeneratedAt = $payload.generated_at
        }
    }
    catch {
        return [pscustomobject]@{
            Healthy = $false
            Endpoint = $null
            Error = $_.Exception.Message
            GeneratedAt = $null
        }
    }
}

function Resolve-ListenAddress {
    param([string]$Name)

    try {
        return [System.Net.IPAddress]::Parse($Name)
    }
    catch {
        $addresses = [System.Net.Dns]::GetHostAddresses($Name) | Where-Object { $_.AddressFamily -eq [System.Net.Sockets.AddressFamily]::InterNetwork }
        if ($addresses -and $addresses.Count -gt 0) {
            return $addresses[0]
        }
        return [System.Net.IPAddress]::Loopback
    }
}

function Test-PortAvailable {
    param(
        [string]$Name,
        [int]$PortNumber
    )

    $listener = $null
    try {
        $listener = [System.Net.Sockets.TcpListener]::new((Resolve-ListenAddress -Name $Name), $PortNumber)
        $listener.Start()
        return $true
    }
    catch {
        return $false
    }
    finally {
        if ($listener) {
            $listener.Stop()
        }
    }
}

function Test-PortAcceptsTcpConnection {
    param(
        [string]$Name,
        [int]$PortNumber,
        [int]$TimeoutMilliseconds = 750
    )

    $client = [System.Net.Sockets.TcpClient]::new()
    try {
        $task = $client.ConnectAsync($Name, $PortNumber)
        if (-not $task.Wait($TimeoutMilliseconds)) {
            return $false
        }
        return [bool]$client.Connected
    }
    catch {
        return $false
    }
    finally {
        $client.Close()
    }
}

function Find-AvailablePort {
    param(
        [string]$Name,
        [int]$PreferredPort
    )

    $start = [Math]::Max(1024, $PreferredPort + 1)
    for ($candidate = $start; $candidate -le 65535; $candidate++) {
        if ((-not (Test-PortAcceptsTcpConnection -Name $Name -PortNumber $candidate)) -and (Test-PortAvailable -Name $Name -PortNumber $candidate)) {
            return $candidate
        }
    }
    throw "No available fallback port was found after preferred port $PreferredPort."
}

function Find-HealthyFallbackService {
    param(
        [string]$Name,
        [int]$PreferredPort,
        [string]$ExpectedEndpoint,
        [string]$ProjectionMode,
        [int]$MaxOffset = 20
    )

    $lastPort = [Math]::Min(65535, $PreferredPort + $MaxOffset)
    for ($candidate = $PreferredPort + 1; $candidate -le $lastPort; $candidate++) {
        if (-not (Test-PortAcceptsTcpConnection -Name $Name -PortNumber $candidate)) {
            continue
        }
        $candidateBaseUrl = "http://$Name`:$candidate"
        $candidateService = Test-WorkbenchService -BaseUrl $candidateBaseUrl -ExpectedEndpoint $ExpectedEndpoint -ProjectionMode $ProjectionMode
        if ($candidateService.Healthy) {
            return [pscustomobject]@{
                Port = $candidate
                BaseUrl = $candidateBaseUrl
                Service = $candidateService
            }
        }
    }
    return $null
}

function Wait-WorkbenchService {
    param(
        [string]$BaseUrl,
        [string]$ExpectedEndpoint,
        [string]$ProjectionMode,
        [int]$TimeoutSeconds
    )

    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    $last = $null
    while ((Get-Date) -lt $deadline) {
        $last = Test-WorkbenchService -BaseUrl $BaseUrl -ExpectedEndpoint $ExpectedEndpoint -ProjectionMode $ProjectionMode
        if ($last.Healthy) {
            return $last
        }
        Start-Sleep -Milliseconds 300
    }
    if ($last) {
        return $last
    }
    return [pscustomobject]@{
        Healthy = $false
        Endpoint = $null
        Error = "Timed out before first probe."
        GeneratedAt = $null
    }
}

function Start-WorkbenchProcess {
    param(
        [string]$PythonCommand,
        [string]$Repository,
        [string]$EndpointName,
        [string]$HostValue,
        [int]$PortNumber,
        [string]$ProjectionMode,
        [int]$ProjectionLimit,
        [double]$PollIntervalSeconds,
        [string]$LogDirectory
    )

    New-Item -ItemType Directory -Force -Path $LogDirectory | Out-Null
    $stamp = Get-Date -Format "yyyyMMdd-HHmmss"
    $stdoutPath = Join-Path $LogDirectory "workbench-$PortNumber-$stamp.out.log"
    $stderrPath = Join-Path $LogDirectory "workbench-$PortNumber-$stamp.err.log"
    $arguments = @(
        "-m", "shujuan",
        "--repo", $Repository,
        "workbench", "serve",
        "--endpoint", $EndpointName,
        "--host", $HostValue,
        "--port", [string]$PortNumber,
        "--mode", $ProjectionMode,
        "--limit", [string]$ProjectionLimit,
        "--poll-seconds", [string]$PollIntervalSeconds
    )
    $process = Start-Process `
        -FilePath $PythonCommand `
        -ArgumentList $arguments `
        -WorkingDirectory $Repository `
        -WindowStyle Hidden `
        -RedirectStandardOutput $stdoutPath `
        -RedirectStandardError $stderrPath `
        -PassThru

    return [pscustomobject]@{
        Process = $process
        StdoutLog = $stdoutPath
        StderrLog = $stderrPath
    }
}

$requestedPort = $Port
$actualPort = $requestedPort
$requestedBaseUrl = "http://$HostName`:$requestedPort"
$baseUrl = $requestedBaseUrl
$started = $false
$processId = $null
$stdoutLog = $null
$stderrLog = $null
$fallbackUsed = $false
$rejectedService = $null
$rejectedBaseUrl = $null
$preferredPortAcceptedTcp = $false
$requestedEndpoint = $Endpoint
$service = [pscustomobject]@{
    Healthy = $false
    Endpoint = $null
    Error = "Launcher did not run."
    GeneratedAt = $null
}

try {
    $repo = (Resolve-Path $RepoRoot).Path
    $pythonCommand = Resolve-PythonCommand
    $Endpoint = Resolve-WorkbenchEndpoint -PythonCommand $pythonCommand -Repository $repo -RequestedEndpoint $requestedEndpoint

    $service = Test-WorkbenchService -BaseUrl $baseUrl -ExpectedEndpoint $Endpoint -ProjectionMode $Mode
    if ($service.Healthy) {
        $preferredPortAcceptedTcp = $true
    }
    else {
        $preferredPortAcceptedTcp = Test-PortAcceptsTcpConnection -Name $HostName -PortNumber $requestedPort
    }
    if (-not $service.Healthy -and -not $NoStart) {
        $logDir = Join-Path $repo ".shujuan\logs"

        if ($preferredPortAcceptedTcp) {
            $rejectedService = $service
            $rejectedBaseUrl = $baseUrl
            $fallbackUsed = $true
            $healthyFallback = Find-HealthyFallbackService -Name $HostName -PreferredPort $requestedPort -ExpectedEndpoint $Endpoint -ProjectionMode $Mode
            if ($healthyFallback) {
                $actualPort = [int]$healthyFallback.Port
                $baseUrl = [string]$healthyFallback.BaseUrl
                $service = $healthyFallback.Service
            }
            else {
                $actualPort = Find-AvailablePort -Name $HostName -PreferredPort $requestedPort
                $baseUrl = "http://$HostName`:$actualPort"
            }
        }

        if (-not $service.Healthy) {
            $startedProcess = Start-WorkbenchProcess `
                -PythonCommand $pythonCommand `
                -Repository $repo `
                -EndpointName $Endpoint `
                -HostValue $HostName `
                -PortNumber $actualPort `
                -ProjectionMode $Mode `
                -ProjectionLimit $Limit `
                -PollIntervalSeconds $PollSeconds `
                -LogDirectory $logDir
            $started = $true
            $processId = $startedProcess.Process.Id
            $stdoutLog = $startedProcess.StdoutLog
            $stderrLog = $startedProcess.StderrLog
            $service = Wait-WorkbenchService -BaseUrl $baseUrl -ExpectedEndpoint $Endpoint -ProjectionMode $Mode -TimeoutSeconds $StartupTimeoutSeconds
        }
    }

    $workbenchUrl = "$baseUrl/workbench?mode=$([uri]::EscapeDataString($Mode))&limit=$Limit"
    $projectionUrl = "$baseUrl/api/projection?mode=$([uri]::EscapeDataString($Mode))&limit=$Limit"
    $reportedLastError = $service.Error
    if ($fallbackUsed -and $rejectedService) {
        $reportedLastError = $rejectedService.Error
    }

    $opened = $false
    if ($service.Healthy -and -not $NoOpen) {
        Start-Process $workbenchUrl
        $opened = $true
    }
}
catch {
    $workbenchUrl = "$baseUrl/workbench?mode=$([uri]::EscapeDataString($Mode))&limit=$Limit"
    $projectionUrl = "$baseUrl/api/projection?mode=$([uri]::EscapeDataString($Mode))&limit=$Limit"
    $reportedLastError = $_.Exception.Message
    $opened = $false
    $service = [pscustomobject]@{
        Healthy = $false
        Endpoint = $null
        Error = $_.Exception.Message
        GeneratedAt = $null
    }
}

$result = [pscustomobject]@{
    ok = [bool]$service.Healthy
    endpoint = $Endpoint
    requested_endpoint = $requestedEndpoint
    endpoint_auto_resolved = [bool](Test-AutomaticEndpointValue -Value $requestedEndpoint)
    service_endpoint = $service.Endpoint
    service_started = [bool]$started
    service_reused = [bool]((-not $started) -and $service.Healthy)
    process_id = $processId
    url = $workbenchUrl
    base_url = $baseUrl
    projection_endpoint = $projectionUrl
    mode = $Mode
    port = $actualPort
    requested_port = $requestedPort
    preferred_port = $requestedPort
    actual_port = $actualPort
    requested_base_url = $requestedBaseUrl
    fallback_used = [bool]$fallbackUsed
    preferred_port_accepts_tcp = [bool]$preferredPortAcceptedTcp
    rejected_base_url = $rejectedBaseUrl
    rejected_service_endpoint = if ($rejectedService) { $rejectedService.Endpoint } else { $null }
    rejected_service_last_error = if ($rejectedService) { $rejectedService.Error } else { $null }
    opened = $opened
    no_open = [bool]$NoOpen
    stdout_log = $stdoutLog
    stderr_log = $stderrLog
    last_error = $reportedLastError
    service_last_error = $service.Error
    generated_at = $service.GeneratedAt
}

if ($PassThru) {
    Write-Output ($result | ConvertTo-Json -Depth 5)
}
elseif ($service.Healthy) {
    Write-Host "shujuan live workbench is ready: $workbenchUrl"
}
else {
    Write-Error "shujuan live workbench is not reachable at $baseUrl. Last error: $($service.Error)"
}

if (-not $service.Healthy) {
    exit 2
}
