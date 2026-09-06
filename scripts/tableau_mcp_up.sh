#!/usr/bin/env bash
# 공식 Tableau MCP 서버(@tableau/mcp-server) 기동 스크립트 — Streamable HTTP 트랜스포트
#
# 배경:
#   MCP의 query-datasource는 backend가 이미 쓰는 것과 동일한 VizQL Data Service(VDS)를 호출한다.
#   → 성능 향상이 아니라 도구 표면적(뷰 CSV/이미지, Pulse, 검색 등) 확장 + VDS 폴백 목적.
#   backend TableauGateway는 USE_MCP=true & TABLEAU_GATEWAY_MODE=AUTO 일 때 이 서버를 먼저 호출하고
#   실패하면 직접 VDS로 폴백한다.
#
# 사용법:
#   scripts/tableau_mcp_up.sh                    # backend/.env 자격으로 :7788 기동
#   MCP_HTTP_PORT=7788 scripts/tableau_mcp_up.sh # 포트 지정
#
# 전제:
#   - Node/npx 설치됨 (npx가 @tableau/mcp-server 를 자동 내려받아 실행)
#   - backend/.env 에 TABLEAU_SERVER / TABLEAU_SITE / TABLEAU_PAT_NAME / TABLEAU_PAT_SECRET 채워짐
#     (직접 VDS와 동일 자격 재사용 — 시크릿을 이 스크립트에 하드코딩하지 않는다)
#
# backend 연동:
#   - 이 서버 주소를 backend/.env 의 MCP_BASE_URL 에 맞춘다 (기본 http://localhost:7788).
#   - @tableau/mcp-server 의 Streamable HTTP 엔드포인트 경로를 backend/.env 의 MCP_ROUTE 와 일치시킨다.
#     서버 기동 로그에 찍히는 실제 URL 경로를 확인하고, 다르면 MCP_ROUTE 를 그 값으로 맞출 것.
#     (backend endpoint = {MCP_BASE_URL}/{MCP_ROUTE}/ — config.py mcp_endpoint)

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="${ENV_FILE:-$ROOT_DIR/backend/.env}"
# @tableau/mcp-server v3.0.0 는 HTTP 트랜스포트에서 포트 3927 에 고정 서빙하고 엔드포인트는
# /tableau-mcp 경로다(backend MCP_ROUTE 기본값과 일치). backend/.env 의 MCP_BASE_URL 을
# http://localhost:3927 로 맞출 것. (HTTP_PORT 지정은 이 버전에서 반영되지 않는 것을 확인)
MCP_HTTP_PORT="${MCP_HTTP_PORT:-3927}"

log() { echo "[tableau-mcp] $*"; }

# ── 1. backend/.env 에서 Tableau 자격 로드 ───────────────
if [ ! -f "$ENV_FILE" ]; then
  echo "[tableau-mcp] ERROR: $ENV_FILE 없음. backend/.env 를 먼저 채우세요 (.env.example 참고)." >&2
  exit 1
fi
# .env 의 TABLEAU_* 키만 안전하게 읽는다 (export 하지 않고 값만 추출).
read_env() { grep -E "^${1}=" "$ENV_FILE" | tail -n1 | cut -d= -f2- | sed -e 's/[[:space:]]*#.*$//' -e 's/^"//' -e 's/"$//'; }

TABLEAU_SERVER="$(read_env TABLEAU_SERVER)"
TABLEAU_SITE="$(read_env TABLEAU_SITE)"
# MCP 서버 전용 PAT 우선 — 없으면 백엔드 직접 VDS와 동일 PAT로 폴백.
# ⚠️ 동일 PAT를 쓰면 Tableau Cloud의 PAT당 동시세션 제한으로 MCP 서버와 백엔드가 서로의 세션을
#    무효화(401001)한다. MCP+직접 VDS를 동시에 상시 구동하려면 MCP_PAT_NAME/SECRET에 별도 PAT를 채울 것.
TABLEAU_PAT_NAME="$(read_env MCP_PAT_NAME)"; TABLEAU_PAT_NAME="${TABLEAU_PAT_NAME:-$(read_env TABLEAU_PAT_NAME)}"
TABLEAU_PAT_SECRET="$(read_env MCP_PAT_SECRET)"; TABLEAU_PAT_SECRET="${TABLEAU_PAT_SECRET:-$(read_env TABLEAU_PAT_SECRET)}"

: "${TABLEAU_SERVER:?TABLEAU_SERVER 필요 (backend/.env)}"
: "${TABLEAU_PAT_NAME:?PAT 필요 (MCP_PAT_NAME 또는 TABLEAU_PAT_NAME in backend/.env)}"
: "${TABLEAU_PAT_SECRET:?PAT 필요 (MCP_PAT_SECRET 또는 TABLEAU_PAT_SECRET in backend/.env)}"

# ── 2. @tableau/mcp-server 환경변수 매핑 후 기동 ──────────
# (backend 키명 → MCP 서버 키명: TABLEAU_SERVER→SERVER, TABLEAU_SITE→SITE_NAME, PAT_*→PAT_NAME/PAT_VALUE)
log "MCP 서버 기동: $TABLEAU_SERVER (site='${TABLEAU_SITE}') → http://localhost:${MCP_HTTP_PORT}"
export SERVER="$TABLEAU_SERVER"
export SITE_NAME="$TABLEAU_SITE"
export PAT_NAME="$TABLEAU_PAT_NAME"
export PAT_VALUE="$TABLEAU_PAT_SECRET"
export AUTH="pat"
export TRANSPORT="http"
export HTTP_PORT="$MCP_HTTP_PORT"
# HTTP 트랜스포트는 기본으로 OAuth 2.1(per-user)을 요구한다 — 사용자별 신원·권한 enforcement.
# 이 기본값을 그대로 두는 것을 권장한다.
#
# 로컬에서 backend 게이트웨이가 PAT로 붙는 경로만 검증하려면 OAuth 를 꺼야 하는데,
# 그때만 한시적으로 켠다. 한 번만 쓸 때는 실행 시 앞에 붙이면 된다:
#   DANGEROUSLY_DISABLE_OAUTH=true scripts/tableau_mcp_up.sh
# 상시로 쓰려면 아래 줄의 주석을 푼다.
# ⚠️ MCP 서버를 localhost 밖으로 노출하는 구성에서는 절대 켜지 말 것.
# export DANGEROUSLY_DISABLE_OAUTH=true

exec npx -y @tableau/mcp-server
