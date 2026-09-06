#!/usr/bin/env bash
# Dify self-hosted 초기 프로비저닝 스크립트
# - 관리자 계정 생성 → 로그인 → Ollama 플러그인 설치 → 모델 등록 → 기본 모델 지정
# - Datasets API 키 발급 → 테스트 지식베이스 생성 → 문서 색인 → 검색 검증
#
# 사용법:
#   DIFY_URL=http://localhost:8090 ADMIN_EMAIL=... ADMIN_PASSWORD=... ./dify_provision.sh
#
# 전제:
#   - Dify 안정 버전(예: 1.15.0)이 docker compose로 기동되어 있음
#   - Ollama가 호스트에서 실행 중 (gemma4:26b, qwen3-embedding:8b pull 완료)

set -euo pipefail

DIFY_URL="${DIFY_URL:-http://localhost:8090}"
ADMIN_EMAIL="${ADMIN_EMAIL:?ADMIN_EMAIL 필요}"
ADMIN_NAME="${ADMIN_NAME:-Admin}"
ADMIN_PASSWORD="${ADMIN_PASSWORD:?ADMIN_PASSWORD 필요}"
OLLAMA_URL="${OLLAMA_URL:-http://host.docker.internal:11434}"
LLM_MODEL="${LLM_MODEL:-gemma4:26b}"
EMBED_MODEL="${EMBED_MODEL:-qwen3-embedding:8b}"
WORK_DIR="$(mktemp -d)"
COOKIES="$WORK_DIR/cookies"

log() { echo "[dify-provision] $*"; }

# ── 1. API 대기 ──────────────────────────────────────────
log "Dify API 대기 중..."
until curl -sf "$DIFY_URL/console/api/setup" >/dev/null 2>&1; do sleep 3; done

# ── 2. 관리자 계정 생성 (이미 있으면 스킵) ──────────────
SETUP_STEP=$(curl -s "$DIFY_URL/console/api/setup" | python3 -c 'import sys,json; print(json.load(sys.stdin)["step"])')
if [ "$SETUP_STEP" = "not_started" ]; then
  log "관리자 계정 생성: $ADMIN_EMAIL"
  curl -sf -X POST "$DIFY_URL/console/api/setup" -H 'Content-Type: application/json' \
    -d "{\"email\":\"$ADMIN_EMAIL\",\"name\":\"$ADMIN_NAME\",\"password\":\"$ADMIN_PASSWORD\"}" >/dev/null
else
  log "설정 완료 상태 — 계정 생성 스킵"
fi

# ── 3. 로그인 (비밀번호는 base64 인코딩 필요) ───────────
PW_B64=$(printf '%s' "$ADMIN_PASSWORD" | base64)
curl -sf -c "$COOKIES" -X POST "$DIFY_URL/console/api/login" -H 'Content-Type: application/json' \
  -d "{\"email\":\"$ADMIN_EMAIL\",\"password\":\"$PW_B64\"}" >/dev/null
CSRF=$(awk '$6 ~ /csrf/ {print $7}' "$COOKIES" | head -1)
auth_curl() { curl -s -b "$COOKIES" -H "X-CSRF-Token: $CSRF" "$@"; }
log "로그인 성공"

# ── 4. Ollama 플러그인 설치 ──────────────────────────────
PLUGIN_ID=$(curl -s "https://marketplace.dify.ai/api/v1/plugins/langgenius/ollama" \
  | python3 -c 'import sys,json; print(json.load(sys.stdin)["data"]["plugin"]["latest_package_identifier"])')
log "Ollama 플러그인 설치: $PLUGIN_ID"
TASK_ID=$(auth_curl -H 'Content-Type: application/json' -X POST \
  "$DIFY_URL/console/api/workspaces/current/plugin/install/marketplace" \
  -d "{\"plugin_unique_identifiers\":[\"$PLUGIN_ID\"]}" \
  | python3 -c 'import sys,json; print(json.load(sys.stdin)["task_id"])')
for _ in $(seq 1 30); do
  ST=$(auth_curl "$DIFY_URL/console/api/workspaces/current/plugin/tasks/$TASK_ID" \
    | python3 -c 'import sys,json; print(json.load(sys.stdin)["task"]["status"])')
  [ "$ST" = "success" ] && break
  [ "$ST" = "failed" ] && { log "플러그인 설치 실패"; exit 1; }
  sleep 3
done
log "플러그인 설치 완료"

# ── 5. 모델 등록 (credentials API — 등록 시 Ollama 연결 검증됨) ──
PROVIDER_BASE="$DIFY_URL/console/api/workspaces/current/model-providers/langgenius/ollama/ollama"
log "LLM 등록: $LLM_MODEL"
auth_curl -H 'Content-Type: application/json' -X POST "$PROVIDER_BASE/models/credentials" -d "{
  \"model\": \"$LLM_MODEL\", \"model_type\": \"llm\", \"name\": \"local-ollama-llm\",
  \"credentials\": {\"base_url\": \"$OLLAMA_URL\", \"mode\": \"chat\", \"context_size\": \"131072\",
    \"max_tokens\": \"8192\", \"vision_support\": \"false\", \"function_call_support\": \"true\"}}" >/dev/null
log "임베딩 등록: $EMBED_MODEL"
auth_curl -H 'Content-Type: application/json' -X POST "$PROVIDER_BASE/models/credentials" -d "{
  \"model\": \"$EMBED_MODEL\", \"model_type\": \"text-embedding\", \"name\": \"local-ollama-embed\",
  \"credentials\": {\"base_url\": \"$OLLAMA_URL\", \"mode\": \"chat\", \"context_size\": \"32768\", \"max_tokens\": \"8192\"}}" >/dev/null

# ── 6. 워크스페이스 기본 모델 지정 ──────────────────────
auth_curl -H 'Content-Type: application/json' -X POST "$DIFY_URL/console/api/workspaces/current/default-model" -d "{
  \"model_settings\": [
    {\"model_type\": \"llm\", \"provider\": \"langgenius/ollama/ollama\", \"model\": \"$LLM_MODEL\"},
    {\"model_type\": \"text-embedding\", \"provider\": \"langgenius/ollama/ollama\", \"model\": \"$EMBED_MODEL\"}]}" >/dev/null
log "기본 모델 지정 완료"

# ── 7. Datasets API 키 발급 ──────────────────────────────
DS_KEY=$(auth_curl -X POST "$DIFY_URL/console/api/datasets/api-keys" \
  | python3 -c 'import sys,json; print(json.load(sys.stdin)["token"])')
log "Datasets API 키: ${DS_KEY:0:12}..."
echo "$DS_KEY" > "$WORK_DIR/dataset_api_key"
log "API 키 저장 위치: $WORK_DIR/dataset_api_key (백엔드 .env의 DIFY_DATASET_API_KEY로 사용)"

# ── 8. E2E 검증: 지식베이스 생성 → 색인 → 검색 ──────────
log "E2E 검증 시작"
DSID=$(curl -s -X POST "$DIFY_URL/v1/datasets" -H "Authorization: Bearer $DS_KEY" -H 'Content-Type: application/json' \
  -d '{"name":"provision-smoke-test","permission":"only_me","indexing_technique":"high_quality"}' \
  | python3 -c 'import sys,json; print(json.load(sys.stdin)["id"])')
BATCH=$(curl -s -X POST "$DIFY_URL/v1/datasets/$DSID/document/create-by-text" -H "Authorization: Bearer $DS_KEY" -H 'Content-Type: application/json' -d '{
  "name": "smoke-test.md",
  "text": "2026년 봄맞이 프로모션은 예산 조기 소진으로 3월 10일부로 조기 종료되었습니다. 3월 중순 이후 매출에 프로모션 효과가 반영되지 않습니다.",
  "indexing_technique": "high_quality", "process_rule": {"mode": "automatic"}}' \
  | python3 -c 'import sys,json; print(json.load(sys.stdin)["batch"])')
for _ in $(seq 1 40); do
  ST=$(curl -s "$DIFY_URL/v1/datasets/$DSID/documents/$BATCH/indexing-status" -H "Authorization: Bearer $DS_KEY" \
    | python3 -c 'import sys,json; print(json.load(sys.stdin)["data"][0]["indexing_status"])')
  [ "$ST" = "completed" ] && break
  [ "$ST" = "error" ] && { log "색인 실패"; exit 1; }
  sleep 5
done
log "색인 완료 — 검색 테스트"
curl -s -X POST "$DIFY_URL/v1/datasets/$DSID/retrieve" -H "Authorization: Bearer $DS_KEY" -H 'Content-Type: application/json' -d '{
  "query": "3월 매출이 왜 떨어졌어?",
  "retrieval_model": {"search_method": "semantic_search", "reranking_enable": false, "top_k": 3, "score_threshold_enabled": false}}' \
  | python3 -c '
import sys, json
d = json.load(sys.stdin)
recs = d.get("records", [])
assert recs, f"검색 결과 없음: {d}"
for r in recs:
    print(f"  score={r.get('"'"'score'"'"')} | {r['"'"'segment'"'"']['"'"'content'"'"'][:80]}")
print("[dify-provision] 검색 검증 성공")
'
log "프로비저닝 완료 ✅"