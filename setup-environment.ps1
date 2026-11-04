param([string]$PythonPath)
$ErrorActionPreference = 'Stop'
$env:PYTHONIOENCODING = 'utf-8'
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
$sopRoot = $PSScriptRoot
$sopEnvironment = Join-Path $sopRoot '.venv'
$sopEnvironmentPython = Join-Path $sopEnvironment 'Scripts\python.exe'
$sopBaseArguments = @()
if ($PythonPath) {
    if (-not [IO.Path]::IsPathRooted($PythonPath)) { $PythonPath = Join-Path $sopRoot $PythonPath }
    if (-not (Test-Path -LiteralPath $PythonPath -PathType Leaf)) { throw "Python 不存在：$PythonPath" }
    $sopBasePython = $PythonPath
} else {
    $sopBaseCommand = Get-Command python -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1
    if (-not $sopBaseCommand) {
        $sopBaseCommand = Get-Command py -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1
        $sopBaseArguments = @('-3')
    }
    if (-not $sopBaseCommand) { throw '请先安装 Python 3.10+ 并启用 Python Launcher 或添加到 PATH，再运行初始化。也可用 -PythonPath 指定解释器。' }
    $sopBasePython = $sopBaseCommand.Source
}
$sopProbe = "import json,sys; sys.version_info >= (3,10) or sys.exit('Python 3.10+ is required'); print(json.dumps({'executable':sys.executable},ensure_ascii=True))"
$sopBaseInfo = & $sopBasePython @sopBaseArguments -X utf8 -c $sopProbe
if ($LASTEXITCODE -ne 0) { throw '初始化需要可运行的 Python 3.10+。' }
$sopBasePython = ($sopBaseInfo | ConvertFrom-Json).executable
if (-not (Test-Path -LiteralPath $sopEnvironment)) {
    & $sopBasePython -m venv $sopEnvironment
    if ($LASTEXITCODE -ne 0) { throw '创建 .venv 失败。请检查 Python 的 venv/ensurepip 组件和项目目录权限。' }
}
if (-not (Test-Path -LiteralPath $sopEnvironmentPython -PathType Leaf)) {
    throw '现有 .venv 不完整。请将旧 .venv 移到项目外保留，再运行初始化；不要直接复制其他电脑的环境。'
}
try {
    & $sopEnvironmentPython -X utf8 -c $sopProbe | Out-Null
    if ($LASTEXITCODE -ne 0) { throw 'Python validation failed.' }
} catch {
    throw '现有 .venv 无法运行。请将旧 .venv 移到项目外保留，再运行初始化，在本机重新创建环境。'
}
& $sopEnvironmentPython -m pip install -r (Join-Path $sopRoot 'requirements.txt')
if ($LASTEXITCODE -ne 0) { throw '依赖安装失败。请检查包源/网络，或使用本机离线依赖包，然后重新运行初始化。' }
& $sopEnvironmentPython -X utf8 -c 'import openpyxl,sys; print(sys.executable)'
if ($LASTEXITCODE -ne 0) { throw '环境检查失败。' }
Write-Host '环境已准备好，可以双击“启动工作台.cmd”。'
