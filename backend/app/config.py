# app/config.py — Pydantic Settings (기존 config.py + api/main.py 환경변수 이관)
from functools import lru_cache
from typing import Dict, Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # ── Tableau ─────────────────────────────────────────────
    tableau_server: str = ""                    # TABLEAU_SERVER
    tableau_site: str = ""                      # TABLEAU_SITE
    tableau_site_content_url: str = ""          # TABLEAU_SITE_CONTENT_URL
    auth_mode: Literal["PAT", "JWT"] = "PAT"    # 직접 VDS는 PAT만
    tableau_pat_name: str = ""
    tableau_pat_secret: str = ""
    tableau_datasource_luid: str = ""           # 기본 LUID
    tableau_datasource_map: str = ""            # "name=luid,name2=luid2"
    tableau_ssl_verify: bool = True
    tableau_ca_bundle: str = ""
    tableau_gateway_mode: Literal["VDS_ONLY", "MCP_ONLY", "AUTO"] = "AUTO"
    vds_debug: bool = False

    # ── Tableau Connected App (Direct Trust) — 무로그인 임베딩 JWT ──
    tableau_ca_client_id: str = ""       # Connected App Client ID
    tableau_ca_secret_id: str = ""       # Secret ID
    tableau_ca_secret_value: str = ""    # Secret Value
    tableau_user: str = ""               # JWT sub (사이트 사용자 이메일). 비우면 PAT 사용자로 자동 감지

    # ── Tableau MCP (공식 @tableau/mcp-server) ──────────────
    use_mcp: bool = False
    mcp_base_url: str = "http://localhost:7788"
    mcp_route: str = "tableau-mcp"
    mcp_timeout: float = 60.0
    mcp_ssl_verify: bool = True
    mcp_ca_bundle: str = ""
    mcp_allowed_tools: str = ""                 # 콤마 구분 화이트리스트
    mcp_max_tool_args_bytes: int = 1_048_576

    # ── LLM (로컬 우선, PRD §7.3) ───────────────────────────
    ollama_base_url: str = "http://localhost:11434"
    llm_router_model: str = "gemma4:26b"        # no-think 라우팅
    llm_synthesis_model: str = "gemma4:26b"     # thinking 융합 합성
    # 합성 컨텍스트 창(토큰). Ollama 기본(~4096)이면 화면 데이터가 잘림 → 크게 지정해 여러 시트 수용.
    # 모델 최대 262144. 키울수록 메모리·지연 증가 (M4 Max 64GB에서 32768 권장 상한선 근처).
    llm_num_ctx: int = 32768
    # 외부 API 비활성 슬롯 (v1 미사용 — 정책 허용 시 채워서 활성화)
    openai_api_key: str = ""
    openai_base_url: str = ""
    openai_model: str = ""

    # ── Dify (지식 계층, PRD §7.2) ──────────────────────────
    dify_base_url: str = "http://localhost:8090"
    dify_dataset_api_key: str = ""              # scripts/dify_provision.sh 발급 키
    dify_default_dataset_id: str = ""           # 기본(일반) — 선택 미해당/폴백
    # 지식 도메인별 dataset (L2 에이전틱 선택, docs/KNOWLEDGE_ROUTING_DESIGN.md). 비우면 해당 도메인 미사용
    dify_dataset_general: str = ""
    dify_dataset_strategy: str = ""

    # ── Database ────────────────────────────────────────────
    database_url: str = "sqlite:///./insight.db"

    # ── 파생 속성 ───────────────────────────────────────────
    @property
    def tableau_server_base(self) -> str:
        s = self.tableau_server.strip()
        if not s:
            return ""
        if not s.startswith(("http://", "https://")):
            s = f"https://{s}"
        return s.rstrip("/")

    @property
    def mcp_endpoint(self) -> str:
        base = self.mcp_base_url.strip()
        if base and not base.startswith(("http://", "https://")):
            base = f"http://{base}"
        route = self.mcp_route.strip("/") or "tableau-mcp"
        return f"{base.rstrip('/')}/{route}/"   # 끝 슬래시 필수

    @property
    def ds_map(self) -> Dict[str, str]:
        out: Dict[str, str] = {}
        for part in (p.strip() for p in self.tableau_datasource_map.split(",") if p.strip()):
            if "=" in part:
                k, v = part.split("=", 1)
                out[k.strip()] = v.strip()
        return out

    @property
    def tableau_verify(self):
        return self.tableau_ca_bundle or self.tableau_ssl_verify

    @property
    def mcp_verify(self):
        return self.mcp_ca_bundle or self.mcp_ssl_verify

    @property
    def mcp_allowed_tools_list(self) -> list[str]:
        return [t.strip() for t in self.mcp_allowed_tools.split(",") if t.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
