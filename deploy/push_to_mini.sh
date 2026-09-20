#!/bin/sh
# cpk 소스를 맥미니(~/cpk)로 배포한다. 비밀·상태(cpk.env·state·data·.venv·.git)는 절대 보내지 않는다.
# 대상은 집 랜(mini)이면 mini, 아니면 aws103 경유(mini-remote)로 자동 선택.
# 실행: sh deploy/push_to_mini.sh   (레포 루트에서)
#
# 웹(web/)은 Railway 가 배포하므로 여기서 보내지 않는다. 맥미니 크롤러 코드만 배포한다.
set -eu

# 대상 선택
if ssh -o BatchMode=yes -o ConnectTimeout=4 mini true 2>/dev/null; then TARGET=mini
elif ssh -o BatchMode=yes -o ConnectTimeout=15 mini-remote true 2>/dev/null; then TARGET=mini-remote
else echo "맥미니에 접속할 수 없음(mini/mini-remote 모두 실패)"; exit 1
fi
echo "대상: $TARGET"

# 보낼 파일(크롤러 코드 + 배포 스크립트 + 테스트 + 예시 env). 비밀·상태 제외.
FILES="cpk_browser.py cpk_collect.py cpk_import.py cpk_keepalive.py cpk_keywords.py cpk_queue.py cpk_search.py cpk_session.py cpk_search_json.py cpk_domeggook.py cpk_market.py cpk_market_db.py cpk_market_score.py cpk_market_collect.py cpk.env.example requirements.txt"

# 파일 전송
scp -o BatchMode=yes $FILES "$TARGET:~/cpk/"
scp -o BatchMode=yes deploy/cpk_ssh_search.sh deploy/install_mac.sh deploy/disable_scheduled.sh "$TARGET:~/cpk/deploy/"
scp -o BatchMode=yes tests/*.py "$TARGET:~/cpk/tests/"

# 원격 마무리: 실행권한 + 의존성 갱신(있으면)
ssh -o BatchMode=yes "$TARGET" 'cd ~/cpk && chmod +x deploy/*.sh 2>/dev/null; [ -x .venv/bin/pip ] && .venv/bin/pip install -q -r requirements.txt >/dev/null 2>&1; echo deploy-done'

echo "완료. (cpk.env·state·data 는 건드리지 않음)"
