param([switch]$NoBrowser, [ValidateRange(1024, 65535)][int]$Port = 8876, [switch]$CheckRuntime)
$ErrorActionPreference = 'Stop'
$env:PYTHONIOENCODING = 'utf-8'
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
$sopRoot = $PSScriptRoot
$sopUrl = "http://127.0.0.1:$Port"
$sopRuntime = $env:SOP_PYTHON_PATH
if ($sopRuntime) {
    $sopRuntime = [Environment]::ExpandEnvironmentVariables($sopRuntime)
    if (-not [IO.Path]::IsPathRooted($sopRuntime)) { $sopRuntime = Join-Path $sopRoot $sopRuntime }
    if (-not (Test-Path -LiteralPath $sopRuntime -PathType Leaf)) {
        throw "SOP_PYTHON_PATH 指向的 Python 不存在：$sopRuntime"
    }
} else {
    foreach ($sopEnvironmentRoot in @($sopRoot, (Split-Path -Parent $sopRoot))) {
        $sopEnvironment = Join-Path $sopEnvironmentRoot '.venv'
        if (Test-Path -LiteralPath $sopEnvironment) {
            $sopCandidate = Join-Path $sopEnvironment 'Scripts\python.exe'
            if (-not (Test-Path -LiteralPath $sopCandidate -PathType Leaf)) { throw "Python 环境不完整：$sopEnvironment。请保留旧环境并在本机重新初始化。" }
            $sopRuntime = $sopCandidate
            break
        }
    }
    if (-not $sopRuntime) {
        foreach ($sopCommandName in @('python', 'python3')) {
            $sopPython = Get-Command $sopCommandName -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1
            if ($sopPython) { $sopRuntime = $sopPython.Source; break }
        }
    }
    if (-not $sopRuntime) { throw '找不到 Python。请创建项目或工作区的 .venv，或设置 SOP_PYTHON_PATH，并安装 requirements.txt。' }
}
$sopProbe = "import json,sys; sys.version_info >= (3,10) or sys.exit('Python 3.10+ is required'); import openpyxl; print(json.dumps({'executable':sys.executable},ensure_ascii=True))"
try {
    $sopProbeOutput = & $sopRuntime -X utf8 -c $sopProbe 2>&1
    if ($LASTEXITCODE -ne 0) { throw ($sopProbeOutput | Out-String) }
    $sopRuntime = ($sopProbeOutput | ConvertFrom-Json).executable
    if (-not $sopRuntime) { throw 'Python did not report its executable.' }
} catch {
    throw "Python 不可用或缺少依赖：$sopRuntime。请使用该解释器安装 requirements.txt。$($_.Exception.Message)"
}
if ($CheckRuntime) { Write-Output $sopRuntime; exit 0 }
$env:SOP_PYTHON_PATH = $sopRuntime
$Host.UI.RawUI.WindowTitle = 'SOP计划工作台'
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
