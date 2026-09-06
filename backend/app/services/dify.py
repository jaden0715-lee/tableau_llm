# services/dify.py — Dify 지식 계층 클라이언트 (PRD §7.2)
# Dataset API(문서 관리) + Retrieval API(검색)만 사용. 채팅 오케스트레이션은 LangGraph가 담당.
from typing import Any, Dict, List, Optional

import httpx
from fastapi import HTTPException

from app.config import Settings


class DifyClient:
    def __init__(self, settings: Settings):
        self.s = settings
        self._client = httpx.Client(
            base_url=settings.dify_base_url.rstrip("/"),
            headers={"Authorization": f"Bearer {settings.dify_dataset_api_key}"},
            timeout=120.0,
        )

    def _check(self, r: httpx.Response) -> Dict[str, Any]:
        if r.status_code >= 400:
            raise HTTPException(status_code=502, detail=f"Dify error {r.status_code}: {(r.text or '')[:400]}")
        return r.json()

    # ── Dataset(지식베이스) 관리 ────────────────────────────
    def list_datasets(self) -> List[Dict[str, Any]]:
        return self._check(self._client.get("/v1/datasets", params={"limit": 100})).get("data", [])

    def create_dataset(self, name: str) -> Dict[str, Any]:
        return self._check(self._client.post("/v1/datasets", json={
            "name": name, "permission": "only_me", "indexing_technique": "high_quality",
        }))

    # ── 문서 색인 ────────────────────────────────────────────
    def create_document_by_text(self, dataset_id: str, name: str, text: str) -> Dict[str, Any]:
        return self._check(self._client.post(f"/v1/datasets/{dataset_id}/document/create-by-text", json={
            "name": name, "text": text,
            "indexing_technique": "high_quality",
            "process_rule": {"mode": "automatic"},
        }))

    def create_document_by_file(self, dataset_id: str, filename: str, content: bytes) -> Dict[str, Any]:
        data = {"data": (
            '{"indexing_technique":"high_quality","process_rule":{"mode":"automatic"}}'
        )}
        files = {"file": (filename, content)}
        return self._check(self._client.post(f"/v1/datasets/{dataset_id}/document/create-by-file", data=data, files=files))

    def indexing_status(self, dataset_id: str, batch: str) -> Dict[str, Any]:
        j = self._check(self._client.get(f"/v1/datasets/{dataset_id}/documents/{batch}/indexing-status"))
        docs = j.get("data") or []
        return docs[0] if docs else {"indexing_status": "unknown"}

    def delete_document(self, dataset_id: str, document_id: str) -> None:
        r = self._client.delete(f"/v1/datasets/{dataset_id}/documents/{document_id}")
        if r.status_code >= 400:
            raise HTTPException(502, f"Dify delete error {r.status_code}")

    # ── 검색 (LangGraph 문서질의/융합질의 노드가 호출) ───────
    def retrieve(self, dataset_id: str, query: str, top_k: int = 4,
                 score_threshold: Optional[float] = None) -> List[Dict[str, Any]]:
        retrieval_model: Dict[str, Any] = {
            "search_method": "semantic_search",
            "reranking_enable": False,
            "top_k": top_k,
            "score_threshold_enabled": score_threshold is not None,
        }
        if score_threshold is not None:
            retrieval_model["score_threshold"] = score_threshold
        j = self._check(self._client.post(f"/v1/datasets/{dataset_id}/retrieve", json={
            "query": query, "retrieval_model": retrieval_model,
        }))
        out = []
        for rec in j.get("records", []):
            seg = rec.get("segment", {})
            out.append({
                "score": rec.get("score"),
                "content": seg.get("content", ""),
                "document_name": (seg.get("document") or {}).get("name", ""),
                "document_id": (seg.get("document") or {}).get("id", ""),
            })
        return out

    def retrieve_many(self, dataset_ids: List[str], query: str, top_k: int = 4,
                      score_threshold: Optional[float] = None) -> List[Dict[str, Any]]:
        """여러 dataset을 각각 검색해 score로 병합, 전역 top_k 반환 (L2 팬아웃/다중선택).
        한 dataset이 실패해도 나머지는 진행. 결과에 dataset_id 태깅."""
        merged: List[Dict[str, Any]] = []
        seen: set = set()
        for ds in dataset_ids:
            if not ds:
                continue
            try:
                recs = self.retrieve(ds, query, top_k=top_k, score_threshold=score_threshold)
            except HTTPException:
                continue  # 개별 dataset 오류는 무시하고 나머지 검색 지속
            for c in recs:
                key = (c.get("document_name", ""), (c.get("content") or "")[:80])
                if key in seen:
                    continue
                seen.add(key)
                c["dataset_id"] = ds
                merged.append(c)
        merged.sort(key=lambda c: c.get("score") or 0, reverse=True)
        return merged[:top_k]
