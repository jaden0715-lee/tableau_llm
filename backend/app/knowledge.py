# app/knowledge.py — 지식 도메인 레지스트리 + L2 에이전틱-라이트 선택
# 설계: docs/KNOWLEDGE_ROUTING_DESIGN.md
# - 도메인 "설명"은 코드에 고정(안정적), dataset ID는 .env에서 주입.
# - KB 추가 = 아래 _DOMAINS 한 줄 + .env 한 줄 (프롬프트/분기 수정 불필요 = L2의 확장성).
import json
import re
from typing import Any, Dict, List, Optional, Tuple

from app.config import Settings

# (key, 설명, settings 속성명) — 순서는 카탈로그 노출 순서
_DOMAINS = [
    ("전략", "할인·마진·가격 정책, 수익성 전략, 중장기 방향, 경영 판단 근거", "dify_dataset_strategy"),
    ("일반", "카테고리·시장 트렌드, 이커머스 동향, 물류·배송·공급망 등 일반 지식", "dify_dataset_general"),
]


def kb_registry(s: Settings) -> List[Dict[str, str]]:
    """설정된(=dataset ID가 있는) 도메인만 [{key, id, desc}] 로 반환.
    '일반'은 dify_dataset_general 이 비면 dify_default_dataset_id 로 폴백."""
    out: List[Dict[str, str]] = []
    for key, desc, attr in _DOMAINS:
        ds = getattr(s, attr, "") or ""
        if not ds and key == "일반":
            ds = s.dify_default_dataset_id or ""
        if ds:
            out.append({"key": key, "id": ds, "desc": desc})
    return out


SELECT_PROMPT = """질문에 답하려면 아래 지식베이스 중 무엇을 검색해야 하는지 고르세요.

[지식베이스]
{catalog}

규칙:
- 질문과 관련된 것만 고르세요. 여러 개 관련되면 여러 개 고르세요.
- 관련된 지식베이스가 하나도 없으면 datasets 는 빈 배열 [].
- 반드시 JSON 한 줄만 출력. 형식: {{"datasets": ["전략"], "reason": "한 문장 이유"}}

[질문] {query}"""


def build_select_prompt(registry: List[Dict[str, str]], query: str) -> str:
    catalog = "\n".join(f'- {e["key"]}: {e["desc"]}' for e in registry)
    return SELECT_PROMPT.format(catalog=catalog, query=query)


def parse_selection(raw: str, registry: List[Dict[str, str]]) -> Tuple[List[str], str]:
    """LLM 출력(JSON)에서 (선택된 key 목록, 이유) 파싱. 견고 파싱 — 실패 시 빈 선택."""
    valid = {e["key"] for e in registry}
    keys: List[str] = []
    reason = ""
    m = re.search(r"\{.*\}", raw, re.DOTALL)
    if m:
        try:
            obj = json.loads(m.group(0))
            reason = str(obj.get("reason") or "").strip()
            for k in obj.get("datasets") or []:
                if isinstance(k, str) and k.strip() in valid and k.strip() not in keys:
                    keys.append(k.strip())
        except (json.JSONDecodeError, TypeError):
            pass
    # JSON 파싱 실패 시 텍스트에서 도메인 키 언급이라도 회수
    if not keys:
        for k in valid:
            if k in raw:
                keys.append(k)
    return keys, reason


def resolve_scope(
    registry: List[Dict[str, str]], scope: Optional[str]
) -> Optional[List[Dict[str, str]]]:
    """UI 강제선택(scope) 해석. 반환 None = '자동(LLM 선택)', 그 외 = 확정된 도메인 목록.
    scope: auto/자동/'' → None | 전체/all → 전부 | '전략'/'일반' → 해당."""
    s = (scope or "").strip()
    if s in ("", "auto", "자동"):
        return None
    if s in ("전체", "all", "ALL"):
        return list(registry)
    picked = [e for e in registry if e["key"] == s]
    return picked if picked else list(registry)  # 알 수 없는 값이면 전체로 안전 폴백
