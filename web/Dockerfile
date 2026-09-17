# cpk-web (Railway). Dockerfile 로 빌더 감지 모호성 제거 + openssh-client 포함.
# Railway Settings > Root Directory = web 로 두면 이 Dockerfile 이 빌드 컨텍스트가 된다.
FROM python:3.12-slim

# 맥미니 SSH 호출용 클라이언트
RUN apt-get update \
    && apt-get install -y --no-install-recommends openssh-client ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .

EXPOSE 8000
# PORT 는 Railway 가 주입. gunicorn 타임아웃은 크롤(최대 ~90초)보다 길게.
CMD ["sh", "-c", "gunicorn app:app --bind 0.0.0.0:${PORT:-8000} --timeout 190 --workers 2"]
