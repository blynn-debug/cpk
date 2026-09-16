# 로컬(노트북)에서 실행: 브라우저 Cookie 헤더를 서버 쿠키통으로 밀어 넣고 검증한다.
#
# 준비: Chrome에서 coupang.com 아무 페이지 열기 → F12 → Network → 문서 요청 클릭 →
#       Request Headers의 cookie 값 전체를 복사(클립보드).
# 실행: powershell -ExecutionPolicy Bypass -File push_cookie.ps1            (클립보드에서)
#       powershell -ExecutionPolicy Bypass -File push_cookie.ps1 cookie.txt (파일에서)
param([string]$File = "", [string]$Target = "mini")
$env:CPK_HOST = $Target

$ErrorActionPreference = "Stop"
$tmp = Join-Path $env:TEMP ("cpk_cookie_" + [guid]::NewGuid().ToString("N") + ".txt")
try {
    if ($File -ne "") {
        $raw = Get-Content -Path $File -Raw -Encoding utf8
    } else {
        $raw = Get-Clipboard -Raw
    }
    $raw = $raw.Trim()
    if ($raw -notmatch "_abck=" -or $raw -notmatch "bm_sz=") {
        throw "클립보드/파일에 아카마이 쿠키(_abck, bm_sz)가 없습니다. Cookie 헤더 전체를 복사했는지 확인하세요."
    }
    [IO.File]::WriteAllText($tmp, $raw, (New-Object Text.UTF8Encoding($false)))
    scp -o BatchMode=yes $tmp "${env:CPK_HOST}:~/cpk/state/incoming_cookie.txt"
    if ($LASTEXITCODE -ne 0) { throw "scp 실패 (보안 그룹의 SSH IP 규칙 확인)" }
    ssh -o BatchMode=yes $env:CPK_HOST 'cd ~/cpk && set -a && . ./cpk.env && set +a && export PYTHONIOENCODING=utf-8 CPK_HOME=$HOME/cpk/state && .venv/bin/python cpk_import.py state/incoming_cookie.txt; rc=$?; rm -f state/incoming_cookie.txt; exit $rc'
    if ($LASTEXITCODE -ne 0) { throw "서버에서 쿠키 검증 실패 (쿠키가 이미 만료됐거나 서버 IP가 거부됨)" }
    Write-Host "OK: 서버 쿠키 세션 갱신 완료"
} finally {
    if (Test-Path $tmp) { Remove-Item $tmp -Force }
}
