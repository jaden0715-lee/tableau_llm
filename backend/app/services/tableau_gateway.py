# services/tableau_gateway.py — 기존 api/main.py의 VDS+MCP 게이트웨이 이관 (검증된 자산)
# 변경점: 전역 os.getenv → Settings 주입, requests → httpx, 로직은 동일 유지
import json
import time
from datetime import date
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import quote

import httpx
from fastapi import HTTPException

from app.config import Settings


class TableauGateway:
    """VDS 직접 호출 + MCP(JSON-RPC) 폴백 게이트웨이.

    - GATEWAY_MODE: VDS_ONLY | MCP_ONLY | AUTO (요청별 X-Tableau-Backend 헤더로 오버라이드)
    - VDS 스키마 차이(QUANTITATIVE_DATE ↔ CATEGORICAL 변형 등)를 후보 payload로 흡수
    """

    def __init__(self, settings: Settings):
        self.s = settings
        self._token_cache: Dict[str, Any] = {"token": None, "site_id": None, "expires_at": 0}
        self._tools: Dict[str, str] = {"read_metadata": "", "list_fields": "", "query_datasource": ""}
        self._tools_discovered_at = 0
        self._client = httpx.Client(verify=self.s.tableau_verify, timeout=60.0)
        self._mcp_client = httpx.Client(verify=self.s.mcp_verify, timeout=self.s.mcp_timeout)
        self._view_luid_cache: Dict[str, str] = {}          # embed 경로 → view LUID
        self._view_image_cache: Dict[str, bytes] = {}        # (luid|filters) → PNG 바이트

    # ── 모드 결정 ────────────────────────────────────────────
    def pick_mode(self, header_override: Optional[str]) -> str:
        ov = (header_override or "").upper().strip()
        if ov in ("VDS", "MCP", "AUTO"):
            return {"VDS": "VDS_ONLY", "MCP": "MCP_ONLY", "AUTO": "AUTO"}[ov]
        return self.s.tableau_gateway_mode

    def resolve_ds_luid(self, param_luid: Optional[str], param_name: Optional[str]) -> str:
        if param_luid:
            return param_luid
        if param_name:
            ds_map = self.s.ds_map
            if param_name not in ds_map:
                raise HTTPException(400, f"Unknown ds name: {param_name}")
            return ds_map[param_name]
        if not self.s.tableau_datasource_luid:
            raise HTTPException(400, "datasourceLuid not provided and no default set.")
        return self.s.tableau_datasource_luid

    # ── PAT 인증 ─────────────────────────────────────────────
    def _sign_in_with_pat(self) -> None:
        now = int(time.time())
        if self._token_cache["token"] and self._token_cache["expires_at"] - now > 60:
            return
        if not self.s.tableau_server_base:
            raise HTTPException(500, "TABLEAU_SERVER is not set")
        signin_url = f"{self.s.tableau_server_base}/api/3.22/auth/signin"
        payload = {
            "credentials": {
                "personalAccessTokenName": self.s.tableau_pat_name,
                "personalAccessTokenSecret": self.s.tableau_pat_secret,
                "site": {"contentUrl": self.s.tableau_site_content_url or self.s.tableau_site or ""},
            }
        }
        r = self._client.post(
            signin_url, json=payload,
            headers={"Accept": "application/json", "Content-Type": "application/json"},
            follow_redirects=False,
        )
        if 300 <= r.status_code < 400:
            raise HTTPException(401, f"PAT sign-in redirected (status={r.status_code}, Location={r.headers.get('Location', '')}).")
        try:
            j = r.json()
        except Exception:
            raise HTTPException(r.status_code or 500, f"Non-JSON response (status={r.status_code}). Body: {(r.text or '')[:400]}")
        if r.status_code != 200 or "credentials" not in j:
            raise HTTPException(401, f"PAT sign-in failed: {j}")
        self._token_cache["token"] = j["credentials"]["token"]
        self._token_cache["site_id"] = j["credentials"]["site"]["id"]
        self._token_cache["expires_at"] = int(time.time()) + 110 * 60

    def _auth_headers(self) -> Dict[str, str]:
        if self.s.auth_mode != "PAT":
            raise HTTPException(400, "Direct VDS call supports PAT only. Use MCP for JWT.")
        self._sign_in_with_pat()
        return {"X-Tableau-Auth": self._token_cache["token"]}

    def current_username(self) -> str:
        """PAT 사용자의 사이트 username(이메일) 조회 — Connected App JWT sub 자동 감지용."""
        self._sign_in_with_pat()
        r = self._client.get(
            f"{self.s.tableau_server_base}/api/3.22/sessions/current",
            headers={**self._auth_headers(), "Accept": "application/json"},
        )
        if r.status_code == 200:
            try:
                return r.json()["session"]["user"]["name"]
            except Exception:
                pass
        raise HTTPException(500, "Tableau 사용자 자동 감지 실패 — .env의 TABLEAU_USER를 직접 설정하세요.")

    # ── REST 뷰 이미지 (답변 근거 화면 캡처, 표시용) ──────────
    def resolve_view_luid(self, embed_path: str) -> str:
        """embed 경로(예 'Superstore/Overview') → 뷰 LUID.

        REST contentUrl은 'Workbook/sheets/View' 형식이라 변환 후 사이트 뷰 목록에서 매칭.
        결과는 프로세스 수명 동안 캐시.
        """
        embed_path = embed_path.strip().strip("/")
        if embed_path in self._view_luid_cache:
            return self._view_luid_cache[embed_path]
        parts = embed_path.split("/")
        if len(parts) < 2:
            raise HTTPException(400, f"뷰 경로 형식 오류: {embed_path!r} (Workbook/View 필요)")
        target = f"{parts[0]}/sheets/{parts[-1]}"

        self._sign_in_with_pat()
        base = self.s.tableau_server_base
        site_id = self._token_cache["site_id"]

        def _list() -> httpx.Response:
            return self._client.get(
                f"{base}/api/3.22/sites/{site_id}/views",
                headers={**self._auth_headers(), "Accept": "application/json"},
                params={"pageSize": 1000},
            )

        r = _list()
        if r.status_code == 401:
            self._token_cache["token"] = None
            self._sign_in_with_pat()
            r = _list()
        if r.status_code >= 400:
            raise HTTPException(r.status_code, f"뷰 목록 조회 실패: {(r.text or '')[:300]}")
        views = (r.json().get("views", {}) or {}).get("view", [])
        for v in views:
            if v.get("contentUrl") == target:
                self._view_luid_cache[embed_path] = v["id"]
                return v["id"]
        raise HTTPException(404, f"뷰를 찾을 수 없음: {target}")

    def get_view_image(self, view_luid: str, vf_filters: Optional[Dict[str, str]] = None,
                       resolution: str = "high") -> bytes:
        """뷰를 서버 렌더링한 PNG 바이트. vf_filters는 CATEGORICAL 필터 반영(vf_<field>=<value>)."""
        vf_filters = vf_filters or {}
        cache_key = f"{view_luid}|{resolution}|" + "&".join(
            f"{k}={v}" for k, v in sorted(vf_filters.items()))
        if cache_key in self._view_image_cache:
            return self._view_image_cache[cache_key]

        self._sign_in_with_pat()
        base = self.s.tableau_server_base
        site_id = self._token_cache["site_id"]
        qs = f"resolution={quote(resolution)}&maxAge=1"
        for k, v in vf_filters.items():
            qs += f"&vf_{quote(str(k))}={quote(str(v))}"
        url = f"{base}/api/3.22/sites/{site_id}/views/{view_luid}/image?{qs}"

        def _get() -> httpx.Response:
            # Accept: image/png 은 Tableau가 406으로 거부 → */* 사용
            return self._client.get(url, headers={**self._auth_headers(), "Accept": "*/*"})

        r = _get()
        if r.status_code == 401:
            self._token_cache["token"] = None
            self._sign_in_with_pat()
            r = _get()
        if r.status_code >= 400:
            raise HTTPException(r.status_code, f"뷰 이미지 렌더 실패: {(r.text or '')[:300]}")
        img = r.content
        # 소량 캐시 (최근 32개)
        if len(self._view_image_cache) > 32:
            self._view_image_cache.clear()
        self._view_image_cache[cache_key] = img
        return img

    # ── Direct VDS ───────────────────────────────────────────
    def _call_vds_direct(self, path: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        url = f"{self.s.tableau_server_base}{path}"
        r = self._client.post(url, headers={**self._auth_headers(), "Accept": "application/json"}, json=payload)
        if r.status_code == 401:  # 토큰 만료 재시도
            self._token_cache["token"] = None
            self._sign_in_with_pat()
            r = self._client.post(url, headers={**self._auth_headers(), "Accept": "application/json"}, json=payload)
        if r.status_code >= 400:
            raise HTTPException(status_code=r.status_code, detail=r.text)
        return r.json()

    def _post_vds(self, url: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        r = self._client.post(url, headers={**self._auth_headers(), "Accept": "application/json"}, json=payload)
        ct = r.headers.get("content-type", "")
        # Tableau Cloud VDS는 Content-Type 헤더 없이 JSON을 반환하므로 CT와 무관하게 파싱 시도
        parsed = None
        if r.text:
            try:
                parsed = r.json()
            except Exception:
                parsed = None
        return {"status": r.status_code, "ct": ct, "text": r.text, "json": parsed}

    # ── MCP(JSON-RPC) ────────────────────────────────────────
    def _mcp_jsonrpc(self, payload: Dict[str, Any]) -> Tuple[Dict[str, Any], Dict[str, Any]]:
        headers = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream"}
        with self._mcp_client.stream("POST", self.s.mcp_endpoint, json=payload, headers=headers) as r:
            ct = r.headers.get("content-type", "")
            if r.status_code >= 400:
                r.read()
                raise HTTPException(status_code=r.status_code, detail=(r.text or "")[:500] or "MCP error")
            if ct.startswith("text/event-stream"):
                last_json = None
                for line in r.iter_lines():
                    if line.startswith("data:"):
                        data_str = line[5:].strip()
                        if not data_str or data_str == "[DONE]":
                            continue
                        try:
                            last_json = json.loads(data_str)
                        except Exception:
                            pass
                if last_json is None:
                    raise HTTPException(502, "No JSON data in SSE response.")
                return last_json, {"status": r.status_code, "ct": ct}
            r.read()
            try:
                return r.json(), {"status": r.status_code, "ct": ct}
            except Exception:
                raise HTTPException(502, f"Invalid JSON response: {(r.text or '')[:500]}")

    def _discover_tools(self) -> None:
        resp, _ = self._mcp_jsonrpc({"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
        tools = (resp.get("result") or {}).get("tools") or []
        names = {t.get("name", "") for t in tools if isinstance(t, dict)}

        def pick(cands: List[str]) -> str:
            return next((c for c in cands if c in names), "")

        self._tools["read_metadata"] = pick(["read-metadata", "get-datasource-metadata", "vds.readMetadata", "readMetadata"])
        self._tools["list_fields"] = pick(["list-fields", "metadata.listFields"])
        self._tools["query_datasource"] = pick(["query-datasource", "vds.queryDatasource", "queryDatasource"])
        self._tools_discovered_at = int(time.time())

    def ensure_tools(self) -> None:
        if not self.s.use_mcp:
            return
        now = int(time.time())
        if (now - self._tools_discovered_at) > 600 or not self._tools["read_metadata"] or not self._tools["query_datasource"]:
            self._discover_tools()

    @property
    def tools(self) -> Dict[str, str]:
        return dict(self._tools)

    def mcp_tools_call(self, name: str, arguments: Dict[str, Any]) -> Dict[str, Any]:
        req = {
            "jsonrpc": "2.0",
            "id": int(time.time() * 1000) % 10_000_000,
            "method": "tools/call",
            "params": {"name": name, "arguments": arguments},
        }
        resp, _ = self._mcp_jsonrpc(req)
        if "error" in resp:
            err = resp["error"]
            raise HTTPException(502, f"MCP error {err.get('code', -32000)}: {err.get('message', 'MCP tool error')}")
        result = resp.get("result")
        if result is None:
            raise HTTPException(502, "MCP response has no result")
        return result

    def guard_tool_call(self, name: str, arguments: Dict[str, Any]) -> None:
        allowed = self.s.mcp_allowed_tools_list
        if allowed and name not in allowed:
            raise HTTPException(403, f"Tool '{name}' not allowed")
        try:
            size = len(json.dumps(arguments, ensure_ascii=False).encode("utf-8"))
        except Exception:
            size = 0
        if size > self.s.mcp_max_tool_args_bytes:
            raise HTTPException(413, f"Arguments too large: {size} > {self.s.mcp_max_tool_args_bytes} bytes")

    # ── 메타데이터 (AUTO: MCP→VDS 폴백) ─────────────────────
    def read_metadata(self, ds_luid: str, mode: str) -> Tuple[Dict[str, Any], str]:
        if self.s.use_mcp and mode in ("MCP_ONLY", "AUTO"):
            try:
                self.ensure_tools()
                tool = self._tools.get("read_metadata") or ""
                if not tool:
                    raise HTTPException(503, "MCP tool 'read-metadata' not available")
                result = self.mcp_tools_call(tool, {"datasourceLuid": ds_luid})
                return (result.get("data") or result), "MCP"
            except Exception:
                if mode == "MCP_ONLY":
                    raise
        payload = {"datasource": {"datasourceLuid": ds_luid}}
        return self._call_vds_direct("/api/v1/vizql-data-service/read-metadata", payload), "VDS"

    # ── 쿼리 payload 변형 생성 (스키마 차이 흡수) ────────────
    @staticmethod
    def _to_dict_list(objs) -> List[Dict[str, Any]]:
        out = []
        for o in objs or []:
            if hasattr(o, "model_dump"):
                out.append(o.model_dump(exclude_none=True))
            elif isinstance(o, dict):
                out.append({k: v for k, v in o.items() if v is not None})
            else:
                out.append(dict(o))
        return out

    @staticmethod
    def _parse_date_range_str(s: str) -> Tuple[str, str]:
        s = (s or "").strip().strip('"').strip("'")
        if ":" in s:
            a, b = s.split(":", 1)
            return a.strip(), b.strip()
        return s, s

    @staticmethod
    def _to_date(s: str) -> date:
        y, m, d = s.split("-")
        return date(int(y), int(m), int(d))

    @classmethod
    def _year_members(cls, start_str: str, end_str: str) -> List[str]:
        a, b = cls._to_date(start_str), cls._to_date(end_str)
        return [str(y) for y in range(a.year, b.year + 1)]

    @classmethod
    def _month_members(cls, start_str: str, end_str: str) -> List[str]:
        a, b = cls._to_date(start_str), cls._to_date(end_str)
        y, m = a.year, a.month
        members = []
        while (y < b.year) or (y == b.year and m <= b.month):
            members.append(f"{y:04d}-{m:02d}")
            m += 1
            if m == 13:
                y, m = y + 1, 1
        return members

    @staticmethod
    def _normalize_categorical_base(f: Dict[str, Any]) -> Dict[str, Any]:
        g = dict(f)
        g.pop("operator", None)
        return g

    @staticmethod
    def _categorical_variants_from_members(base: Dict[str, Any], members: List[str]) -> List[Dict[str, Any]]:
        v1 = {**base, "values": members}
        v2 = {**base, "operator": "MEMBER_IN", "values": members}
        v3 = {**base, "members": [{"value": m} for m in members]}
        v3.pop("values", None)
        return [v1, v2, v3]

    def _categoricalize_date_filter(self, f: Dict[str, Any], fields_ctx: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        field_info = dict(f.get("field") or {})
        field_caption = field_info.get("fieldCaption")
        dl = (field_info.get("dateLevel") or "").upper()
        if not dl and field_caption:
            for fld in fields_ctx or []:
                if fld.get("fieldCaption") == field_caption and fld.get("dateLevel"):
                    dl = str(fld["dateLevel"]).upper()
                    break
        if dl not in ("YEAR", "MONTH"):
            dl = "YEAR"
        vals = list(f.get("values") or [])
        vmin = vmax = None
        if vals and isinstance(vals[0], str):
            vmin, vmax = self._parse_date_range_str(vals[0])
        if not (vmin and vmax):
            return []
        members = self._year_members(vmin, vmax) if dl == "YEAR" else self._month_members(vmin, vmax)
        base = {"field": {**field_info, "dateLevel": dl}, "filterType": "CATEGORICAL"}
        return self._categorical_variants_from_members(base, members)

    @staticmethod
    def _has_quantitative_keys(f: Dict[str, Any]) -> bool:
        keys = {k.lower() for k in f.keys()}
        return any(k in keys for k in ("quantitativefiltertype", "mindate", "maxdate", "min", "max", "start", "end", "range", "ranges"))

    def _filters_variants(self, filters: Optional[List[Dict[str, Any]]], fields_ctx: List[Dict[str, Any]]) -> List[List[Dict[str, Any]]]:
        base = self._to_dict_list(filters)
        if not base:
            return [[]]

        date_idx = next((i for i, f in enumerate(base) if (f.get("filterType") or "").upper() == "QUANTITATIVE_DATE"), None)

        if date_idx is None:
            normalized = [
                self._normalize_categorical_base(f) if (f.get("filterType") or "").upper() == "CATEGORICAL" else f
                for f in base
            ]
            return [normalized]

        fixed = [
            self._normalize_categorical_base(f) if (f.get("filterType") or "").upper() == "CATEGORICAL" else f
            for j, f in enumerate(base) if j != date_idx
        ]
        date_f = dict(base[date_idx])

        if self._has_quantitative_keys(date_f):
            variants = [dict(date_f)]
            if "minDate" in date_f or "maxDate" in date_f:
                v1 = dict(date_f)
                if "minDate" in v1:
                    v1["min"] = v1.pop("minDate")
                if "maxDate" in v1:
                    v1["max"] = v1.pop("maxDate")
                variants.append(v1)
            if "min" in date_f or "max" in date_f:
                v2 = dict(date_f)
                rng = {}
                if "min" in v2:
                    rng["min"] = v2.pop("min")
                if "max" in v2:
                    rng["max"] = v2.pop("max")
                v2["range"] = rng
                variants.append(v2)
            if "range" in date_f and isinstance(date_f["range"], dict):
                v3 = dict(date_f)
                v3["ranges"] = [v3.pop("range")]
                variants.append(v3)
            out = []
            for dv in variants:
                dv.pop("operator", None)
                dv.pop("values", None)
                out.append(fixed + [dv])
            return out

        date_variants = self._categoricalize_date_filter(date_f, fields_ctx)
        if not date_variants:
            return [base]
        return [fixed + [dv] for dv in date_variants]

    def build_query_candidates(self, ds_luid: str, fields, filters, options) -> List[Dict[str, Any]]:
        fields_d = self._to_dict_list(fields)
        opts = (options or {"returnFormat": "OBJECTS", "disaggregate": False}).copy()
        opts.pop("limit", None)
        return [
            {
                "datasource": {"datasourceLuid": ds_luid},
                "query": {"fields": fields_d, "filters": vf},
                "options": opts.copy(),
            }
            for vf in self._filters_variants(self._to_dict_list(filters), fields_d)
        ]

    # ── 쿼리 (AUTO: MCP→VDS 폴백) ────────────────────────────
    def query(self, ds_luid: str, fields, filters, options, limit: Optional[int], mode: str) -> Tuple[Dict[str, Any], str]:
        candidates = self.build_query_candidates(ds_luid, fields, filters, options)

        if self.s.use_mcp and mode in ("MCP_ONLY", "AUTO"):
            try:
                self.ensure_tools()
                tool = self._tools.get("query_datasource") or ""
                if not tool:
                    raise HTTPException(503, "MCP tool 'query-datasource' not available")
                for cand in candidates:
                    args: Dict[str, Any] = {
                        "datasourceLuid": ds_luid,
                        "query": {"fields": cand["query"]["fields"], "filters": cand["query"]["filters"]},
                    }
                    # MCP 스키마: options/limit는 query 바깥(최상위)
                    if cand.get("options"):
                        args["options"] = cand["options"]
                    if limit is not None:
                        args["limit"] = limit
                    res = self.mcp_tools_call(tool, args)
                    if limit and isinstance(res, dict) and isinstance(res.get("data"), list):
                        res = {**res, "data": res["data"][:limit]}
                    return res, "MCP"
            except Exception:
                if mode == "MCP_ONLY":
                    raise

        modern_url = f"{self.s.tableau_server_base}/api/v1/vizql-data-service/query-datasource"
        last = None
        for payload in candidates:
            r = self._post_vds(modern_url, payload)
            last = r
            if r["status"] == 200 and r["json"] is not None:
                ok = r["json"]
                if limit and isinstance(ok, dict) and isinstance(ok.get("data"), list):
                    ok = {**ok, "data": ok["data"][:limit]}
                return ok, "VDS"

        raise HTTPException(400, (last and last.get("text")) or "VDS/MCP query failed")
