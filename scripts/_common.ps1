# Shared helpers for VME scripts.
$ErrorActionPreference = "Stop"

function Get-VmeRoot {
    return (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
}

function Refresh-Path {
    $machine = [Environment]::GetEnvironmentVariable("Path", "Machine")
    $user = [Environment]::GetEnvironmentVariable("Path", "User")
    $env:Path = "$machine;$user"
}

function Clear-PythonHome {
    if (Test-Path Env:PYTHONHOME) { Remove-Item Env:PYTHONHOME -ErrorAction SilentlyContinue }
}

function Test-Admin {
    $current = New-Object Security.Principal.WindowsPrincipal([Security.Principal.WindowsIdentity]::GetCurrent())
    return $current.IsInRole([Security.Principal.WindowsBuiltinRole]::Administrator)
}

function Get-VenvPython {
    $root = Get-VmeRoot
    return (Join-Path $root "backend\.venv\Scripts\python.exe")
}

function Get-SecretsFile {
    return (Join-Path (Get-VmeRoot) "secrets.env")
}

# Parse <root>/secrets.env (KEY=VALUE per line, # = comment) into a hashtable.
function Read-SecretsFile {
    $path = Get-SecretsFile
    $map = @{}
    if (-not (Test-Path $path)) { return $map }
    foreach ($raw in (Get-Content -LiteralPath $path -Encoding UTF8)) {
        $line = $raw.Trim()
        if (-not $line -or $line.StartsWith("#")) { continue }
        $eq = $line.IndexOf("=")
        if ($eq -lt 1) { continue }
        $key = $line.Substring(0, $eq).Trim()
        $val = $line.Substring($eq + 1).Trim()
        if ($val.StartsWith('"') -and $val.EndsWith('"') -and $val.Length -ge 2) {
            $val = $val.Substring(1, $val.Length - 2)
        }
        $map[$key] = $val
    }
    return $map
}

# Apply secrets.env to the current process's environment. Existing env vars take
# precedence so callers can override (e.g. set VME_HTTPS_PORT=8443 then run serve.ps1).
function Load-VmeEnv {
    foreach ($entry in (Read-SecretsFile).GetEnumerator()) {
        if (-not [Environment]::GetEnvironmentVariable($entry.Key, "Process")) {
            [Environment]::SetEnvironmentVariable($entry.Key, $entry.Value, "Process")
        }
    }
}

# Atomic write to secrets.env. Preserves keys not in $updates.
function Write-SecretsFile([hashtable]$updates) {
    $path = Get-SecretsFile
    $existing = Read-SecretsFile
    foreach ($key in $updates.Keys) { $existing[$key] = $updates[$key] }
    $lines = @(
        "# VME secrets - DO NOT COMMIT.",
        "# Loaded by scripts/serve.ps1 and used by the Windows service."
    )
    foreach ($key in ($existing.Keys | Sort-Object)) {
        $value = $existing[$key]
        $lines += "$key=$value"
    }
    [IO.File]::WriteAllLines($path, $lines, (New-Object Text.UTF8Encoding($false)))
    # Restrict to current user (best-effort; falls back silently on FAT/network paths).
    try {
        $acl = Get-Acl -LiteralPath $path
        $acl.SetAccessRuleProtection($true, $false)
        $sid = ([Security.Principal.WindowsIdentity]::GetCurrent()).User
        $rule = New-Object Security.AccessControl.FileSystemAccessRule(
            $sid, "FullControl", "Allow")
        $acl.Access | ForEach-Object { [void]$acl.RemoveAccessRule($_) }
        [void]$acl.AddAccessRule($rule)
        Set-Acl -LiteralPath $path -AclObject $acl
    } catch {
        Write-Verbose "Could not tighten ACL on ${path}: $_"
    }
}
