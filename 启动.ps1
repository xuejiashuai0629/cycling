param(
    [string]$PythonPath = '',
    [switch]$CheckOnly,
    [switch]$Test,
    [switch]$CheckIma,
    [switch]$CheckModel
)
$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot

# Discover existing installations; never install or change versions here.
$candidates = New-Object System.Collections.Generic.List[string]
if ($PythonPath) {
    $candidates.Add($PythonPath)
} else {
    foreach ($name in @('python3.15', 'python')) {
        $command = Get-Command $name -ErrorAction SilentlyContinue
        if ($command) { $candidates.Add($command.Source) }
    }
    if (Get-Command py -ErrorAction SilentlyContinue) {
        try {
            $registered = & py -3.15 -c 'import sys; print(sys.executable)' 2>$null
            if ($LASTEXITCODE -eq 0 -and $registered) { $candidates.Add([string]$registered) }
        } catch {
            # Unregistered portable installations are checked below.
        }
    }
    foreach ($registry in @('HKCU:\Software\Python\PythonCore\3.15\InstallPath', 'HKLM:\Software\Python\PythonCore\3.15\InstallPath')) {
        if (Test-Path -LiteralPath $registry) {
            $install = (Get-Item -LiteralPath $registry).GetValue('')
            if ($install) { $candidates.Add((Join-Path $install 'python.exe')) }
        }
    }
    foreach ($drive in Get-PSDrive -PSProvider FileSystem) {
        foreach ($folder in @('python', 'Python315')) {
            $candidate = Join-Path $drive.Root ($folder + '\python.exe')
            if (Test-Path -LiteralPath $candidate) { $candidates.Add($candidate) }
        }
    }
}
$selected = $null
foreach ($candidate in ($candidates | Select-Object -Unique)) {
    if (-not (Test-Path -LiteralPath $candidate)) { continue }
    $version = & $candidate -c 'import sys; print(sys.version.split()[0])' 2>$null
    if ($LASTEXITCODE -eq 0 -and $version -eq '3.15.0') {
        $selected = $candidate
        break
    }
}
if (-not $selected) {
    Write-Host '未找到 Python 3.15.0。请使用：.\启动.ps1 -PythonPath "你的python.exe完整位置"'
    exit 1
}
$streamlitVersion = & $selected -c 'import streamlit; print(streamlit.__version__)' 2>$null
if ($LASTEXITCODE -ne 0 -or $streamlitVersion -ne '1.66.0') {
    Write-Host '所选 Python 中缺少 Streamlit 1.66.0，请先统一环境；本脚本不会自动修改版本。'
    exit 1
}
Write-Host "环境检查通过：Python 3.15.0 / Streamlit 1.66.0"
if ($CheckOnly) { exit 0 }
if ($Test) {
    & $selected -m unittest discover -s tests -v
    exit $LASTEXITCODE
}
if ($CheckIma) {
    & $selected -m tests.live_ima_smoke
    exit $LASTEXITCODE
}
if ($CheckModel) {
    & $selected -m tests.live_deepseek_smoke
    exit $LASTEXITCODE
}
Write-Host '启动后打开 http://127.0.0.1:8501 ；保持此窗口开启，按 Ctrl+C 停止。'
& $selected -m streamlit run (Join-Path $PSScriptRoot 'app.py')
exit $LASTEXITCODE
