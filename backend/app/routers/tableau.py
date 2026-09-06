# routers/tableau.py — Tableau 게이트웨이 엔드포인트 (기존 api/main.py 계약 유지)
import datetime as dt
import uuid
from typing import Any, Dict, List, Optional, Union

import jwt
from fastapi import APIRouter, Body, Depends, HTTPException, Query, Request, Response
from pydantic import BaseModel, ConfigDict, Field

from app.config import Settings, get_settings
from app.services.tableau_gateway import TableauGateway

router = APIRouter(prefix="/api/tableau", tags=["tableau"])

_gateway: Optional[TableauGateway] = None


def get_gateway(settings: Settings = Depends(get_settings)) -> TableauGateway:
    global _gateway
    if _gateway is None:
        _gateway = TableauGateway(settings)
    return _gateway


# ── 스키마 (기존 계약 그대로) ────────────────────────────────
class VdsField(BaseModel):
    fieldCaption: str
    maxDecimalPlaces: Optional[int] = None
    sortDirection: Optional[str] = None
    sortPriority: Optional[int] = None
    function: Optional[str] = None
    dateLevel: Optional[str] = None
    model_config = ConfigDict(extra="allow")


class VdsFilter(BaseModel):
    field: Dict[str, Any]
    filterType: str
    operator: Optional[str] = None
    values: Optional[List[Any]] = None
    quantitativeFilterType: Optional[str] = None
    minDate: Optional[str] = None
    maxDate: Optional[str] = None
    min: Optional[Union[int, float, str]] = None
    max: Optional[Union[int, float, str]] = None
    start: Optional[Union[int, float, str]] = None
    end: Optional[Union[int, float, str]] = None
    range: Optional[Dict[str, Any]] = None
    ranges: Optional[List[Dict[str, Any]]] = None
    members: Optional[List[Dict[str, Any]]] = None
    model_config = ConfigDict(extra="allow")


class VdsQueryPayload(BaseModel):
    datasourceLuid: Optional[str] = None
    ds: Optional[str] = None
    fields: List[VdsField]
    filters: Optional[List[VdsFilter]] = []
    options: Optional[Dict[str, Any]] = {"returnFormat": "OBJECTS", "disaggregate": False}
    limit: Optional[int] = None


class McpToolCallBody(BaseModel):
    name: str = Field(..., description="MCP tool name (e.g., 'query-datasource')")
    arguments: Dict[str, Any] = Field(default_factory=dict)


# ── 엔드포인트 ───────────────────────────────────────────────
@router.get("/metadata")
def read_metadata(
    request: Request,
    response: Response,
    ds: Optional[str] = Query(default=None),
    luid: Optional[str] = Query(default=None),
    gw: TableauGateway = Depends(get_gateway),
):
    ds_luid = gw.resolve_ds_luid(luid, ds)
    mode = gw.pick_mode(request.headers.get("X-Tableau-Backend"))
    result, used = gw.read_metadata(ds_luid, mode)
    response.headers["X-Gateway"] = used
    return result


@router.post("/query")
def query_datasource(
    request: Request,
    response: Response,
    q: VdsQueryPayload = Body(...),
    gw: TableauGateway = Depends(get_gateway),
):
    ds_luid = gw.resolve_ds_luid(q.datasourceLuid, q.ds)
    mode = gw.pick_mode(request.headers.get("X-Tableau-Backend"))
    result, used = gw.query(ds_luid, q.fields, q.filters, q.options, q.limit, mode)
    response.headers["X-Gateway"] = used
    return result


# ── Connected App (Direct Trust) 무로그인 임베딩 토큰 ────────
_username_cache: Dict[str, str] = {}


@router.get("/embed-token")
def embed_token(gw: TableauGateway = Depends(get_gateway),
                settings: Settings = Depends(get_settings)):
    """Tableau Embedding API v3용 Connected App JWT 발급.

    - 서명: HS256, Secret Value
    - 유효기간: 5분 (Tableau 최대 10분) — 토큰은 viz 로드 시 1회 사용
    - sub: TABLEAU_USER (비어 있으면 PAT 사용자 자동 감지 후 캐시)
    """
    if not (settings.tableau_ca_client_id and settings.tableau_ca_secret_id and settings.tableau_ca_secret_value):
        raise HTTPException(503, "Connected App 미설정 — .env에 TABLEAU_CA_CLIENT_ID/SECRET_ID/SECRET_VALUE 필요")

    username = settings.tableau_user or _username_cache.get("u")
    if not username:
        username = gw.current_username()
        _username_cache["u"] = username

    now = dt.datetime.now(dt.timezone.utc)
    token = jwt.encode(
        {
            "iss": settings.tableau_ca_client_id,
            "exp": now + dt.timedelta(minutes=5),
            "jti": str(uuid.uuid4()),
            "aud": "tableau",
            "sub": username,
            "scp": ["tableau:views:embed"],
        },
        settings.tableau_ca_secret_value,
        algorithm="HS256",
        headers={"kid": settings.tableau_ca_secret_id, "iss": settings.tableau_ca_client_id},
    )
    return {"token": token, "username": username, "expires_in": 300}


@router.get("/view-image")
def view_image(
    view: str = Query(..., description="embed 경로 suffix (예: 'Superstore/Overview')"),
    vf: Optional[str] = Query(default=None, description="CATEGORICAL 필터 JSON: {\"Region\":\"West\"}"),
    resolution: str = Query(default="high"),
    gw: TableauGateway = Depends(get_gateway),
):
    """답변 근거용 뷰 캡처 PNG. AI 적용 필터(CATEGORICAL)를 vf로 받아 반영."""
    import json as _json

    filters: Dict[str, str] = {}
    if vf:
        try:
            raw = _json.loads(vf)
            if isinstance(raw, dict):
                filters = {str(k): str(v) for k, v in raw.items() if v not in (None, "")}
        except Exception:
            filters = {}
    luid = gw.resolve_view_luid(view)
    png = gw.get_view_image(luid, filters, resolution=resolution)
    return Response(content=png, media_type="image/png",
                    headers={"Cache-Control": "private, max-age=300"})


@router.get("/mcp/tools")
def mcp_tools(response: Response, gw: TableauGateway = Depends(get_gateway),
              settings: Settings = Depends(get_settings)):
    if not settings.use_mcp:
        raise HTTPException(503, "MCP is disabled")
    gw.ensure_tools()
    response.headers["X-Gateway"] = "MCP"
    return {"tools": gw.tools, "endpoint": settings.mcp_endpoint}


@router.post("/mcp/tools/call")
def mcp_tools_call(response: Response, body: McpToolCallBody,
                   gw: TableauGateway = Depends(get_gateway),
                   settings: Settings = Depends(get_settings)):
    if not settings.use_mcp:
        raise HTTPException(503, "MCP is disabled")
    gw.ensure_tools()
    gw.guard_tool_call(body.name, body.arguments)
    response.headers["X-Gateway"] = "MCP"
    return {"isError": False, "result": gw.mcp_tools_call(body.name, body.arguments)}
