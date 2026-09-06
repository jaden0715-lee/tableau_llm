// api.ts — backend 클라이언트 (SSE 채팅 + 이력 + 문서)
const BASE = import.meta.env.VITE_API_BASE ?? 'http://localhost:8000';

export interface Citation { name: string; score: number; excerpt?: string; }

export interface QuerySpec {
  fields?: { fieldCaption: string; function?: string }[];
  filters?: { field?: { fieldCaption?: string }; filterType?: string; values?: string[] }[];
}

export interface DataSource {
  datasource?: string;
  gateway?: string;
  row_count?: number;
  query_spec?: QuerySpec;       // 필터칩용 (첫 질의)
  query_specs?: QuerySpec[];    // 다중 질의 전체
}

export interface ChatMeta {
  route?: string;
  doc_citations?: Citation[];
  data_sources?: DataSource[];
  active_sheet?: string | null;   // 분석 당시 활성 시트(탭) — 캡처 이미지가 이 시트를 렌더
  steps?: StepInfo[];             // 작업 과정 — 완료 후에도 답변과 함께 유지
}

export interface ChatMessage {
  id: string;
  role: 'user' | 'assistant';
  content: string;
  thread_root_id?: string | null;
  meta: ChatMeta;
  feedback?: number;        // 👍 +1 | 👎 -1 | 0 없음 (P1-2)
  created_at: string;
}

export interface MarkContext {
  [key: string]: string | number;
}

export async function fetchHistory(channel: string): Promise<ChatMessage[]> {
  const r = await fetch(`${BASE}/api/chat/history/${encodeURIComponent(channel)}`);
  const j = await r.json();
  return j.messages;
}

export interface VdsInfo {
  datasource?: string;
  gateway?: string;
  row_count?: number;
  error?: string;
  query_spec?: QuerySpec;
  query_specs?: QuerySpec[];
}

/** 작업 과정 스텝 (SSE `step` 이벤트) */
export interface StepInfo {
  id: string;
  label: string;
  status: 'run' | 'done' | 'fail';
  detail?: string;
}

/** 임베드 뷰에서 수집한 시트별 표시 데이터 (전체 시트 근거 답변용) */
export interface SheetData {
  sheet: string;
  columns: string[];
  rows: (string | number | null)[][];
  truncated?: boolean;
}

export interface StreamHandlers {
  onRoute?: (route: string) => void;
  onStep?: (step: StepInfo) => void;
  onCitations?: (citations: Citation[]) => void;
  onVds?: (info: VdsInfo) => void;
  onToken: (t: string) => void;
  onDone?: (meta: ChatMeta) => void;
  onError?: (e: unknown) => void;
}

export interface StreamOptions {
  mark?: MarkContext;
  threadRootId?: string;
  activeSheet?: string;
  sheets?: SheetData[] | null;
  appliedFilters?: Record<string, string[]> | null;   // 사용자가 화면에서 건 필터
  knowledgeScope?: string | null;   // 지식 범위: auto(기본)/전략/일반/전체 (UI 칩 오버라이드)
  signal?: AbortSignal;             // 사용자 취소용 (Stop 버튼 → controller.abort())
}

/** POST /api/chat SSE 스트림 소비 (fetch 기반 — EventSource는 POST 불가) */
export async function streamChat(
  channel: string,
  message: string,
  handlers: StreamHandlers,
  opts: StreamOptions = {},
): Promise<void> {
  let r: Response;
  try {
    r = await fetch(`${BASE}/api/chat`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        channel, message,
        mark_context: opts.mark ?? null,
        thread_root_id: opts.threadRootId ?? null,
        active_sheet: opts.activeSheet || null,
        sheets_context: opts.sheets ?? null,
        applied_filters: opts.appliedFilters ?? null,
        knowledge_scope: opts.knowledgeScope ?? null,
      }),
      signal: opts.signal,
    });
  } catch (e) {
    if (!opts.signal?.aborted) handlers.onError?.(e);   // 취소는 조용히 무시
    return;
  }
  if (!r.ok || !r.body) {
    handlers.onError?.(new Error(`chat failed: ${r.status}`));
    return;
  }
  const reader = r.body.getReader();
  const decoder = new TextDecoder();
  let buf = '';
  let event = '';
  let terminated = false;   // done/error 이벤트로 정상 종료했는지
  try {
    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;
      buf += decoder.decode(value, { stream: true });
      const lines = buf.split('\n');
      buf = lines.pop() ?? '';
      for (const line of lines) {
        if (line.startsWith('event:')) {
          event = line.slice(6).trim();
        } else if (line.startsWith('data:')) {
          const data = line.slice(5).trim();
          if (!data) continue;
          try {
            const obj = JSON.parse(data);
            if (event === 'route') handlers.onRoute?.(obj.route);
            else if (event === 'step') handlers.onStep?.(obj);
            else if (event === 'citations') handlers.onCitations?.(obj);
            else if (event === 'vds') handlers.onVds?.(obj);
            else if (event === 'token') handlers.onToken(obj.t);
            else if (event === 'done') { terminated = true; handlers.onDone?.(obj.meta); }
            else if (event === 'error') { terminated = true; handlers.onError?.(new Error(obj.message || '응답 오류')); }
          } catch { /* ping 등 무시 */ }
        }
      }
    }
  } catch (e) {
    if (!opts.signal?.aborted) { terminated = true; handlers.onError?.(e); }
    return;
  } finally {
    // 안전망: done/error 없이 스트림이 끊겼는데 취소도 아니면 스피너가 멈추도록 종료 알림
    if (!terminated && !opts.signal?.aborted) handlers.onError?.(new Error('연결이 끊겼습니다. 다시 시도해주세요.'));
  }
}

/** Connected App JWT — 무로그인 임베딩용. 미설정(503)이면 null (브라우저 세션 폴백) */
export async function fetchEmbedToken(): Promise<string | null> {
  try {
    const r = await fetch(`${BASE}/api/tableau/embed-token`);
    if (!r.ok) return null;
    const j = await r.json();
    return j.token ?? null;
  } catch {
    return null;
  }
}

/** 대시보드 요약 — 시작 시 1회 미리 생성 (backend 캐시, 재열람 시 즉시 반환) */
export async function fetchSummary(channel: string, dashboard: string, sheets?: SheetData[] | null):
  Promise<{ summary: string; cached: boolean }> {
  const r = await fetch(`${BASE}/api/chat/summary`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ channel, dashboard, sheets_context: sheets ?? null }),
  });
  if (!r.ok) throw new Error(`summary failed: ${r.status}`);
  return r.json();
}

/** 추천 질문 3개 (현재 대시보드/시트 기준) */
export async function fetchSuggestions(channel: string, dashboard: string, sheet: string):
  Promise<{ questions: string[] }> {
  const r = await fetch(`${BASE}/api/chat/suggest`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ channel, dashboard, sheet }),
  });
  if (!r.ok) throw new Error(`suggest failed: ${r.status}`);
  return r.json();
}

/** 답변 피드백 (P1-2): +1 👍 / -1 👎 / 0 취소 */
export async function sendFeedback(messageId: string, rating: 1 | -1 | 0): Promise<{ rating: number }> {
  const r = await fetch(`${BASE}/api/feedback`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ message_id: messageId, rating }),
  });
  if (!r.ok) throw new Error(`feedback failed: ${r.status}`);
  return r.json();
}

/** 답변 meta의 VDS query_spec에서 값 필터(SET) → {field: value} (뷰 이미지 vf_용) */
export function categoricalFilters(meta: ChatMeta): Record<string, string> {
  const out: Record<string, string> = {};
  for (const ds of meta.data_sources ?? []) {
    const specs = ds.query_specs ?? (ds.query_spec ? [ds.query_spec] : []);
    for (const sp of specs) {
      for (const f of sp.filters ?? []) {
        const field = f.field?.fieldCaption;
        // SET = 값 필터. CATEGORICAL은 과거 저장 메시지 호환용
        if (field && (f.filterType === 'SET' || f.filterType === 'CATEGORICAL') && f.values?.length) {
          out[field] = f.values.join(',');
        }
      }
    }
  }
  return out;
}

/** 뷰 캡처 이미지 URL (img src 전용). filters는 CATEGORICAL 필터 반영 */
export function viewImageUrl(viewPath: string, filters?: Record<string, string>, resolution = 'high'): string {
  const p = new URLSearchParams({ view: viewPath, resolution });
  if (filters && Object.keys(filters).length) p.set('vf', JSON.stringify(filters));
  return `${BASE}/api/tableau/view-image?${p.toString()}`;
}

export async function uploadDocument(channel: string, file: File, scope = 'channel') {
  const fd = new FormData();
  fd.append('file', file);
  fd.append('channel', channel);
  fd.append('scope', scope);
  const r = await fetch(`${BASE}/api/documents`, { method: 'POST', body: fd });
  return r.json();
}

export async function documentStatus(docId: string) {
  const r = await fetch(`${BASE}/api/documents/${docId}/status`);
  return r.json();
}
