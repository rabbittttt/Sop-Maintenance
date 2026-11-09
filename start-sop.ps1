param([switch]$NoBrowser, [ValidateRange(1024, 65535)][int]$Port = 8876, [switch]$CheckRuntime)
$ErrorActionPreference = 'Stop'
$env:PYTHONIOENCODING = 'utf-8'
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
$sopRoot = $PSScriptRoot
$sopUrl = "http://127.0.0.1:$Port"
$sopRuntime = $env:SOP_PYTHON_PATH
$sopExplicitRuntime = [bool]$sopRuntime
if ($sopExplicitRuntime) {
    $sopRuntime = [Environment]::ExpandEnvironmentVariables($sopRuntime)
    if (-not [IO.Path]::IsPathRooted($sopRuntime)) { $sopRuntime = Join-Path $sopRoot $sopRuntime }
    if (-not (Test-Path -LiteralPath $sopRuntime -PathType Leaf)) {
        throw "SOP_PYTHON_PATH 指向的 Python 不存在：$sopRuntime"
    }
} else {
    $sopWorkspace = Get-Item -LiteralPath $sopRoot
    while ($sopWorkspace -and $sopWorkspace.Name -ine 'Project') { $sopWorkspace = $sopWorkspace.Parent }
    if (-not $sopWorkspace) { throw '找不到 Project 工作区。请将项目放在 Project 目录下，再启动工作台。' }
    $sopPythonSuffix = if ([Environment]::OSVersion.Platform -eq [PlatformID]::Win32NT) { '.venv\Scripts\python.exe' } else { '.venv/bin/python' }
    $sopRuntime = Join-Path $sopWorkspace.FullName $sopPythonSuffix
}

$sopExpectedPrefix = if ($sopExplicitRuntime) { $null } else { Split-Path -Parent (Split-Path -Parent $sopRuntime) }

function Get-SopRuntimeState([string]$Python, [string]$ExpectedPrefix) {
    if (-not (Test-Path -LiteralPath $Python -PathType Leaf)) {
        return @{ Ready = $false; Details = "Project 共享 Python 不存在：$Python" }
    }
    $probe = "import json,sys; sys.version_info >= (3,10) or sys.exit('Python 3.10+ is required'); import openpyxl; print(json.dumps({'executable':sys.executable,'prefix':sys.prefix},ensure_ascii=True))"
    $previousPreference = $ErrorActionPreference
    try {
        # Windows PowerShell 5.1 turns native stderr into ErrorRecord objects.
        # Capture the probe failure so a missing package can reach initialization.
        $ErrorActionPreference = 'Continue'
        $output = @(& $Python -X utf8 -c $probe 2>&1)
        $exitCode = $LASTEXITCODE
        if ($exitCode -ne 0) { return @{ Ready = $false; Details = ($output | Out-String).Trim() } }
        $info = ($output | Out-String) | ConvertFrom-Json -ErrorAction Stop
        if (-not $info.executable) { throw 'Python did not report its executable.' }
        if ($ExpectedPrefix -and [IO.Path]::GetFullPath($info.prefix).TrimEnd('\', '/') -ine [IO.Path]::GetFullPath($ExpectedPrefix).TrimEnd('\', '/')) {
            return @{ Ready = $false; Details = '共享 Python 未使用 Project/.venv，请保留旧环境并在本机重建。' }
        }
        return @{ Ready = $true; Executable = $info.executable }
    } catch {
        return @{ Ready = $false; Details = $_.Exception.Message }
    } finally {
        $ErrorActionPreference = $previousPreference
    }
}

$sopRuntimeState = Get-SopRuntimeState $sopRuntime $sopExpectedPrefix
if (-not $sopRuntimeState.Ready) {
    if ($CheckRuntime -or $sopExplicitRuntime) {
        throw "Python 不可用或缺少依赖：$sopRuntime。请使用该解释器安装 requirements.txt。$($sopRuntimeState.Details)"
    }
    Write-Host '正在准备 Project 共享 Python 环境和 SOP 依赖，首次启动可能需要几分钟…'
    & (Join-Path $sopRoot 'setup-environment.ps1')
    $sopRuntimeState = Get-SopRuntimeState $sopRuntime $sopExpectedPrefix
    if (-not $sopRuntimeState.Ready) {
        throw "环境初始化后检查仍未通过，未启动工作台：$($sopRuntimeState.Details)"
    }
}
$sopRuntime = $sopRuntimeState.Executable
if ($CheckRuntime) { Write-Output $sopRuntime; exit 0 }
$env:SOP_PYTHON_PATH = $sopRuntime
$sopPythonDirectory = Split-Path -Parent $sopRuntime
$env:PATH = $sopPythonDirectory + [IO.Path]::PathSeparator + $env:PATH
$sopSelectedEnvironment = Split-Path -Parent $sopPythonDirectory
if (Test-Path -LiteralPath (Join-Path $sopSelectedEnvironment 'pyvenv.cfg') -PathType Leaf) {
    $env:VIRTUAL_ENV = $sopSelectedEnvironment
} else {
    Remove-Item Env:VIRTUAL_ENV -ErrorAction SilentlyContinue
}
$Host.UI.RawUI.WindowTitle = 'SOP计划管理'
$sopServer = Join-Path $sopRoot 'server.py'
Write-Host "使用 Python：$sopRuntime"

# One-time handover from the former hidden service. Never stop a process on
# port number alone: the health endpoint, persisted identity and listener
# must all belong to this workspace before ending that legacy process.
$sopHealth = $null
try { $sopHealth = Invoke-RestMethod -Uri "$sopUrl/api/health" -TimeoutSec 2 } catch { }
if ($sopHealth -and $sopHealth.app_id -eq 'hima-sop' -and $sopHealth.workspace -eq $sopRoot -and $sopHealth.launch_mode -ne 'console') {
    $sopMetaPath = Join-Path $sopRoot 'data\service.json'
    if (-not (Test-Path -LiteralPath $sopMetaPath)) {
        throw '检测到旧版后台看板，但缺少可信的启动记录。请先退出该旧进程，再启动工作台。'
    }
    $sopMeta = Get-Content -LiteralPath $sopMetaPath -Raw -Encoding UTF8 | ConvertFrom-Json
    if ($sopMeta.workspace -ne $sopRoot -or [int]$sopMeta.port -ne $Port) { throw '旧版启动记录与当前工作台不匹配，未停止任何进程。' }
    $sopLegacyPid = [int]$sopMeta.pid
    $sopProcess = Get-Process -Id $sopLegacyPid -ErrorAction Stop
    $sopStarted = [DateTime]::Parse($sopMeta.started_at)
    $sopCommand = Get-CimInstance Win32_Process -Filter "ProcessId = $sopLegacyPid"
    $sopListeners = @(Get-NetTCPConnection -State Listen -LocalPort $Port -ErrorAction Stop | Where-Object { $_.OwningProcess -eq $sopLegacyPid })
    if ($sopProcess.ProcessName -ne 'python' -or $sopProcess.Path -ne $sopMeta.executable -or
        [Math]::Abs(($sopProcess.StartTime - $sopStarted).TotalSeconds) -gt 2 -or
        $sopCommand.CommandLine -notmatch [regex]::Escape($sopServer) -or $sopListeners.Count -eq 0) {
        throw '旧版进程身份核对未通过，未停止任何进程。'
    }
    Write-Host '正在将本项目的旧版后台看板切换为前台工作台…'
    Stop-Process -Id $sopLegacyPid -Force -ErrorAction Stop
    if (-not $sopProcess.WaitForExit(10000)) { throw '旧版进程仍未退出，请稍后重试。' }
    Remove-Item -LiteralPath $sopMetaPath -Force
    $sopPidPath = Join-Path $sopRoot 'data\service.pid'
    if (Test-Path -LiteralPath $sopPidPath) { Remove-Item -LiteralPath $sopPidPath -Force }
}

# Run in this console. No detached or hidden Python service is created.
$sopArguments = @('-u', $sopServer, '--port', $Port)
if (-not $NoBrowser) { $sopArguments += '--open-browser' }
& $sopRuntime @sopArguments
exit $LASTEXITCODE
