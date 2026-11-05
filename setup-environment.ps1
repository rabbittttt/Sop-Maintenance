param([string]$PythonPath)
$ErrorActionPreference = 'Stop'
$env:PYTHONIOENCODING = 'utf-8'
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
$sopRoot = $PSScriptRoot
$sopWorkspace = Get-Item -LiteralPath $sopRoot
while ($sopWorkspace -and $sopWorkspace.Name -ine 'Project') { $sopWorkspace = $sopWorkspace.Parent }
if (-not $sopWorkspace) { throw '找不到 Project 工作区。请将 SOP 项目放在 Project 目录下，再启动工作台。' }
$sopEnvironment = Join-Path $sopWorkspace.FullName '.venv'
$sopPythonSuffix = if ([Environment]::OSVersion.Platform -eq [PlatformID]::Win32NT) { 'Scripts\python.exe' } else { 'bin/python' }
$sopEnvironmentPython = Join-Path $sopEnvironment $sopPythonSuffix
if ($PythonPath) {
    if (-not [IO.Path]::IsPathRooted($PythonPath)) { $PythonPath = Join-Path $sopRoot $PythonPath }
    if (-not (Test-Path -LiteralPath $PythonPath -PathType Leaf)) { throw "Python 不存在：$PythonPath" }
}
$sopProbe = "import json,sys; sys.version_info >= (3,11) or sys.exit('Project shared environment requires Python 3.11+'); print(json.dumps({'executable':sys.executable,'prefix':sys.prefix},ensure_ascii=True))"
if (-not (Test-Path -LiteralPath $sopEnvironment)) {
    $sopBaseArguments = @()
    if ($PythonPath) {
        $sopBasePython = $PythonPath
    } else {
        $sopBaseCommand = Get-Command python -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1
        if (-not $sopBaseCommand) { $sopBaseCommand = Get-Command python3 -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1 }
        if (-not $sopBaseCommand) {
            $sopBaseCommand = Get-Command py -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1
            $sopBaseArguments = @('-3')
        }
        if (-not $sopBaseCommand) { throw '请先安装 Python 3.11+ 并启用 Python Launcher 或添加到 PATH，再双击启动工作台。也可用 -PythonPath 指定基础解释器。' }
        $sopBasePython = $sopBaseCommand.Source
    }
    $sopBaseInfo = & $sopBasePython @sopBaseArguments -X utf8 -c $sopProbe
    if ($LASTEXITCODE -ne 0) { throw 'Project 共享环境初始化需要可运行的 Python 3.11+。' }
    $sopBasePython = ($sopBaseInfo | ConvertFrom-Json).executable
    & $sopBasePython -m venv $sopEnvironment
    if ($LASTEXITCODE -ne 0) { throw '创建 .venv 失败。请检查 Python 的 venv/ensurepip 组件和项目目录权限。' }
}
if (-not (Test-Path -LiteralPath $sopEnvironmentPython -PathType Leaf)) {
    throw '现有 .venv 不完整。请将旧 .venv 移到项目外保留，再双击启动工作台；不要直接复制其他电脑的环境。'
}
try {
    $sopEnvironmentInfo = & $sopEnvironmentPython -X utf8 -c $sopProbe
    if ($LASTEXITCODE -ne 0) { throw 'Python validation failed.' }
    $sopEnvironmentInfo = $sopEnvironmentInfo | ConvertFrom-Json
    if ([IO.Path]::GetFullPath($sopEnvironmentInfo.prefix).TrimEnd('\', '/') -ine [IO.Path]::GetFullPath($sopEnvironment).TrimEnd('\', '/')) {
        throw 'Python did not activate the shared environment.'
    }
} catch {
    throw '现有 .venv 无法运行。请将旧 .venv 移到项目外保留，再双击启动工作台，在本机重新创建环境。'
}
$env:PATH = (Split-Path -Parent $sopEnvironmentPython) + [IO.Path]::PathSeparator + $env:PATH
$env:VIRTUAL_ENV = $sopEnvironment
# An existing shared environment may have been created without pip.
& $sopEnvironmentPython -X utf8 -c "import importlib.util,sys; sys.exit(0 if importlib.util.find_spec('pip') else 1)"
if ($LASTEXITCODE -ne 0) {
    & $sopEnvironmentPython -m ensurepip --upgrade
    if ($LASTEXITCODE -ne 0) { throw '共享环境缺少 pip 且无法自动准备，请检查 Python 的 ensurepip 组件。' }
}
& $sopEnvironmentPython -m pip install -r (Join-Path $sopRoot 'requirements.txt')
if ($LASTEXITCODE -ne 0) { throw '依赖安装失败。请检查包源/网络，或准备本机离线依赖包，再双击启动工作台重试。' }
& $sopEnvironmentPython -X utf8 -c 'import openpyxl'
if ($LASTEXITCODE -ne 0) { throw '环境检查失败，未启动工作台。' }
Write-Host 'Project 共享环境已准备好。'
