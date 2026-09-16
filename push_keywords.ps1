# 로컬 keywords.txt 를 맥미니로 올린다. 집 와이파이면 mini, 아니면 aws103 경유 mini-remote 로 자동 선택.
# 실행: powershell -ExecutionPolicy Bypass -File push_keywords.ps1 [-Target mini|mini-remote]
param([string]$Target = "")
$ErrorActionPreference = "Stop"
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$file = Join-Path $here "keywords.txt"
if ($Target -eq "") {
    ssh -o BatchMode=yes -o ConnectTimeout=3 mini true 2>$null
    $Target = if ($LASTEXITCODE -eq 0) { "mini" } else { "mini-remote" }
}
scp -o BatchMode=yes $file "${Target}:~/cpk/keywords.txt"
if ($LASTEXITCODE -ne 0) { throw "copy failed ($Target)" }
$n = ssh -o BatchMode=yes $Target "grep -cv '^\s*#\|^\s*$' ~/cpk/keywords.txt"
Write-Output "OK via $Target : $n keywords"
