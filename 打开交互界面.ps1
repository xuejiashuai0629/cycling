param(
    [string]$PythonPath = ''
)

$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot

$url = 'http://127.0.0.1:8501'

function Test-Url([string]$target) {
    try {
        $response = Invoke-WebRequest -Uri $target -UseBasicParsing -TimeoutSec 1
        return $response.StatusCode -ge 200 -and $response.StatusCode -lt 500
    }
    catch {
        return $false
    }
}

if (Test-Url $url) {
    Start-Process $url
    Write-Host '网站已经在运行，已为你打开浏览器。'
    exit 0
}

$python = $null
if ($PythonPath) {
    $python = Get-Item -LiteralPath $PythonPath -ErrorAction SilentlyContinue
}
else {
    $python = Get-Command python -ErrorAction SilentlyContinue
    if (-not $python) {
        $python = Get-Command py -ErrorAction SilentlyContinue
    }
}

if (-not $python) {
    Write-Host '未找到 python。请先安装 Python，或用 -PythonPath 指定 python.exe 位置。'
    Read-Host '按回车退出'
    exit 1
}

if ($python.PSIsContainer) {
    $pythonPath = Join-Path $python.FullName 'python.exe'
}
else {
    $pythonPath = $python.Source
}
if (-not (Test-Path -LiteralPath $pythonPath)) {
    Write-Host 'Python 可执行文件不存在。'
    Read-Host '按回车退出'
    exit 1
}

Write-Host '正在启动交互界面…'
$process = Start-Process -FilePath $pythonPath -ArgumentList @('-m', 'streamlit', 'run', 'app.py', '--server.headless', 'true') -WorkingDirectory $PSScriptRoot -PassThru

try {
    for ($i = 0; $i -lt 80; $i++) {
        Start-Sleep -Milliseconds 250
        if (Test-Url $url) {
            Start-Process $url
            break
        }
        if ($process.HasExited) {
            break
        }
    }

    if (-not (Test-Url $url)) {
        Write-Host '网站启动失败，请把上面的错误信息发给我。'
        Read-Host '按回车退出'
        exit 1
    }

    Write-Host '已打开：' + $url
    Write-Host '保持这个窗口开启。按 Ctrl+C 停止服务。'
    Wait-Process -Id $process.Id
}
finally {
    if (-not $process.HasExited) {
        Stop-Process -Id $process.Id -Force
    }
}
