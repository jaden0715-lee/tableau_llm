#!/usr/bin/env bash
# FastAPI 백엔드 기동 스크립트 — 개발용 자동 리로드(--reload) 포함
#
# 배경:
#   uvicorn 을 --reload 로 띄우면 app/ 소스를 저장하는 즉시 서버가 재기동돼
#   코드 수정이 바로 반영된다(수동 재시작 불필요). 운영 배포에는 --reload 를 쓰지 않는다.
#
# 사용법:
#   scripts/backend_up.sh                 # backend/.venv 로 :8000 기동(자동 리로드)
#   PORT=8001 scripts/backend_up.sh       # 포트 지정
#   HOST=127.0.0.1 scripts/backend_up.sh  # 바인드 주소 지정
#   RELOAD=0 scripts/backend_up.sh        # 리로드 끄기(운영/성능 측정용)
#
# 전제:
#   - backend/.venv 생성됨 (README 2. 백엔드 참고)
#   - backend/.env 채워짐 (TABLEAU_* / DIFY_* / OLLAMA_BASE_URL / USE_MCP / TABLEAU_GATEWAY_MODE 등)

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BACKEND_DIR="$ROOT_DIR/backend"
HOST="${HOST:-0.0.0.0}"
PORT="${PORT:-8000}"
RELOAD="${RELOAD:-1}"

UVICORN="$BACKEND_DIR/.venv/bin/uvicorn"
if [[ ! -x "$UVICORN" ]]; then
  echo "✗ $UVICORN 없음 — 먼저 가상환경을 만드세요:" >&2
  echo "  cd backend && python -m venv .venv && .venv/bin/pip install -U fastapi 'uvicorn[standard]' httpx pydantic-settings sqlalchemy pyyaml" >&2
  exit 1
fi

RELOAD_ARGS=()
if [[ "$RELOAD" != "0" ]]; then
  # app/ 하위만 감시해 .venv·DB 파일 변경으로 인한 불필요한 재기동을 막는다
  RELOAD_ARGS=(--reload --reload-dir "$BACKEND_DIR/app")
fi

echo "▶ uvicorn app.main:app  host=$HOST port=$PORT reload=$([[ "$RELOAD" != "0" ]] && echo on || echo off)"
cd "$BACKEND_DIR"
exec "$UVICORN" app.main:app --host "$HOST" --port "$PORT" "${RELOAD_ARGS[@]}"
