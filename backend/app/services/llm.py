# services/llm.py — 모델 provider 추상화 (PRD §7.3: 로컬 우선, 외부 API 비활성 슬롯)
# v1: Ollama(로컬)만 활성. openai_* 설정이 채워지면 외부 슬롯 활성화 가능.
import json
from typing import AsyncIterator, Dict, List, Optional

import httpx

from app.config import Settings


class LLMService:
    def __init__(self, settings: Settings):
        self.s = settings

    # ── 논스트리밍 (라우터 노드 등 짧은 판단용, no-think) ────
    async def complete(self, messages: List[Dict[str, str]], model: Optional[str] = None,
                       think: bool = False) -> str:
        model = model or self.s.llm_router_model
        async with httpx.AsyncClient(timeout=120.0) as client:
            r = await client.post(f"{self.s.ollama_base_url}/api/chat", json={
                "model": model, "messages": messages, "stream": False, "think": think,
            })
            r.raise_for_status()
            return r.json().get("message", {}).get("content", "")

    # ── 스트리밍 (융합 합성 노드 → SSE로 전달) ───────────────
    async def stream(self, messages: List[Dict[str, str]], model: Optional[str] = None,
                     think: bool = False, num_ctx: Optional[int] = None) -> AsyncIterator[str]:
        model = model or self.s.llm_synthesis_model
        # 청크 간 read 타임아웃(120s) — 모델이 멈추면 무한 대기 대신 오류로 종료.
        # 긴 생성은 청크마다 타이머가 리셋되므로 문제없음.
        timeout = httpx.Timeout(60.0, connect=10.0, read=120.0)
        body: Dict[str, object] = {"model": model, "messages": messages, "stream": True, "think": think}
        if num_ctx:   # 컨텍스트 창 확대 — 화면 시트 데이터가 잘리지 않게 (Ollama 기본 ~4096)
            body["options"] = {"num_ctx": num_ctx}
        async with httpx.AsyncClient(timeout=timeout) as client:
            async with client.stream("POST", f"{self.s.ollama_base_url}/api/chat", json=body) as r:
                r.raise_for_status()
                async for line in r.aiter_lines():
                    if not line:
                        continue
                    try:
                        obj = json.loads(line)
                    except Exception:
                        continue
                    chunk = obj.get("message", {}).get("content", "")
                    if chunk:
                        yield chunk
                    if obj.get("done"):
                        break
