[English](README.md) | **한국어**

# Insight Analytics — Tableau + LLM 분석 데모

**Tableau 데이터 소스에 대한 정형 질의**와 **문서 지식(RAG)** 을 하나의 대화형 어시스턴트로 융합해, 대시보드만으로는 답할 수 없는 질문까지 자연어로 답하는 데모입니다.

- **정형 데이터**: Tableau **VizQL Data Service(VDS)**, 공식 **Tableau MCP 서버**를 대체 경로로 사용 가능
- **문서 지식**: **Dify**(RAG) 다중 지식베이스 + 에이전틱-라이트 도메인 라우팅
- **오케스트레이션**: FastAPI 미니그래프(SSE 스트리밍) — 라우팅 → VDS 질의 → 문서 검색 → 융합 합성
- **LLM**: 로컬 **Ollama**(빠른 no-think 라우팅 + 합성)
- **프론트**: **React + Vite** — 임베드 대시보드, 채팅, 근거 카드

---

## 아키텍처

```
React (Vite :5175)
   │  임베드 뷰 + 질문
   ▼
FastAPI backend (:8000)  ── 미니그래프 (app/routers/chat.py, SSE)
   ├─ L1 라우팅 (데이터질의 / 문서질의 / 융합질의)
   ├─ 데이터: TableauGateway ── VDS 직접  ⇄  MCP 서버  (AUTO = VDS 우선, MCP 폴백)
   ├─ 지식: Dify RAG (L2 도메인 선택)
   └─ 합성: Ollama → 답변 + 근거
```

핵심 모듈: `backend/app/routers/chat.py`(미니그래프), `backend/app/services/tableau_gateway.py`(VDS + MCP 게이트웨이), `backend/app/knowledge.py`(지식 라우팅), `backend/app/services/dify.py`, `backend/app/services/llm.py`, `backend/app/config.py`.

---

## 주요 기능

- **L1 라우팅** — 작업을 시작하기 전에 질문을 데이터 / 문서 / 융합으로 분류합니다.
- **VDS 질의 생성** — LLM이 데이터소스 메타데이터로 질의 JSON을 만들고, 실행 전에 검증합니다. VDS 변종 간 스키마 차이(날짜 필터, `limit` 위치, 연산자 이름)는 폴백으로 흡수합니다.
- **L2 지식 라우팅** — 고정 레지스트리에서 모델이 0..N개 지식베이스를 고르고, Dify 검색 결과를 병합합니다.
- **게이트웨이 모드** — `TABLEAU_GATEWAY_MODE=VDS_ONLY | MCP_ONLY | AUTO`. MCP `query-datasource`는 **동일한 VizQL Data Service**를 호출하므로 속도 이득은 없습니다. 가치는 넓은 도구 표면적과 전송 폴백에 있습니다.
- **근거 제시** — 답변이 출처를 밝히고, 분석에 쓴 뷰를 서버에서 이미지로 렌더링할 수 있습니다.
- **문서 업로드·색인**, 피드백(👍/👎), 대시보드 요약, 추천 질문.

---

## 저장소 구조

```text
backend/            FastAPI 백엔드
  app/
    routers/        chat.py · tableau.py · documents.py · feedback.py
    services/       tableau_gateway.py (VDS + MCP) · dify.py · llm.py
    knowledge.py    지식 라우팅 레지스트리·선택
    config.py       Pydantic Settings (.env 로드)
  alembic/          DB 마이그레이션
frontend/           React + Vite (:5175)
knowledge/          샘플 지식베이스 — RAG 에 쓰는 미국 소매시장 자료
scripts/
  backend_up.sh     FastAPI 백엔드 기동 (개발용 자동 리로드)
  tableau_mcp_up.sh 공식 @tableau/mcp-server 기동 (Streamable HTTP)
  dify_provision.sh Dify 초기 프로비저닝
```

---

## 실행 방법

### 0. 전제
- 로컬에서 도는 **Ollama**(`http://localhost:11434`)와 pull 완료된 모델
- **Dify**(self-hosted) — `scripts/dify_provision.sh` 로 초기화
- **Tableau Cloud/Server** 사이트와 Personal Access Token. 토큰 기반 임베딩을 쓰려면 Connected App
- Node(프론트), Python 3(백엔드)

### 1. 설정

**백엔드** — 예시를 복사해 본인 값으로 채웁니다:
```bash
cp backend/.env.example backend/.env
# TABLEAU_SERVER / TABLEAU_SITE / TABLEAU_PAT_NAME / TABLEAU_PAT_SECRET / TABLEAU_DATASOURCE_LUID
# DIFY_* , OLLAMA_BASE_URL , USE_MCP , TABLEAU_GATEWAY_MODE
```

**프론트** — Tableau Cloud 주소는 배포마다 다르므로 하드코딩하지 않고 주입합니다:
```bash
cp frontend/.env.example frontend/.env
# VITE_TABLEAU_HOST=https://<본인-pod>.online.tableau.com   ← Embedding API 로드에도 함께 쓰입니다
# VITE_TABLEAU_SITE=<본인-사이트-ID>                         ← Tableau 주소의 /t/<여기> 부분
```
두 `.env` 파일 모두 gitignore 대상입니다. `frontend/src/components/DashboardList.tsx` 에 나열된 대시보드는 Tableau **Samples** 프로젝트(Superstore) 기준이므로, 본인 워크북·뷰로 바꿔 쓰세요.

### 2. 백엔드
```bash
cd backend
python -m venv .venv && .venv/bin/pip install -U fastapi 'uvicorn[standard]' httpx pydantic-settings sqlalchemy pyyaml pyjwt
.venv/bin/python -m alembic upgrade head        # 로컬 SQLite 스키마 생성
cd .. && scripts/backend_up.sh                  # :8000 기동 · 자동 리로드
# PORT=8001 scripts/backend_up.sh               # 포트 지정 / RELOAD=0 으로 리로드 끄기
```

### 3. 프론트
```bash
cd frontend && npm install && npm run dev    # http://localhost:5175
```

### 4. (선택) Tableau MCP 서버
MCP 경로는 기본 비활성입니다.
```bash
scripts/tableau_mcp_up.sh      # @tableau/mcp-server HTTP 기동 (자격은 backend/.env 에서 읽음)
# 이후 backend/.env 에 USE_MCP=true , TABLEAU_GATEWAY_MODE=AUTO 설정 후 백엔드 재기동
```

> ⚠️ MCP 서버와 백엔드를 **동시에 상시 구동**한다면 **MCP 전용 PAT**를 따로 쓰세요. Tableau Cloud 는 PAT당 동시 세션을 제한하기 때문에, 같은 PAT를 공유하면 서로 세션을 무효화합니다(`401001`). `backend/.env` 에 `MCP_PAT_NAME` / `MCP_PAT_SECRET` 을 채우면 스크립트가 이를 우선 사용하고, 없으면 `TABLEAU_PAT_*` 로 폴백합니다.

> MCP 서버의 HTTP 트랜스포트는 기본으로 **OAuth 2.1(사용자별)** 을 요구하며, 스크립트는 이 기본값을 그대로 둡니다. 로컬에서 백엔드의 PAT 기반 게이트웨이 경로만 확인하고 싶다면, 그 실행에 한해 꺼서 쓰세요:
> ```bash
> DANGEROUSLY_DISABLE_OAUTH=true scripts/tableau_mcp_up.sh
> ```
> localhost 밖에서 접근 가능한 MCP 서버에는 절대 쓰지 마세요 — 사용자별 신원·권한 enforcement 가 사라집니다.

---

## 주요 API

| 메서드 · 경로 | 설명 |
|---|---|
| `GET /healthz` | 상태·실효 설정(`gateway_mode`, `use_mcp`, `mcp_endpoint`, LLM) |
| `POST /api/chat` | SSE 대화: route → step → citations → vds → token → done |
| `POST /api/chat/suggest` · `POST /api/chat/summary` | 추천 질문·대시보드 요약 |
| `GET /api/chat/history/{channel}` | 채널별 대화 이력 |
| `GET /api/tableau/metadata` · `POST /api/tableau/query` | VDS 메타·질의 |
| `GET /api/tableau/view-image` | 근거용 뷰 서버 렌더 PNG |
| `GET /api/tableau/embed-token` | 임베딩용 Connected App JWT |
| `GET /api/tableau/mcp/tools` · `POST /api/tableau/mcp/tools/call` | MCP 도구 목록·호출(`USE_MCP=true`) |
| `POST /api/documents` · `GET /api/documents` · `DELETE /api/documents/{id}` | 문서 업로드·목록·삭제 |
| `POST /api/feedback` · `GET /api/feedback/stats` | 답변 피드백·집계 |

응답 헤더 `X-Gateway: MCP \| VDS` 로 실제 처리 경로를 확인할 수 있습니다. `AUTO` 에서 MCP 실패 시 VDS로 자동 폴백합니다.

---

## 참고 사항

- `QUANTITATIVE_DATE` 필터는 Tableau 문서에 직접 나오지 않아, 게이트웨이가 여러 `RANGE` 형태를 시도해 성공하는 방식을 사용합니다.
- `options.limit` 과 최상위 `limit` 은 VDS 변종마다 의미가 달라, 페이로드 빌더가 모드별로 올바른 위치에 넣습니다.
- 폴백 모드는 VDS 스키마가 바뀌어도 질의 경로 전체가 깨지지 않고 완만하게 저하되도록 하기 위한 장치입니다.
