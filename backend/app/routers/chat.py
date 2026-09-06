# routers/chat.py — 채팅 SSE 엔드포인트 (P0-3/P0-6/P0-9)
# 미니 그래프: 라우터(no-think 분류) → [VDS 질의 생성·실행 | Dify 검색] → 스트리밍 합성
# SSE 이벤트: route → step*(작업 과정) → (citations) → (vds) → token* → done(meta)
# TODO(Phase 2): LangGraph StateGraph로 정식 전환
import asyncio
import json
import logging
import re
from typing import Any, AsyncIterator, Dict, List, Optional, Tuple

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session
from sse_starlette.sse import EventSourceResponse

from app.config import Settings, get_settings
from app.db.base import get_db
from app.db.models import Channel, Feedback, Message
from app.knowledge import build_select_prompt, kb_registry, parse_selection, resolve_scope
from app.routers.tableau import get_gateway
from app.services.dify import DifyClient
from app.services.llm import LLMService
from app.services.tableau_gateway import TableauGateway

router = APIRouter(prefix="/api/chat", tags=["chat"])

ROUTE_PROMPT = """다음 질문을 세 가지 유형 중 하나로 분류하세요: 데이터질의/문서질의/융합질의.
- 데이터질의: 수치·집계·추이 등 대시보드 데이터로만 답할 수 있는 질문
- 문서질의: 정책·공지·매뉴얼 등 문서로만 답할 수 있는 질문
- 융합질의: 수치와 문서 근거가 모두 필요한 질문
답은 유형명만 출력. 질문: "{query}\""""

VDS_QUERY_PROMPT = """당신은 Tableau VizQL Data Service(VDS) 질의 생성기입니다.
사용 가능한 필드 목록:
{fields}

사용자 질문: "{query}"
{mark}
질문에 답하는 데 필요한 집계 질의를 1~5개 생성해 아래 JSON 형식으로만 출력하세요. 설명 금지, JSON만 출력.
{{"queries": [{{"fields": [{{"fieldCaption": "<필드명>"}}, {{"fieldCaption": "<측정값 필드명>", "function": "SUM"}}], "filters": []}}]}}

규칙:
- 좁은 질문(특정 수치 하나)은 질의 1개, 넓은 질문(요약/전체 현황/원인 분석)은 서로 다른 관점의 질의 2~5개
- 선택 마크가 주어지면 그 대상을 SET 필터로 고정하고 총계·카테고리별·세그먼트별·서브카테고리별·배송모드별 등 서로 다른 차원으로 분해하는 질의 3~5개 생성
- fieldCaption은 반드시 위 필드 목록에 있는 이름만 사용
- 측정값(REAL/INTEGER)에는 function(SUM|AVG|COUNT|MIN|MAX) 지정, 차원(STRING)은 function 없이
- 날짜 필드로 추이를 볼 때는 {{"fieldCaption": "<날짜필드>", "function": "TRUNC_MONTH"}} 또는 TRUNC_YEAR
- filterType은 SET 과 QUANTITATIVE_DATE 두 가지만 존재. 다른 타입 금지 (CATEGORICAL 아님)
- 특정 값 필터(예: 특정 지역/카테고리): {{"field": {{"fieldCaption": "<필드>"}}, "filterType": "SET", "values": ["<값>"]}}
- 날짜 범위 필터: {{"field": {{"fieldCaption": "<날짜필드>"}}, "filterType": "QUANTITATIVE_DATE", "quantitativeFilterType": "RANGE", "minDate": "YYYY-MM-DD", "maxDate": "YYYY-MM-DD"}}
- 질의당 필드는 최대 4개, 단순하게

예시 1 — 질문: "카테고리별 매출 순위 보여줘"
{{"queries": [{{"fields": [{{"fieldCaption": "Category"}}, {{"fieldCaption": "Sales", "function": "SUM", "sortDirection": "DESC", "sortPriority": 1}}], "filters": []}}]}}

예시 2 — 질문: "2024년 월별 매출 추이는?"
{{"queries": [{{"fields": [{{"fieldCaption": "Order Date", "function": "TRUNC_MONTH"}}, {{"fieldCaption": "Sales", "function": "SUM"}}], "filters": [{{"field": {{"fieldCaption": "Order Date"}}, "filterType": "QUANTITATIVE_DATE", "quantitativeFilterType": "RANGE", "minDate": "2024-01-01", "maxDate": "2024-12-31"}}]}}]}}

예시 3 — 질문: "West 지역의 세그먼트별 이익은?" (특정 값 필터 → SET)
{{"queries": [{{"fields": [{{"fieldCaption": "Segment"}}, {{"fieldCaption": "Profit", "function": "SUM"}}], "filters": [{{"field": {{"fieldCaption": "Region"}}, "filterType": "SET", "values": ["West"]}}]}}]}}

예시 4 — 질문: "이 대시보드 전체 현황을 요약해줘" (넓은 질문 → 관점이 다른 질의 3개)
{{"queries": [{{"fields": [{{"fieldCaption": "Sales", "function": "SUM"}}, {{"fieldCaption": "Profit", "function": "SUM"}}], "filters": []}}, {{"fields": [{{"fieldCaption": "Category"}}, {{"fieldCaption": "Sales", "function": "SUM"}}, {{"fieldCaption": "Profit", "function": "SUM"}}], "filters": []}}, {{"fields": [{{"fieldCaption": "Order Date", "function": "TRUNC_MONTH"}}, {{"fieldCaption": "Sales", "function": "SUM"}}], "filters": []}}]}}

예시 5 — 선택 마크(State/Province=Ohio) 설명: 그 지역을 고정하고 여러 관점으로 분해 (모든 질의에 동일 SET 필터)
{{"queries": [{{"fields": [{{"fieldCaption": "Sales", "function": "SUM"}}, {{"fieldCaption": "Profit", "function": "SUM"}}, {{"fieldCaption": "Quantity", "function": "SUM"}}], "filters": [{{"field": {{"fieldCaption": "State/Province"}}, "filterType": "SET", "values": ["Ohio"]}}]}}, {{"fields": [{{"fieldCaption": "Category"}}, {{"fieldCaption": "Sales", "function": "SUM"}}, {{"fieldCaption": "Profit", "function": "SUM"}}], "filters": [{{"field": {{"fieldCaption": "State/Province"}}, "filterType": "SET", "values": ["Ohio"]}}]}}, {{"fields": [{{"fieldCaption": "Segment"}}, {{"fieldCaption": "Profit", "function": "SUM"}}], "filters": [{{"field": {{"fieldCaption": "State/Province"}}, "filterType": "SET", "values": ["Ohio"]}}]}}, {{"fields": [{{"fieldCaption": "Sub-Category"}}, {{"fieldCaption": "Profit", "function": "SUM", "sortDirection": "ASC", "sortPriority": 1}}], "filters": [{{"field": {{"fieldCaption": "State/Province"}}, "filterType": "SET", "values": ["Ohio"]}}]}}]}}"""

# 재시도 프롬프트 — 직전 실패 원인을 피드백해 1회 재생성 (A3: VDS 질의 생성 안정화)
VDS_RETRY_SUFFIX = """

주의: 이전 시도가 실패했습니다. 아래 원인을 수정해 올바른 JSON만 다시 출력하세요.
- 이전 출력: {prev}
- 실패 원인: {reason}"""

VDS_MAX_ATTEMPTS = 2  # 최초 1회 + 재시도 1회

# 문서 근거 관련성 임계값 — 이보다 낮은 유사도는 "근거"로 표시하지 않음(약한 매치=노이즈)
CITATION_MIN_SCORE = 0.5

SUGGEST_PROMPT = """Tableau 대시보드 "{dashboard}"를 분석하는 사용자에게 도움이 될 짧은 분석 질문 3개를 한국어로 제안하세요.
현재 보고 있는 시트: {sheet}
데이터 필드: {fields}
현재 시트와 관련된 구체적이고 바로 물어볼 수 있는 질문 위주로. 각 질문은 25자 이내.
JSON만 출력: {{"questions": ["질문1", "질문2", "질문3"]}}"""


def _extract_json(text: str) -> Optional[Dict[str, Any]]:
    """LLM 출력에서 첫 JSON 오브젝트 추출 (```json 펜스 허용)."""
    text = re.sub(r"```(?:json)?", "", text)
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        return None
    try:
        return json.loads(m.group(0))
    except Exception:
        return None


def _extract_specs(obj: Optional[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """{"queries": [...]} 또는 단일 {"fields": ...} (구형식) → 질의 리스트."""
    if not obj:
        return []
    if isinstance(obj.get("queries"), list):
        return [q for q in obj["queries"] if isinstance(q, dict)][:5]  # 마크 프로파일·다관점 비교에 최대 5개
    if obj.get("fields"):
        return [obj]
    return []


def _normalize_specs(specs: List[Dict[str, Any]]) -> None:
    """LLM이 습관적으로 낸 'CATEGORICAL'을 VDS가 요구하는 'SET'으로 교정 (in-place)."""
    for sp in specs:
        for fl in sp.get("filters") or []:
            if (fl.get("filterType") or "").upper() == "CATEGORICAL":
                fl["filterType"] = "SET"


def _merge_applied_filters(specs: List[Dict[str, Any]], applied: Dict[str, List[str]],
                           valid_dims: set) -> None:
    """사용자가 화면에서 건 필터를 각 질의에 SET으로 보장 (in-place).
    차원(STRING) 필드만 병합해 측정값에 잘못된 SET이 걸리는 것을 방지. 이미 같은 필드 필터가
    있으면 건드리지 않음(LLM이 더 구체적으로 건 경우 존중)."""
    for sp in specs:
        existing = {(f.get("field") or {}).get("fieldCaption") for f in sp.get("filters") or []}
        for field, values in applied.items():
            if field in valid_dims and field not in existing and values:
                sp.setdefault("filters", []).append(
                    {"field": {"fieldCaption": field}, "filterType": "SET", "values": list(values)})


def _validate_spec(spec: Dict[str, Any], valid_fields: set) -> Optional[str]:
    """VDS 실행 전 사전 검증 — 문제가 있으면 사유 반환 (재시도 프롬프트에 피드백).
    존재하지 않는 필드/필터 타입을 서버 왕복 없이 걸러 400 재시도 낭비를 줄임."""
    if not spec.get("fields"):
        return "fields 배열이 없음"
    problems: List[str] = []
    for f in spec["fields"]:
        cap = f.get("fieldCaption")
        if cap not in valid_fields:
            problems.append(f"필드 목록에 없는 필드 사용: {cap!r}")
    for fl in spec.get("filters") or []:
        ft = fl.get("filterType")
        if ft not in ("SET", "QUANTITATIVE_DATE"):
            problems.append(f"허용되지 않는 filterType: {ft!r} (SET|QUANTITATIVE_DATE만 가능)")
        cap = (fl.get("field") or {}).get("fieldCaption")
        if cap not in valid_fields:
            problems.append(f"필터에 필드 목록에 없는 필드 사용: {cap!r}")
    return "; ".join(problems) or None


def _sheets_context_text(sheets: List[Dict[str, Any]], active: Optional[str]) -> str:
    """임베드 화면에서 수집한 시트별 표시 데이터 → 합성 컨텍스트 텍스트.
    전체 시트를 근거로 답하게 하되(요청 2) 토큰 폭주 방지를 위해 시트/행/문자 수 제한."""
    parts = ["[화면 시트 데이터 — 임베드된 대시보드에 실제 표시 중인 값 (사용자 적용 필터 반영)]"]
    budget = 60000   # 컨텍스트 창(num_ctx)을 키웠으므로 여러 시트를 넉넉히 담음
    # 현재 보고 있는 시트를 먼저 처리해 예산·행수를 우선 배정 (크로스탭 등 큰 시트가 잘리지 않게)
    ordered = sorted(sheets[:8], key=lambda s: 0 if active and str(s.get("sheet")) == active else 1)
    for s in ordered:
        name = str(s.get("sheet", "?"))
        is_active = bool(active and name == active)
        marker = " ← 현재 보고 있는 시트" if is_active else ""
        cols = s.get("columns") or []
        rows = s.get("rows") or []
        row_cap = 300 if is_active else 150   # 현재 시트 우선, 다른 시트도 넉넉히
        char_cap = 18000 if is_active else 8000
        lines = [f"## 시트: {name}{marker}", "컬럼: " + " | ".join(map(str, cols))]
        for r in rows[:row_cap]:
            lines.append(" | ".join("" if v is None else str(v) for v in r))
        if len(rows) > row_cap or s.get("truncated"):
            lines.append("(일부 행 생략)")
        chunk = "\n".join(lines)
        if len(chunk) > char_cap:
            chunk = chunk[:char_cap] + "\n(생략)"
        if budget - len(chunk) < 0:
            parts.append("(이후 시트 생략)")
            break
        budget -= len(chunk)
        parts.append(chunk)
    return "\n".join(parts)


async def _vds_node(llm: LLMService, gateway: TableauGateway, settings: Settings,
                    query: str, mark_context: Optional[Dict],
                    applied_filters: Optional[Dict[str, List[str]]] = None) -> AsyncIterator[Tuple[str, Any]]:
    """데이터질의 노드: 메타데이터 → LLM 질의 생성(1~3개) → 사전 검증 → VDS 실행.
    진행 상황을 ("step", {...})로 흘리고 마지막에 ("result", (ctx, meta)) 산출.
    생성/검증/실행 어느 단계든 실패하면 원인을 프롬프트에 피드백해 1회 재시도 (A3).
    applied_filters: 사용자가 임베드 화면에서 건 필터 — 각 질의에 SET으로 강제 반영."""
    ds_luid = gateway.resolve_ds_luid(None, None)  # 기본 데이터소스
    mode = settings.tableau_gateway_mode

    yield "step", {"id": "vds-meta", "label": "데이터소스 필드 조회", "status": "run"}
    meta, _ = await asyncio.to_thread(gateway.read_metadata, ds_luid, mode)
    field_rows = meta.get("data") or []

    valid_fields = {
        n for f in field_rows
        for n in (f.get("fieldName"), f.get("fieldCaption")) if n
    }
    valid_dims = {                    # 차원(STRING)만 — 화면 필터 강제 병합 시 안전
        (f.get("fieldName") or f.get("fieldCaption"))
        for f in field_rows if str(f.get("dataType", "")).upper() == "STRING"
    }
    field_desc = "\n".join(
        f"- {f.get('fieldName') or f.get('fieldCaption')} ({f.get('dataType', '?')})"
        for f in field_rows[:40]
    )

    # 화면 필터 중 데이터소스에 실제 존재하는 차원만 VDS 질의에 사용.
    # (예: 'Order Profitable?' 같은 워크시트 계산필드는 조회 불가 → 힌트/병합에서 제외해야
    #  '없는 필드' 검증 실패가 재시도마다 반복되는 무한 실패를 막음. 화면 반영은 sheets_context가 담당)
    usable_applied = {k: v for k, v in (applied_filters or {}).items() if k in valid_dims and v}
    dropped_applied = [k for k in (applied_filters or {}) if k not in usable_applied]
    meta_detail = f"{len(field_rows)}개 필드"
    if dropped_applied:
        meta_detail += f" · 화면 필터 제외(데이터소스에 없음): {', '.join(dropped_applied)}"
    yield "step", {"id": "vds-meta", "label": "데이터소스 필드 조회", "status": "done", "detail": meta_detail}

    # 선택 마크의 차원값(조회 가능한 것만) → 그 엔티티를 여러 관점으로 분해하는 근거로 사용
    mark_dims = {k: [str(v)] for k, v in (mark_context or {}).items()
                 if k in valid_dims and v not in (None, "")}

    ctx_lines: List[str] = []
    if mark_dims:
        md_text = ", ".join(f"{k}={v[0]}" for k, v in mark_dims.items())
        ctx_lines.append(
            f"사용자가 선택한 마크 = {md_text}. 이 대상을 여러 관점으로 분석하세요: "
            "각 질의에 이 값(들)을 SET 필터로 고정하고, 총계·카테고리별·세그먼트별·서브카테고리별·배송모드별 등 "
            "서로 다른 차원으로 분해하는 질의 3~5개를 생성하세요. fieldCaption은 필드명만.")
    elif mark_context:
        ctx_lines.append(f"현재 선택된 마크: {json.dumps(mark_context, ensure_ascii=False)}")
    if usable_applied:
        # 필드명과 값을 분리해 제시 — 모델이 '필드=값' 전체를 fieldCaption으로 붙이는 사고 방지
        af_lines = "\n".join(f'- "{k}" 값: {json.dumps(v, ensure_ascii=False)}' for k, v in usable_applied.items())
        ctx_lines.append(
            "사용자가 화면에 적용한 필터 — 아래 각 필드를 filters에 SET으로 포함하세요"
            " (fieldCaption은 필드명만, 값은 values 배열):\n" + af_lines)
    mark = ("\n".join(ctx_lines) + "\n") if ctx_lines else ""
    base_prompt = VDS_QUERY_PROMPT.format(fields=field_desc, query=query, mark=mark)

    prompt = base_prompt
    last_error = "vds_query_generation_failed"
    for attempt in range(1, VDS_MAX_ATTEMPTS + 1):
        gen_label = "VDS 질의 생성" + (" (재시도)" if attempt > 1 else "")
        yield "step", {"id": f"vds-gen-{attempt}", "label": gen_label, "status": "run"}
        raw = await llm.complete(
            [{"role": "user", "content": prompt}],
            model=settings.llm_router_model, think=False,
        )
        specs = _extract_specs(_extract_json(raw))
        _normalize_specs(specs)   # CATEGORICAL → SET 교정
        if usable_applied:        # 화면 필터를 각 질의에 SET으로 보장 (조회 가능한 차원만)
            _merge_applied_filters(specs, usable_applied, valid_dims)
        if mark_dims:             # 선택 마크(예: State/Province=Ohio)를 각 질의에 SET으로 고정
            _merge_applied_filters(specs, mark_dims, valid_dims)

        # 사전 검증 실패 → 원인 피드백 후 재생성
        problems: List[str] = [] if specs else ["출력에 유효한 질의가 없음"]
        for i, sp in enumerate(specs):
            p = _validate_spec(sp, valid_fields)
            if p:
                problems.append(f"질의{i + 1}: {p}")
        if problems:
            reason = "; ".join(problems)
            yield "step", {"id": f"vds-gen-{attempt}", "label": gen_label, "status": "fail",
                           "detail": reason[:80]}
            last_error = f"vds_query_generation_failed: {reason}"
            prompt = base_prompt + VDS_RETRY_SUFFIX.format(prev=raw[:400], reason=reason)
            continue
        yield "step", {"id": f"vds-gen-{attempt}", "label": gen_label, "status": "done",
                       "detail": f"{len(specs)}개 질의"}

        # 실행 (질의별 — 일부 실패는 성공분으로 진행, 전부 실패 시 재생성)
        yield "step", {"id": f"vds-exec-{attempt}", "label": "실데이터 조회", "status": "run"}
        results: List[Tuple[Dict[str, Any], List[Dict], str]] = []
        errors: List[str] = []
        for i, sp in enumerate(specs):
            try:
                result, used = await asyncio.to_thread(
                    gateway.query, ds_luid, sp["fields"], sp.get("filters") or [],
                    {"returnFormat": "OBJECTS", "disaggregate": False}, 50, mode,
                )
                results.append((sp, result.get("data", [])[:50], used))
            except Exception as e:
                errors.append(f"질의{i + 1}: {str(e)[:150]}")
        if not results:
            reason = "; ".join(errors)
            yield "step", {"id": f"vds-exec-{attempt}", "label": "실데이터 조회", "status": "fail",
                           "detail": reason[:80]}
            last_error = f"vds_query_failed: {reason[:200]}"
            prompt = base_prompt + VDS_RETRY_SUFFIX.format(
                prev=json.dumps(specs, ensure_ascii=False)[:400], reason=reason[:300])
            continue

        total_rows = sum(len(rows) for _, rows, _ in results)
        used = results[0][2]
        yield "step", {"id": f"vds-exec-{attempt}", "label": "실데이터 조회", "status": "done",
                       "detail": f"질의 {len(results)}개 · {total_rows}행"}

        parts = [f"[대시보드 데이터 — VDS 질의 결과 (게이트웨이: {used})]"]
        for i, (sp, rows, _) in enumerate(results, 1):
            parts.append(f"질의 {i}: {json.dumps(sp, ensure_ascii=False)}")
            parts.append(f"결과 {i} ({len(rows)}행): {json.dumps(rows, ensure_ascii=False)}")
        ctx = "\n".join(parts)
        yield "result", (ctx, {
            "datasource": "superstore", "gateway": used,
            "query_spec": results[0][0],                       # 필터칩용 (기존 계약 유지)
            "query_specs": [sp for sp, _, _ in results],
            "row_count": total_rows, "attempts": attempt,
        })
        return

    yield "result", (None, {"error": last_error, "attempts": VDS_MAX_ATTEMPTS})


class ChatRequest(BaseModel):
    channel: str = "sales-analytics"
    message: str
    thread_root_id: Optional[str] = None
    mark_context: Optional[Dict] = None                # Tableau 마크 선택 컨텍스트
    active_sheet: Optional[str] = None                 # 임베드 뷰에서 현재 보고 있는 시트
    sheets_context: Optional[List[Dict[str, Any]]] = None  # 시트별 표시 데이터 (전체 시트 근거)
    applied_filters: Optional[Dict[str, List[str]]] = None  # 사용자가 화면에서 건 필터 {field:[values]}
    knowledge_scope: Optional[str] = None              # 지식 범위: auto(기본)/전략/일반/전체 (UI 칩 오버라이드)


def _get_or_create_channel(db: Session, name: str) -> Channel:
    ch = db.query(Channel).filter(Channel.name == name).first()
    if not ch:
        ch = Channel(name=name)
        db.add(ch)
        db.commit()
    return ch


def _step(payload: Dict[str, Any]) -> dict:
    return {"event": "step", "data": json.dumps(payload, ensure_ascii=False)}


@router.post("")
async def chat(req: ChatRequest,
               settings: Settings = Depends(get_settings),
               db: Session = Depends(get_db),
               gateway: TableauGateway = Depends(get_gateway)):
    llm = LLMService(settings)
    dify = DifyClient(settings)

    channel = _get_or_create_channel(db, req.channel)
    user_msg = Message(
        channel_id=channel.id, role="user", content=req.message,
        thread_root_id=req.thread_root_id,
        mark_context_json=json.dumps(req.mark_context or {}, ensure_ascii=False),
    )
    db.add(user_msg)
    db.commit()

    async def event_stream() -> AsyncIterator[dict]:
        # 작업 과정 스텝을 id별 최종 상태로 기록 → 답변 meta에 넣어 완료 후에도 유지 (스트리밍 종료 시 사라지지 않게)
        step_log: Dict[str, Dict[str, Any]] = {}

        def rec(payload: Dict[str, Any]) -> dict:
            step_log[payload["id"]] = {k: payload.get(k) for k in ("id", "label", "status", "detail") if payload.get(k) is not None}
            return _step(payload)

        # ① 라우팅 (no-think, ~0.5s 검증됨)
        yield rec({"id": "route", "label": "질문 유형 분석", "status": "run"})
        route_raw = await llm.complete(
            [{"role": "user", "content": ROUTE_PROMPT.format(query=req.message)}],
            model=settings.llm_router_model, think=False,
        )
        route = next((r for r in ("융합질의", "데이터질의", "문서질의") if r in route_raw), "융합질의")
        # 사용자가 지식 범위를 명시(칩: 전략/일반/전체)했으면 반드시 지식 검색 —
        # 데이터질의로 분류돼도 융합으로 승격해 지식이 무시되지 않게 (auto는 라우터 판단 존중)
        _scope = (req.knowledge_scope or "").strip()
        if _scope and _scope not in ("auto", "자동") and route == "데이터질의":
            route = "융합질의"
        # 마크 선택 = 특정 엔티티(예: Ohio) 데이터 분석 → 문서질의로 분류돼도 데이터+지식 융합으로 승격
        if req.mark_context and route == "문서질의":
            route = "융합질의"
        yield rec({"id": "route", "label": "질문 유형 분석", "status": "done", "detail": route})
        yield {"event": "route", "data": json.dumps({"route": route}, ensure_ascii=False)}

        # ② 컨텍스트 수집
        doc_citations: List[Dict] = []
        data_sources: List[Dict] = []
        context_parts: List[str] = []

        if req.mark_context:
            context_parts.append(f"[선택된 마크]\n{json.dumps(req.mark_context, ensure_ascii=False)}")

        # 사용자가 화면에서 건 필터 — 답변·질의 모두 이 필터 기준으로 반영
        if req.applied_filters:
            af_text = ", ".join(f"{k}={'/'.join(v)}" for k, v in req.applied_filters.items())
            context_parts.append(
                f"[사용자가 현재 화면에 적용한 필터]\n{af_text}\n"
                f"반드시 이 필터가 적용된 범위 기준으로 답하세요. 전체 데이터로 답하지 마세요.")
            yield rec({"id": "applied", "label": "화면 적용 필터 반영", "status": "done", "detail": af_text})

        # 화면 시트 데이터 (요청 2: 전체 시트 기준 답변)
        if req.sheets_context:
            context_parts.append(_sheets_context_text(req.sheets_context, req.active_sheet))
            yield rec({"id": "sheets", "label": "화면 시트 데이터 반영", "status": "done",
                         "detail": f"{len(req.sheets_context)}개 시트"
                                   + (f" · 현재: {req.active_sheet}" if req.active_sheet else "")})

        if route in ("문서질의", "융합질의"):
            registry = kb_registry(settings)
            chunks: List[Dict] = []
            if registry:
                # ── L2 에이전틱-라이트 지식 소스 선택 (docs/KNOWLEDGE_ROUTING_DESIGN.md) ──
                forced = resolve_scope(registry, req.knowledge_scope)
                if forced is None:
                    # 자동: 모델이 도메인 선택 (no-think, 구조화 JSON)
                    yield rec({"id": "kb", "label": "지식 소스 선택", "status": "run"})
                    sel_raw = await llm.complete(
                        [{"role": "user", "content": build_select_prompt(registry, req.message)}],
                        model=settings.llm_router_model, think=False,
                    )
                    keys, reason = parse_selection(sel_raw, registry)
                    picked = [e for e in registry if e["key"] in keys]
                else:
                    picked = forced
                    reason = "사용자 지정" if (req.knowledge_scope or "").strip() not in ("", "auto", "자동") else ""
                yield rec({"id": "kb", "label": "지식 소스 선택", "status": "done",
                             "detail": ("/".join(e["key"] for e in picked) or "없음")
                                       + (f" · {reason}" if reason else "")})

                yield rec({"id": "docs", "label": "관련 문서 검색", "status": "run"})
                if picked:
                    chunks = dify.retrieve_many([e["id"] for e in picked], req.message, top_k=4)
                # 약한 검색 가드: 0건 or 최고 score < 임계 → 전체 팬아웃 1회 (경계 있는 자율)
                best = max((c.get("score") or 0) for c in chunks) if chunks else 0.0
                all_ids = [e["id"] for e in registry]
                if (not chunks or best < CITATION_MIN_SCORE) and {e["id"] for e in picked} != set(all_ids):
                    fan = dify.retrieve_many(all_ids, req.message, top_k=4)
                    if fan:
                        chunks = fan
                        yield rec({"id": "kb-fan", "label": "약한 매치 → 전체 지식 재검색",
                                     "status": "done", "detail": "팬아웃 1회"})
            else:
                # 폴백: 레지스트리 미구성 → 기존 단일 dataset
                dataset_id = channel.dify_dataset_id or settings.dify_default_dataset_id
                yield rec({"id": "docs", "label": "관련 문서 검색", "status": "run"})
                if dataset_id:
                    chunks = dify.retrieve(dataset_id, req.message, top_k=4)

            # ── 공통: 관련성 임계값 필터 + 인용 구성 (약한 매치는 근거·컨텍스트 모두 제외) ──
            relevant = [c for c in chunks if (c.get("score") or 0) >= CITATION_MIN_SCORE]
            doc_citations = [{"name": c["document_name"], "score": c["score"],
                              "excerpt": (c.get("content") or "").strip()[:800]} for c in relevant]
            if relevant:
                docs_text = "\n---\n".join(f"({c['document_name']}) {c['content']}" for c in relevant)
                context_parts.append(f"[관련 문서 발췌]\n{docs_text}")
            dropped = len(chunks) - len(relevant)
            detail = f"{len(relevant)}건" + (f" (약한 매치 {dropped}건 제외)" if dropped else "")
            yield rec({"id": "docs", "label": "관련 문서 검색", "status": "done", "detail": detail})
            yield {"event": "citations", "data": json.dumps(doc_citations, ensure_ascii=False)}

        # 데이터질의 노드: VDS 질의 생성·실행 (P0-6 융합의 데이터 축)
        if route in ("데이터질의", "융합질의") and settings.tableau_server:
            ctx, vds_meta = None, {}
            async for kind, payload in _vds_node(llm, gateway, settings, req.message,
                                                 req.mark_context, req.applied_filters):
                if kind == "step":
                    yield rec(payload)
                else:
                    ctx, vds_meta = payload
            if ctx:
                context_parts.append(ctx)
                data_sources.append(vds_meta)
                yield {"event": "vds", "data": json.dumps(vds_meta, ensure_ascii=False)}
            elif vds_meta.get("error"):
                yield {"event": "vds", "data": json.dumps(vds_meta, ensure_ascii=False)}

        # ③ 합성 스트리밍
        yield rec({"id": "synth", "label": "답변 생성", "status": "run"})
        system = (
            "당신은 Tableau 대시보드와 사내 문서를 함께 분석하는 Insight Bot입니다. "
            "제공된 컨텍스트에 근거해 한국어로 답하고, 근거가 없는 내용은 지어내지 마세요. "
            "화면 시트 데이터가 있으면 특정 시트 하나가 아니라 전체 시트를 종합해 답하세요. "
            "수치를 인용할 때는 출처(데이터/문서/시트명)를 명시하세요.\n"
            "[표·차트 표시 규칙]\n"
            "- 표시 형식은 사용자 요청을 우선하세요: '그래프/차트로만' → 표 없이 차트만, '표로만' → 차트 없이 표만, "
            "지정이 없으면 표 + 차트 둘 다.\n"
            "- 표는 GitHub 마크다운. 구분행은 정확히 `| --- | ---: | ---: |` 형식만(정렬 `---`/`---:`/`:---`, 셀에 `/` 등 금지). "
            "표 앞에 빈 줄, 각 행은 줄바꿈.\n"
            "- 차트 코드블록(최대 1개). 형식은 정확히 아래 JSON:\n"
            "```chart\n"
            '{"type":"bar","title":"제목","x":["West","East"],"series":[{"name":"실적","data":[10,20]},{"name":"예측","data":[12,18]}]}\n'
            "```\n"
            "  여러 값을 비교하려면 series를 여러 개 넣으세요(예: 실적 vs 예측). type은 bar(항목 비교) 또는 line(시간 추이)만. "
            "x 길이와 각 series.data 길이는 반드시 같아야 합니다.\n"
            "- 표·차트의 모든 수치는 제공된 데이터에서만. 절대 지어내지 마세요. x축·계열 이름은 한국어로.\n"
            "- 사용자가 여러 항목(예: 4개 지역, 3개 세그먼트)을 요청했는데 제공된 데이터에 일부만 있으면, "
            "있는 것만 보여주고 '나머지(예: 다른 지역)는 현재 화면 데이터에 없어 제외됨'이라고 명확히 밝히세요. 없는 값을 지어내지 마세요.\n"
            "- 비교할 수치가 없거나 문서 위주 답변이면 표·차트를 넣지 마세요."
        )
        if req.active_sheet:
            system += f" 사용자가 현재 보고 있는 시트: {req.active_sheet}."
        user_content = req.message if not context_parts else (
            "\n\n".join(context_parts) + f"\n\n[질문]\n{req.message}"
        )
        full: List[str] = []
        async for chunk in llm.stream(
            [{"role": "system", "content": system}, {"role": "user", "content": user_content}],
            model=settings.llm_synthesis_model, think=False, num_ctx=settings.llm_num_ctx,
        ):
            full.append(chunk)
            yield {"event": "token", "data": json.dumps({"t": chunk}, ensure_ascii=False)}
        yield rec({"id": "synth", "label": "답변 생성", "status": "done"})

        # ④ 저장 + 종료 이벤트 (P0-9 출처 메타 포함)
        # active_sheet 보존 — 캡처 이미지가 분석한 시트(탭)를 그대로 렌더하도록 (Overview 고정 방지)
        # steps — 작업 과정을 답변과 함께 저장해 완료 후에도(새로고침 포함) 유지
        meta = {"route": route, "doc_citations": doc_citations, "data_sources": data_sources,
                "active_sheet": req.active_sheet or None, "steps": list(step_log.values())}
        bot_msg = Message(
            channel_id=channel.id, role="assistant", content="".join(full),
            thread_root_id=req.thread_root_id or user_msg.id,
            meta_json=json.dumps(meta, ensure_ascii=False),
        )
        db.add(bot_msg)
        db.commit()
        yield {"event": "done", "data": json.dumps({"message_id": bot_msg.id, "meta": meta}, ensure_ascii=False)}

    async def safe_stream() -> AsyncIterator[dict]:
        # 스트림 중 예외가 나면 연결이 done 없이 끊겨 프론트 스피너가 무한 대기함 →
        # 항상 error 이벤트로 종료를 보장. 클라이언트 중단(취소)은 그대로 전파.
        try:
            async for ev in event_stream():
                yield ev
        except asyncio.CancelledError:
            raise
        except Exception:
            logging.exception("chat event_stream failed")
            yield {"event": "error", "data": json.dumps(
                {"message": "응답 생성 중 오류가 발생했습니다. 잠시 후 다시 시도해주세요."},
                ensure_ascii=False)}

    return EventSourceResponse(safe_stream())


# ── 추천 질문 (요청 3) ────────────────────────────────────────
class SuggestRequest(BaseModel):
    channel: str = "sales-analytics"
    dashboard: str = ""
    sheet: str = ""


_FALLBACK_QUESTIONS = ["카테고리별 매출 순위 보여줘", "월별 매출 추이는?", "가장 수익성 낮은 제품은?"]


@router.post("/suggest")
async def suggest_questions(req: SuggestRequest,
                            settings: Settings = Depends(get_settings),
                            gateway: TableauGateway = Depends(get_gateway)):
    """현재 대시보드/시트 기준 추천 질문 3개 (no-think, 실패 시 기본 질문)."""
    llm = LLMService(settings)
    fields = ""
    try:
        meta, _ = await asyncio.to_thread(
            gateway.read_metadata, gateway.resolve_ds_luid(None, None), settings.tableau_gateway_mode)
        fields = ", ".join(
            (f.get("fieldName") or f.get("fieldCaption") or "") for f in (meta.get("data") or [])[:25])
    except Exception:
        pass
    try:
        raw = await llm.complete(
            [{"role": "user", "content": SUGGEST_PROMPT.format(
                dashboard=req.dashboard or req.channel, sheet=req.sheet or "(전체)", fields=fields)}],
            model=settings.llm_router_model, think=False,
        )
        obj = _extract_json(raw) or {}
        qs = [q.strip() for q in obj.get("questions", []) if isinstance(q, str) and q.strip()][:3]
    except Exception:
        qs = []
    return {"questions": qs or _FALLBACK_QUESTIONS}


# ── 대시보드 요약 (요청 3: 시작 시 1회 미리 생성, 캐시) ──────
class SummaryRequest(BaseModel):
    channel: str = "sales-analytics"
    dashboard: str = ""
    sheets_context: Optional[List[Dict[str, Any]]] = None
    refresh: bool = False


_summary_cache: Dict[str, str] = {}   # (channel::dashboard) → 요약 텍스트 (프로세스 수명)


@router.post("/summary")
async def dashboard_summary(req: SummaryRequest,
                            settings: Settings = Depends(get_settings),
                            gateway: TableauGateway = Depends(get_gateway)):
    """대시보드 열람 시 1회 미리 생성하는 요약. 캐시 히트 시 즉시 반환.
    컨텍스트: 화면 시트 데이터 우선, 없으면 VDS 다각도 질의로 수집."""
    key = f"{req.channel}::{req.dashboard}"
    if not req.refresh and key in _summary_cache:
        return {"summary": _summary_cache[key], "cached": True}

    llm = LLMService(settings)
    context_parts: List[str] = []
    if req.sheets_context:
        context_parts.append(_sheets_context_text(req.sheets_context, None))
    elif settings.tableau_server:
        async for kind, payload in _vds_node(
                llm, gateway, settings,
                "이 대시보드의 전체 현황을 요약해줘 (총 매출/이익, 카테고리별, 월별 추이)", None):
            if kind == "result":
                ctx, _meta = payload
                if ctx:
                    context_parts.append(ctx)

    if not context_parts:
        return {"summary": "", "cached": False, "error": "no_context"}

    system = ("당신은 Tableau 대시보드 분석가입니다. 제공된 데이터에 근거해 한국어로 "
              "대시보드 요약을 작성하세요. 형식: 2~3문장 총평 후 핵심 지표·특이점 불릿 3개 이내. "
              "근거 없는 수치는 지어내지 마세요.")
    text = await llm.complete(
        [{"role": "system", "content": system},
         {"role": "user", "content": "\n\n".join(context_parts)
          + f"\n\n[요청]\n대시보드 '{req.dashboard or req.channel}'을 요약해줘"}],
        model=settings.llm_synthesis_model, think=False,
    )
    _summary_cache[key] = text
    return {"summary": text, "cached": False}


@router.get("/history/{channel_name}")
def history(channel_name: str, db: Session = Depends(get_db)):
    ch = db.query(Channel).filter(Channel.name == channel_name).first()
    if not ch:
        return {"channel": channel_name, "messages": []}
    msgs = (db.query(Message).filter(Message.channel_id == ch.id)
            .order_by(Message.created_at).limit(200).all())
    # 저장된 피드백 병합 (P1-2 — 새로고침 후에도 👍/👎 선택 상태 유지)
    fb_map: Dict[str, int] = {}
    if msgs:
        rows = db.query(Feedback.message_id, Feedback.rating).filter(
            Feedback.message_id.in_([m.id for m in msgs])).all()
        fb_map = {mid: int(r) for mid, r in rows}
    return {"channel": channel_name, "messages": [
        {"id": m.id, "role": m.role, "content": m.content,
         "thread_root_id": m.thread_root_id, "meta": json.loads(m.meta_json or "{}"),
         "feedback": fb_map.get(m.id, 0),
         "created_at": m.created_at.isoformat()} for m in msgs
    ]}
