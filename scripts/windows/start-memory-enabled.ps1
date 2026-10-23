param(
    [switch]$ArmDemo,
    [switch]$ServiceOnly
)

$ErrorActionPreference = 'Stop'
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
$legacyRoot = Join-Path $repoRoot 'SenseVoice'
$runtimePath = Join-Path $repoRoot 'data\memory_runtime.json'
$envPath = Join-Path $legacyRoot '.env.local'
$python = Join-Path $legacyRoot '.venv\Scripts\python.exe'
$pythonw = Join-Path $legacyRoot '.venv\Scripts\pythonw.exe'
$dashboard = Join-Path $legacyRoot 'dashboard.py'

if (-not (Test-Path -LiteralPath $runtimePath)) { throw 'Local memory runtime settings are missing.' }
if (-not (Test-Path -LiteralPath $envPath)) { throw 'Local API keys are missing.' }
$settings = Get-Content -LiteralPath $runtimePath -Raw | ConvertFrom-Json
if (-not $settings.enabled) { throw 'Local memory runtime is disabled.' }

function Read-LocalKey([string]$name) {
    $entry = Get-Content -LiteralPath $envPath | Where-Object {
        $_.Split('=', 2)[0].Trim() -eq $name
    } | Select-Object -Last 1
    if (-not $entry) { throw "Missing local setting: $name" }
    return $entry.Split('=', 2)[1].Trim().Trim('"').Trim("'")
}

$env:PYTHONPATH = "$(Join-Path $repoRoot 'src');$legacyRoot"
$env:PYTHONUNBUFFERED = '1'
$env:ZHUANGFY_MEMORY_TOKEN = Read-LocalKey 'ZHUANGFY_MEMORY_TOKEN'
$env:ZHUANGFY_MEMORY_ADMIN_TOKEN = Read-LocalKey 'ZHUANGFY_MEMORY_ADMIN_TOKEN'
$env:ZHUANGFY_MEMORY_ADMIN_UI = '1'
$env:ZHUANGFY_MEMORY_QQ = if ($settings.qq_enabled) { '1' } else { '0' }
$env:ZHUANGFY_MEMORY_DESKTOP = if ($settings.desktop_enabled) { '1' } else { '0' }
$env:ZHUANGFY_MEMORY_DESKTOP_USER_ID = [string]$settings.desktop_user_id

$listener = Get-NetTCPConnection -LocalPort 8769 -State Listen -ErrorAction SilentlyContinue
if (-not $listener) {
    $env:ZHUANGFY_MEMORY_LLM_API_KEY = Read-LocalKey 'SILICONFLOW_API_KEY'
    $env:ZHUANGFY_MEMORY_LLM_BASE_URL = [string]$settings.base_url
    $env:ZHUANGFY_MEMORY_LLM_MODEL = [string]$settings.model
    $env:ZHUANGFY_MEMORY_REFLECT = if ($settings.reflect_enabled) { '1' } else { '0' }
    $env:ZHUANGFY_MEMORY_ALLOW_REMOTE = if ($settings.reflect_enabled) { '1' } else { '0' }
    Start-Process -FilePath $python -ArgumentList '-m', 'zhuangfy.memory.http_server' `
        -WorkingDirectory $repoRoot -WindowStyle Hidden `
        -RedirectStandardOutput (Join-Path $repoRoot 'data\memory-service.out.log') `
        -RedirectStandardError (Join-Path $repoRoot 'data\memory-service.err.log') | Out-Null
    for ($attempt = 0; $attempt -lt 60; $attempt++) {
        Start-Sleep -Milliseconds 500
        $listener = Get-NetTCPConnection -LocalPort 8769 -State Listen -ErrorAction SilentlyContinue
        if ($listener) { break }
    }
    if (-not $listener) { throw 'Memory Service did not open its loopback port; inspect data/memory-service.err.log.' }
}

Remove-Item Env:ZHUANGFY_MEMORY_LLM_API_KEY -ErrorAction SilentlyContinue
Remove-Item Env:ZHUANGFY_MEMORY_LLM_BASE_URL -ErrorAction SilentlyContinue
Remove-Item Env:ZHUANGFY_MEMORY_LLM_MODEL -ErrorAction SilentlyContinue
Remove-Item Env:ZHUANGFY_MEMORY_REFLECT -ErrorAction SilentlyContinue
Remove-Item Env:ZHUANGFY_MEMORY_ALLOW_REMOTE -ErrorAction SilentlyContinue

# A different process on the port must not be mistaken for our memory server.
& $python -c "import os; from zhuangfy.memory.client import MemoryClient; MemoryClient(token=os.environ['ZHUANGFY_MEMORY_ADMIN_TOKEN']).list_people()"
if ($LASTEXITCODE -ne 0) { throw 'Memory Service authentication failed.' }

$autonomyPolicy = Join-Path $legacyRoot 'autonomy_settings.json'
if (Test-Path -LiteralPath $autonomyPolicy) {
    $autonomySettings = Get-Content -LiteralPath $autonomyPolicy -Raw | ConvertFrom-Json
    if ($autonomySettings.enabled) {
        $autonomyModule = 'zhuangfy.agent_v2.autonomy_cli'
        $runningAutonomy = Get-CimInstance Win32_Process | Where-Object {
            $_.CommandLine -like "*$autonomyModule*" -and $_.CommandLine -like '* serve*'
        }
        if (-not $runningAutonomy) {
            Start-Process -FilePath $python `
                -ArgumentList '-m', $autonomyModule, '--database',
                    (Join-Path $repoRoot 'data\agent-v2-background.db'),
                    '--policy', $autonomyPolicy, 'serve' `
                -WorkingDirectory $repoRoot -WindowStyle Hidden `
                -RedirectStandardOutput (Join-Path $repoRoot 'data\agent-v2-background.out.log') `
                -RedirectStandardError (Join-Path $repoRoot 'data\agent-v2-background.err.log') | Out-Null
        }
    }
}

if ($ServiceOnly) {
    Write-Output 'Memory Service is running.'
    return
}

$existing = Get-CimInstance Win32_Process | Where-Object {
    $_.Name -eq 'pythonw.exe' -and $_.CommandLine -like "*$dashboard*"
}
if ($existing) { throw 'Dashboard is already running; exit it from the tray before starting another instance.' }

$arguments = @('"' + $dashboard + '"', '--start-services')
if ($ArmDemo) { $arguments += '--arm-demo' }
Start-Process -FilePath $pythonw -ArgumentList $arguments -WorkingDirectory $legacyRoot | Out-Null
Write-Output 'Memory Service and ZhuangFY Dashboard are running.'
