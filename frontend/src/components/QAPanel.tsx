// D — AI Q&A (3a): 대화 + 근거 카드 + 히스토리 전환 + 퀵액션 + 문서 업로드(A2). backend SSE 실연동.
import { useCallback, useEffect, useRef, useState } from 'react';
import {
  type ChatMessage, type ChatMeta, type SheetData, type StepInfo, type VdsInfo,
  categoricalFilters, documentStatus, fetchHistory, fetchSuggestions, fetchSummary,
  sendFeedback, streamChat, uploadDocument, viewImageUrl,
} from '../api';
import Answer from './Answer';

interface StreamingState {
  route?: string;
  steps?: StepInfo[];                 // 작업 과정 (SSE step 이벤트)
  citations?: { name: string; score: number }[];
  vds?: VdsInfo;
  text: string;
}

// ── 문서 업로드 (A2) — backend documents.py와 동일 기준으로 사전 검증 ──
const ALLOWED_EXTS = ['.pdf', '.docx', '.txt', '.md', '.pptx', '.xlsx', '.csv'];
const MAX_FILE_BYTES = 30 * 1024 * 1024; // 30MB

interface UploadItem {
  id: string;
  filename: string;
  status: 'uploading' | 'indexing' | 'completed' | 'error';
  error?: string;
  file?: File;           // 재시도용 (메모리에만 보관)
}

function validateFile(f: File): string | null {
  const ext = f.name.includes('.') ? '.' + f.name.split('.').pop()!.toLowerCase() : '';
  if (!ALLOWED_EXTS.includes(ext)) return `지원하지 않는 형식 (허용: ${ALLOWED_EXTS.join(' ')})`;
  if (f.size > MAX_FILE_BYTES) return `파일이 너무 큽니다 (${(f.size / 1048576).toFixed(1)}MB > 30MB)`;
  return null;
}

function timeOf(iso: string) {
  return new Date(iso).toLocaleTimeString('ko-KR', { hour: '2-digit', minute: '2-digit' });
}

// 답변 근거로 사용한 태블로 화면 캡처 (표시용, 뷰+AI 적용 필터 반영)
function CapturedView({ viewPath, meta }: { viewPath: string; meta: ChatMeta }) {
  const [hidden, setHidden] = useState(false);
  const hasData = (meta.data_sources?.length ?? 0) > 0;
  if (!viewPath || !hasData || hidden) return null;
  const filters = categoricalFilters(meta);
  const filterText = Object.entries(filters).map(([k, v]) => `${k}=${v}`).join(', ');
  // 분석한 시트(탭)를 캡처 — meta.active_sheet가 있으면 그 시트 뷰, 없으면 대시보드 기본 뷰(Overview 고정 방지)
  const workbook = viewPath.split('/')[0];
  const sheet = meta.active_sheet?.trim();
  const capturePath = sheet ? `${workbook}/${sheet}` : viewPath;
  const src = viewImageUrl(capturePath, filters, 'high');
  return (
    <div className="capture">
      <div className="cap-label">📸 분석에 사용한 화면{sheet ? ` · ${sheet}` : ''}{filterText ? ` · 필터: ${filterText}` : ''}</div>
      <img src={src} alt="분석 화면 캡처" title="클릭하면 원본 크기로 열기"
        onClick={() => window.open(src, '_blank')}
        onError={() => setHidden(true)} />
    </div>
  );
}

function GovLine({ meta }: { meta: ChatMeta }) {
  const cites = meta.doc_citations ?? [];
  return (
    <div className="gov-line">
      🛡 인증 요약 데이터 기반 · PII 제외
      {meta.route && <> · {meta.route}</>}
      {cites.length > 0 && <> · 문서: {cites.map(c => c.name).join(', ')}</>}
    </div>
  );
}

function BotMessage({ content, meta, viewPath, feedback, onFeedback }: {
  content: string; meta: ChatMeta; viewPath: string;
  feedback?: number;                                  // +1 | -1 | 0 (P1-2)
  onFeedback?: (rating: 1 | -1 | 0) => void;          // 미전달 시 버튼 미표시 (로컬 오류 메시지 등)
}) {
  const [openIdx, setOpenIdx] = useState<number | null>(null);   // 근거 인라인 펼침
  const steps = meta.steps ?? [];
  const hasFail = steps.some(s => s.status === 'fail');
  const [showSteps, setShowSteps] = useState(true);              // 성공/실패 무관하게 기본 펼침(접기 가능)
  return (
    <div className="bot-row">
      <div className="ava">✦</div>
      <div className="col">
        {steps.length > 0 && (
          <div className="proc">
            <button className="proc-toggle" onClick={() => setShowSteps(v => !v)}>
              {showSteps ? '▴' : '▾'} 작업 과정 {steps.length}단계{hasFail ? ' · ⚠ 오류 포함' : ''}
            </button>
            {showSteps && (
              <div className="steps">
                {steps.map(s => (
                  <div key={s.id} className={`step ${s.status}`}>
                    <span className="ic">{s.status === 'run' ? '◌' : s.status === 'done' ? '✓' : '✕'}</span>
                    <span>{s.label}{s.detail ? ` · ${s.detail}` : ''}</span>
                  </div>
                ))}
              </div>
            )}
          </div>
        )}
        <div className="bot-bubble"><Answer content={content} /></div>
        <CapturedView viewPath={viewPath} meta={meta} />
        {(meta.doc_citations?.length ?? 0) > 0 && (
          <>
            <div className="ev-label">근거</div>
            {meta.doc_citations!.map((c, i) => {
              const open = openIdx === i;
              return (
                <div key={c.name + i} className={`ev-card ${open ? 'open' : ''}`}>
                  <button className="ev-head" onClick={() => setOpenIdx(open ? null : i)}>
                    <span className={`bar ${i % 2 ? 'alt' : ''}`} />
                    <span className="tx"><b>{c.name}</b><span className="dim"> · 유사도 {(c.score * 100).toFixed(0)}%</span></span>
                    <span className="chev">{open ? '▴' : '▾'}</span>
                  </button>
                  {open && (
                    <div className="ev-excerpt">
                      {c.excerpt?.trim() || '(발췌 내용이 없습니다 — 오래된 대화이거나 원문이 비어 있습니다)'}
                    </div>
                  )}
                </div>
              );
            })}
          </>
        )}
        <div className="meta-row">
          <GovLine meta={meta} />
          {onFeedback && (
            <span className="fb-btns">
              <button className={`fb ${feedback === 1 ? 'on' : ''}`} title="도움이 됐어요"
                onClick={() => onFeedback(feedback === 1 ? 0 : 1)}>👍</button>
              <button className={`fb ${feedback === -1 ? 'on down' : ''}`} title="아쉬워요"
                onClick={() => onFeedback(feedback === -1 ? 0 : -1)}>👎</button>
            </span>
          )}
        </div>
      </div>
    </div>
  );
}

export default function QAPanel({ dashboardName, channel, viewPath, activeSheet, collectSheets, collectFilters, markContext, onVdsApplied, onHide }: {
  dashboardName: string;
  channel: string;
  viewPath: string;                                    // REST 뷰 이미지 경로 (답변 근거 캡처)
  activeSheet: string;                                 // 임베드 뷰의 현재 시트 (탭 전환 추적)
  collectSheets: () => Promise<SheetData[] | null>;    // 화면 시트 데이터 수집 (viz 미로드 시 null)
  collectFilters: () => Promise<Record<string, string[]> | null>;  // 화면 적용 필터 수집
  markContext: Record<string, string> | null;
  onVdsApplied: (info: VdsInfo) => void;
  onHide: () => void;
}) {
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [streaming, setStreaming] = useState<StreamingState | null>(null);
  const [input, setInput] = useState('');
  const [showHistory, setShowHistory] = useState(false);
  const [uploads, setUploads] = useState<UploadItem[]>([]);
  const [dragging, setDragging] = useState(false);
  const dragDepth = useRef(0);           // 자식 요소 dragleave 오탐 방지
  const fileInputRef = useRef<HTMLInputElement>(null);
  const feedRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);         // 질문 입력창 (새 질문 시 포커스)
  const abortRef = useRef<AbortController | null>(null);   // 진행 중 질문 취소용
  const [hiddenIds, setHiddenIds] = useState<Set<string>>(new Set());  // '새 질문'으로 접은 이전 대화(히스토리엔 남음)

  const loadHistory = useCallback(async () => {
    try { setMessages(await fetchHistory(channel)); } catch { setMessages([]); }
  }, [channel]);

  useEffect(() => { loadHistory(); setShowHistory(false); }, [loadHistory]);
  useEffect(() => {
    feedRef.current?.scrollTo({ top: feedRef.current.scrollHeight });
  }, [messages, streaming, uploads]);

  // ── 대시보드 요약 (요청 3): 대시보드 열람 시 1회 미리 생성, backend 캐시로 재열람 즉시 ──
  const [summary, setSummary] = useState<{ text: string; loading: boolean }>({ text: '', loading: false });
  useEffect(() => {
    let alive = true;
    setSummary({ text: '', loading: true });
    (async () => {
      // viz가 이미 로드돼 있으면 화면 시트 데이터 기반, 아니면 backend가 VDS로 수집
      let sheets: SheetData[] | null = null;
      try { sheets = await collectSheets(); } catch { /* viz 미로드 */ }
      try {
        const r = await fetchSummary(channel, dashboardName, sheets);
        if (alive) setSummary({ text: r.summary, loading: false });
      } catch {
        if (alive) setSummary({ text: '', loading: false });
      }
    })();
    return () => { alive = false; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [channel]);

  // ── 추천 질문 (요청 3): 대시보드/시트 변경 시 갱신 (디바운스) ──
  const [suggested, setSuggested] = useState<string[]>([]);
  const [scope, setScope] = useState('auto');   // 지식 범위: auto=모델 자동선택 / 전략·일반·전체=강제
  useEffect(() => {
    let alive = true;
    const t = setTimeout(async () => {
      try {
        const r = await fetchSuggestions(channel, dashboardName, activeSheet);
        if (alive) setSuggested(r.questions ?? []);
      } catch { /* 추천 실패는 무시 */ }
    }, 600);
    return () => { alive = false; clearTimeout(t); };
  }, [channel, dashboardName, activeSheet]);

  // ── 문서 업로드 (A2): 업로드 → 색인 상태 2초 폴링 → 완료/오류 카드 전환 ──
  const patchUpload = (id: string, patch: Partial<UploadItem>) =>
    setUploads(prev => prev.map(u => (u.id === id ? { ...u, ...patch } : u)));

  const doUpload = async (file: File, existingId?: string) => {
    const id = existingId ?? `up-${Date.now()}-${file.name}`;
    if (!existingId) setUploads(prev => [...prev, { id, filename: file.name, status: 'uploading', file }]);
    else patchUpload(id, { status: 'uploading', error: undefined });

    const invalid = validateFile(file);   // 서버 왕복 없이 즉시 안내
    if (invalid) { patchUpload(id, { status: 'error', error: invalid }); return; }

    let docId: string;
    try {
      const res = await uploadDocument(channel, file, 'channel'); // 데모: channel 고정 (P0-5a 셀렉터는 파일럿에서)
      if (!res.id) throw new Error(res.detail ?? '업로드 실패');
      docId = res.id;
    } catch (e) {
      patchUpload(id, { status: 'error', error: e instanceof Error ? e.message : '업로드 실패 — backend(8000) 확인' });
      return;
    }

    patchUpload(id, { status: 'indexing' });
    for (let i = 0; i < 90; i++) {        // 최대 3분 폴링
      await new Promise(r => setTimeout(r, 2000));
      try {
        const st = await documentStatus(docId);
        if (st.indexing_status === 'completed') { patchUpload(id, { status: 'completed' }); return; }
        if (st.indexing_status === 'error') { patchUpload(id, { status: 'error', error: st.error || '색인 실패' }); return; }
      } catch { /* 일시 오류 — 다음 폴링에서 재확인 */ }
    }
    patchUpload(id, { status: 'error', error: '색인 시간 초과 (3분)' });
  };

  const handleFiles = (files: FileList | null) => {
    if (!files?.length) return;
    setShowHistory(false);
    Array.from(files).forEach(f => doUpload(f));
  };

  // 마크 선택 → 자동 질문 (3a: 마크 즉시 설명)
  const lastMark = useRef<string>('');
  useEffect(() => {
    if (!markContext) return;
    const key = JSON.stringify(markContext);
    if (key === lastMark.current) return;
    lastMark.current = key;
    ask(`선택한 마크(${Object.values(markContext).join(' · ')})에 대해 설명해줘`, markContext);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [markContext]);

  // 진행 중 질문 취소 — 부분 답변이 있으면 보존하고 스피너 종료
  const stop = () => {
    abortRef.current?.abort();
    const partial = streaming?.text?.trim();
    if (partial) {
      setMessages(prev => [...prev, {
        id: `stopped-${Date.now()}`, role: 'assistant',
        content: `${partial}\n\n_⏹ 답변이 중단되었습니다._`,
        meta: {
          route: streaming?.route,
          doc_citations: streaming?.citations,
          data_sources: streaming?.vds && !streaming.vds.error ? [streaming.vds] : [],
        },
        created_at: new Date().toISOString(),
      }]);
    }
    setStreaming(null);
  };

  const ask = async (text: string, mark?: Record<string, string>) => {
    if (!text.trim() || streaming) return;
    setShowHistory(false);
    const controller = new AbortController();
    abortRef.current = controller;
    setMessages(prev => [...prev, {
      id: `local-${Date.now()}`, role: 'user', content: text,
      meta: {}, created_at: new Date().toISOString(),
    }]);
    setStreaming({ text: '', steps: [{ id: 'collect', label: '화면 시트 데이터 수집', status: 'run' }] });

    // 전체 시트 근거 + 화면 적용 필터를 매 질문마다 수집해 동봉
    let sheets: SheetData[] | null = null;
    let appliedFilters: Record<string, string[]> | null = null;
    try { [sheets, appliedFilters] = await Promise.all([collectSheets(), collectFilters()]); }
    catch { /* viz 미로드 — 없이 진행 */ }
    const filterText = appliedFilters
      ? Object.entries(appliedFilters).map(([k, v]) => `${k}=${v.join('/')}`).join(', ') : '';
    setStreaming(s => ({
      ...(s ?? { text: '' }),
      steps: [{ id: 'collect', label: '화면 시트/필터 수집', status: 'done',
                detail: (sheets ? `${sheets.length}개 시트` : '건너뜀 (viz 미로드)')
                        + (filterText ? ` · 필터: ${filterText}` : '') }],
    }));

    await streamChat(channel, text, {
      onRoute: route => setStreaming(s => ({ ...(s ?? { text: '' }), route })),
      onStep: step => setStreaming(s => {
        const steps = [...(s?.steps ?? [])];
        const i = steps.findIndex(x => x.id === step.id);
        if (i >= 0) steps[i] = { ...steps[i], ...step };
        else steps.push(step);
        return { ...(s ?? { text: '' }), steps };
      }),
      onCitations: citations => setStreaming(s => ({ ...(s ?? { text: '' }), citations })),
      onVds: info => { setStreaming(s => ({ ...(s ?? { text: '' }), vds: info })); if (!info.error) onVdsApplied(info); },
      onToken: t => setStreaming(s => ({ ...(s ?? { text: '' }), text: (s?.text ?? '') + t })),
      onDone: () => { setStreaming(null); loadHistory(); },
      onError: () => {
        setStreaming(null);
        setMessages(prev => [...prev, {
          id: `err-${Date.now()}`, role: 'assistant',
          content: '⚠️ 응답 생성 실패 — backend(8000)와 Ollama 상태를 확인해주세요.',
          meta: {}, created_at: new Date().toISOString(),
        }]);
      },
    }, { mark: mark ?? markContext ?? undefined, activeSheet, sheets, appliedFilters, knowledgeScope: scope, signal: controller.signal });
  };

  // 시트 요약 (요청 3: 시작 이후의 요약은 현재 시트 기준)
  const askSheetSummary = () => ask(activeSheet
    ? `현재 보고 있는 "${activeSheet}" 시트의 내용을 요약해줘`
    : '현재 화면에 보이는 시트 내용을 요약해줘');

  const send = () => { const t = input.trim(); setInput(''); ask(t); };

  // '새 질문' — 진행 중 응답 중단, 현재 대화 화면 접기(히스토리엔 유지), 입력창으로
  const newQuestion = () => {
    abortRef.current?.abort();
    setStreaming(null);
    setShowHistory(false);
    setInput('');
    setHiddenIds(new Set(messages.map(m => m.id)));
    setTimeout(() => inputRef.current?.focus(), 0);
  };

  // 답변 피드백 (P1-2) — 낙관적 갱신 후 서버 저장, 실패 시 되돌림
  const rate = async (messageId: string, rating: 1 | -1 | 0) => {
    const prev = messages.find(m => m.id === messageId)?.feedback ?? 0;
    setMessages(ms => ms.map(m => (m.id === messageId ? { ...m, feedback: rating } : m)));
    try { await sendFeedback(messageId, rating); }
    catch { setMessages(ms => ms.map(m => (m.id === messageId ? { ...m, feedback: prev } : m))); }
  };

  // 히스토리: 사용자 질문만 그룹핑 (오늘/이전)
  const userQs = messages.filter(m => m.role === 'user');
  const today = new Date().toDateString();
  const histToday = userQs.filter(m => new Date(m.created_at).toDateString() === today);
  const histPast = userQs.filter(m => new Date(m.created_at).toDateString() !== today);

  return (
    <div
      className="qa"
      onDragEnter={e => { e.preventDefault(); dragDepth.current += 1; setDragging(true); }}
      onDragOver={e => e.preventDefault()}
      onDragLeave={e => { e.preventDefault(); dragDepth.current -= 1; if (dragDepth.current <= 0) { dragDepth.current = 0; setDragging(false); } }}
      onDrop={e => { e.preventDefault(); dragDepth.current = 0; setDragging(false); handleFiles(e.dataTransfer.files); }}
    >
      {dragging && (
        <div className="drop-overlay">
          <div className="drop-box">📄 여기에 문서를 놓으세요<br /><span>{ALLOWED_EXTS.join(' · ')} — 최대 30MB</span></div>
        </div>
      )}
      <div className="head">
        <span className="ai-ico">✦</span>
        <div>
          <div className="t1">AI Q&A</div>
          <div className="t2">{dashboardName} 기준</div>
        </div>
        <div className="acts">
          <button className="iconbtn" onClick={() => setShowHistory(h => !h)}>🕘 히스토리</button>
          <button className="iconbtn" onClick={newQuestion}>＋ 새 질문</button>
          <button className="iconbtn" style={{ fontWeight: 700 }} title="AI 숨기기 (대시보드 크게)" onClick={onHide}>⟩</button>
        </div>
      </div>

      <div className="feed" ref={feedRef}>
        {showHistory ? (
          <div className="hist">
            <div className="search">⌕ 질문 히스토리 검색</div>
            {histToday.length > 0 && <div className="grp">오늘</div>}
            {histToday.map(m => (
              <button key={m.id} className="hist-item" onClick={() => { setHiddenIds(new Set()); setShowHistory(false); }}>
                <span className="q-row">
                  <span style={{ color: 'var(--accent)', fontSize: 13, flexShrink: 0 }}>✦</span>
                  <span className="q">{m.content}</span>
                  <span className="tm">{timeOf(m.created_at)}</span>
                </span>
                <span className="tags"><span className="tag-dash">▤ {dashboardName}</span></span>
              </button>
            ))}
            {histPast.length > 0 && <div className="grp">이전</div>}
            {histPast.map(m => (
              <button key={m.id} className="hist-item" onClick={() => { setHiddenIds(new Set()); setShowHistory(false); }}>
                <span className="q-row">
                  <span style={{ color: 'var(--accent)', fontSize: 13, flexShrink: 0 }}>✦</span>
                  <span className="q">{m.content}</span>
                  <span className="tm">{new Date(m.created_at).toLocaleDateString('ko-KR', { month: '2-digit', day: '2-digit' })}</span>
                </span>
                <span className="tags"><span className="tag-dash">▤ {dashboardName}</span></span>
              </button>
            ))}
            {userQs.length === 0 && <div style={{ fontSize: 12.5, color: 'var(--muted)' }}>아직 질문 기록이 없습니다.</div>}
          </div>
        ) : (
          <>
            {/* 웰컴 = 대시보드 요약 카드 (시작 시 1회 미리 생성) + 시트 요약/추천 질문 */}
            <div className="bot-row">
              <div className="ava">✦</div>
              <div className="col">
                <div className="bot-bubble">
                  <b>📋 대시보드 요약 — {dashboardName}</b>
                  <div style={{ marginTop: 6, whiteSpace: 'pre-wrap' }}>
                    {summary.loading
                      ? '대시보드 데이터를 분석해 요약을 준비하는 중…'
                      : (summary.text || '이 대시보드에 대해 무엇이든 물어보세요. 대시보드를 클릭하면 해당 지표를 바로 설명해 드려요.')}
                  </div>
                </div>
                {summary.loading && <div className="typing"><i /><i /><i /></div>}
                {suggested.length > 0 && (
                  <>
                    <div className="ev-label">추천 질문</div>
                    <div className="quick-chips">
                      {suggested.map(q => (
                        <button key={q} className="qchip" onClick={() => ask(q)}>💡 {q}</button>
                      ))}
                    </div>
                  </>
                )}
              </div>
            </div>

            {messages.filter(m => !hiddenIds.has(m.id)).map(m =>
              m.role === 'user'
                ? <div key={m.id} className="user-bubble">{m.content}</div>
                : <BotMessage key={m.id} content={m.content} meta={m.meta} viewPath={viewPath}
                    feedback={m.feedback ?? 0}
                    // 서버 저장 메시지에만 피드백 허용 (로컬 오류 메시지 제외)
                    onFeedback={m.id.startsWith('err-') ? undefined : r => rate(m.id, r)} />
            )}

            {/* 문서 업로드 상태 카드 (A2) — 대화 맥락 안에서 진행 상태 표시 */}
            {uploads.map(u => (
              <div key={u.id} className="bot-row">
                <div className="ava">📄</div>
                <div className="col">
                  <div className="bot-bubble">
                    {u.status === 'uploading' && <><b>{u.filename}</b> — 업로드 중…</>}
                    {u.status === 'indexing' && <><b>{u.filename}</b> — 색인 중… 완료되면 바로 질문할 수 있어요.</>}
                    {u.status === 'completed' && <>✅ <b>{u.filename}</b> — 색인 완료! 이 문서에 대해 바로 질문해 보세요.</>}
                    {u.status === 'error' && <>⚠️ <b>{u.filename}</b> — {u.error}</>}
                  </div>
                  {(u.status === 'uploading' || u.status === 'indexing') && <div className="typing"><i /><i /><i /></div>}
                  {u.status === 'completed' && (
                    <div className="quick-chips">
                      <button className="qchip hot" onClick={() => ask(`방금 업로드한 "${u.filename}" 문서의 핵심 내용을 요약해줘`)}>📄 방금 올린 문서 요약</button>
                      <button className="qchip" onClick={() => ask(`방금 업로드한 "${u.filename}" 문서 내용과 대시보드의 실제 데이터를 비교해줘`)}>데이터와 비교</button>
                    </div>
                  )}
                  {u.status === 'error' && u.file && (
                    <div className="quick-chips">
                      <button className="qchip" onClick={() => doUpload(u.file!, u.id)}>↻ 재시도</button>
                    </div>
                  )}
                </div>
              </div>
            ))}

            {streaming && (
              <div className="bot-row">
                <div className="ava">✦</div>
                <div className="col">
                  {streaming.route && (
                    <div className="filter-note">
                      🔎 {streaming.route}로 분석 중
                      {streaming.vds && !streaming.vds.error && ` · VDS 질의 실행(${streaming.vds.row_count}행, ${streaming.vds.gateway})`}
                      {streaming.citations?.length ? ` · 문서 ${streaming.citations.length}건 검색됨` : ''}
                    </div>
                  )}
                  {/* 작업 과정 (요청 1) — 파이프라인 단계별 진행 표시 */}
                  {(streaming.steps?.length ?? 0) > 0 && (
                    <div className="steps">
                      {streaming.steps!.map(s => (
                        <div key={s.id} className={`step ${s.status}`}>
                          <span className="ic">{s.status === 'run' ? '◌' : s.status === 'done' ? '✓' : '✕'}</span>
                          <span>{s.label}{s.detail ? ` · ${s.detail}` : ''}</span>
                        </div>
                      ))}
                    </div>
                  )}
                  {streaming.text
                    ? <div className="bot-bubble"><Answer content={streaming.text} /></div>
                    : <div className="typing"><i /><i /><i /></div>}
                </div>
              </div>
            )}
          </>
        )}
      </div>

      {!showHistory && (
        <div className="composer">
          <div className="quick-bar">
            <button className="qchip hot" disabled={!!streaming} onClick={askSheetSummary}>
              📄 시트 요약{activeSheet ? ` — ${activeSheet}` : ''}
            </button>
            <button className="qchip" disabled={!!streaming} onClick={() => ask('이 대시보드에서 이상치를 찾아줘')}>이상치 찾기</button>
            <button className="qchip" disabled={!!streaming} onClick={() => ask('전월 대비 변화를 알려줘')}>전월 대비</button>
          </div>
          <div className="scope-row" title="검색할 지식 범위 — 자동은 질문에 따라 AI가 선택">
            <span className="scope-label">지식</span>
            {[['auto', '자동'], ['전략', '전략'], ['일반', '일반'], ['전체', '전체']].map(([v, label]) => (
              <button key={v} className={`scope-chip ${scope === v ? 'on' : ''}`}
                onClick={() => setScope(v)}>{label}</button>
            ))}
          </div>
          <div className="box">
            <button className="attach" title="문서 업로드 (지식으로 색인)" onClick={() => fileInputRef.current?.click()}>📎</button>
            <input ref={fileInputRef} type="file" multiple accept={ALLOWED_EXTS.join(',')}
              style={{ display: 'none' }}
              onChange={e => { handleFiles(e.target.files); e.target.value = ''; }} />
            <input ref={inputRef} value={input} placeholder='대시보드에 질문하기… 예: "가장 수익성 낮은 제품은?"'
              onChange={e => setInput(e.target.value)}
              onKeyDown={e => { if (e.key === 'Enter') send(); }} />
            {streaming
              ? <button className="send stop" onClick={stop} title="답변 중단">■</button>
              : <button className="send" onClick={send}>↑</button>}
          </div>
          <div className="hint">질문하면 관련 필터가 자동 선택되어 반영됩니다 · 📎 또는 드래그로 문서를 올리면 지식으로 색인됩니다</div>
        </div>
      )}
    </div>
  );
}
