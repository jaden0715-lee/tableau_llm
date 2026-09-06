// C — 대시보드 뷰 (3a): 헤더 + AI 적용 필터칩 + 실제 Tableau 임베드 (Embedding API v3) + 거버넌스 스트립
// 인증: 브라우저의 Tableau Cloud 세션 사용 (최초 1회 뷰 안에서 로그인 필요할 수 있음)
//       무로그인 SSO가 필요하면 Connected App(Direct Trust) JWT를 <tableau-viz token>으로 전달
import { useEffect, useRef, useState } from 'react';
import { fetchEmbedToken, type SheetData } from '../api';
import type { DashboardItem } from './DashboardList';

export interface AppliedFilter { field: string; value: string; }
export interface MarkSelection { label: string; detail: string; }
export type SheetCollector = () => Promise<SheetData[] | null>;
export type FilterCollector = () => Promise<Record<string, string[]> | null>;

/** Embedding API v3 마크 선택 이벤트 → {필드: 값} 레코드 추출 */
async function extractMarks(e: Event): Promise<Record<string, string> | null> {
  try {
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    const detail: any = (e as CustomEvent).detail;
    const marks = await detail.getMarksAsync();
    const table = marks?.data?.[0];
    if (!table || !table.data?.length) return null;
    const cols = table.columns.map((c: { fieldName: string }) => c.fieldName);
    const row = table.data[0];
    const out: Record<string, string> = {};
    cols.forEach((c: string, i: number) => {
      const v = row[i]?.formattedValue ?? row[i]?.value;
      if (v !== undefined && v !== null) out[c] = String(v);
    });
    return Object.keys(out).length ? out : null;
  } catch {
    return null;
  }
}

export default function DashboardView({ dashboard, filters, onRemoveFilter, onMarkSelect, onHide, onSheetChange, registerCollector, registerFilterCollector }: {
  dashboard: DashboardItem;
  filters: AppliedFilter[];
  onRemoveFilter: (f: AppliedFilter) => void;
  onMarkSelect: (m: MarkSelection, raw: Record<string, string>) => void;
  onHide: () => void;
  onSheetChange?: (sheet: string) => void;          // 시트 이동 추적 (탭 전환 인지)
  registerCollector?: (fn: SheetCollector | null) => void;  // 시트 데이터 수집기 등록
  registerFilterCollector?: (fn: FilterCollector | null) => void;  // 화면 적용 필터 수집기
}) {
  const vizRef = useRef<HTMLElement>(null);
  const wrapRef = useRef<HTMLDivElement>(null);
  // Connected App JWT: 대시보드 전환마다 새 토큰 발급 (1회용)
  // null = 미설정/실패 → 브라우저 Tableau 세션 폴백, undefined = 로딩 중
  const [embedToken, setEmbedToken] = useState<string | null | undefined>(undefined);

  useEffect(() => {
    setEmbedToken(undefined);
    let alive = true;
    fetchEmbedToken().then(t => { if (alive) setEmbedToken(t); });
    return () => { alive = false; };
  }, [dashboard.slug]);

  // 컨테이너 리사이즈 → viz 크기 동기화 (분할 드래그/패널 숨김·펼침 대응)
  // tableau-viz iframe은 로드 시점 크기로 고정되므로 ResizeObserver로 갱신해줘야 함
  // 주의: viz는 토큰 발급 후에야 마운트되므로 embedToken을 deps에 포함해 그 시점에 재부착
  useEffect(() => {
    if (embedToken === undefined) return;   // 토큰 발급 중 — viz 미마운트
    const wrap = wrapRef.current;
    if (!wrap) return;

    let raf = 0;
    const sync = (w: number, h: number) => {
      // ref 캡처 대신 매번 조회 — 마운트 타이밍과 무관하게 동작
      const viz = wrap.querySelector('tableau-viz') as HTMLElement | null;
      if (!viz) return;
      viz.style.width = `${w}px`;
      viz.style.height = `${h}px`;
      viz.setAttribute('width', String(Math.floor(w)));
      viz.setAttribute('height', String(Math.floor(h)));
      // 내부 iframe까지 강제 동기화
      const iframe = (viz.shadowRoot?.querySelector('iframe') ?? viz.querySelector('iframe')) as HTMLIFrameElement | null;
      if (iframe) {
        iframe.style.width = '100%';
        iframe.style.height = '100%';
      }
    };
    const ro = new ResizeObserver(entries => {
      const r = entries[0].contentRect;
      cancelAnimationFrame(raf);
      raf = requestAnimationFrame(() => sync(r.width, r.height));
    });
    ro.observe(wrap);
    // 초기 1회 + viz 로드 직후 재동기화 (iframe 생성이 비동기라 지연 재시도)
    const rect = wrap.getBoundingClientRect();
    if (rect.width && rect.height) sync(rect.width, rect.height);
    const late = window.setTimeout(() => {
      const r = wrap.getBoundingClientRect();
      if (r.width && r.height) sync(r.width, r.height);
    }, 1500);
    return () => { ro.disconnect(); cancelAnimationFrame(raf); window.clearTimeout(late); };
  }, [dashboard.slug, embedToken]);

  // 시트 이동 인지 (요청 4): firstinteractive에서 초기 시트, tabswitched에서 전환 추적
  useEffect(() => {
    if (embedToken === undefined) return;
    const el = vizRef.current;
    if (!el) return;
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    const readActive = () => {
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      const name = (el as any).workbook?.activeSheet?.name;
      if (name) onSheetChange?.(name);
    };
    const onTab = (e: Event) => {
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      const d: any = (e as CustomEvent).detail;
      const name = d?.newTabName ?? d?.tabName;
      if (name) onSheetChange?.(name);
      else readActive();
    };
    el.addEventListener('firstinteractive', readActive);
    el.addEventListener('tabswitched', onTab);
    return () => {
      el.removeEventListener('firstinteractive', readActive);
      el.removeEventListener('tabswitched', onTab);
    };
  }, [dashboard.slug, embedToken, onSheetChange]);

  // 시트 데이터 수집기 등록 (요청 2: 전체 시트 기준 답변)
  // 대시보드면 내부 워크시트 전체의 표시 데이터(summary data)를 수집 — 사용자 필터 반영된 실제 화면 값
  useEffect(() => {
    if (!registerCollector) return;
    if (embedToken === undefined) { registerCollector(null); return; }
    const collect: SheetCollector = async () => {
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      const viz: any = vizRef.current;
      const sheet = viz?.workbook?.activeSheet;
      if (!sheet) return null;
      const worksheets = sheet.sheetType === 'dashboard' ? (sheet.worksheets ?? []) : [sheet];
      const out: SheetData[] = [];
      for (const w of worksheets.slice(0, 8)) {
        try {
          // 크로스탭(지역×세그먼트×월 등)은 행이 많음 → 넉넉히 수집(백엔드에서 다시 예산 제한)
          const t = await w.getSummaryDataAsync({ maxRows: 400, ignoreSelection: true });
          out.push({
            sheet: w.name,
            // eslint-disable-next-line @typescript-eslint/no-explicit-any
            columns: (t.columns ?? []).map((c: any) => c.fieldName),
            // eslint-disable-next-line @typescript-eslint/no-explicit-any
            rows: (t.data ?? []).slice(0, 400).map((r: any[]) =>
              r.map(v => (v?.formattedValue ?? v?.value ?? null))),
            truncated: (t.totalRowCount ?? t.data?.length ?? 0) > 400,
          });
        } catch { /* 개별 시트 실패는 건너뜀 */ }
      }
      return out.length ? out : null;
    };
    registerCollector(collect);
    return () => registerCollector(null);
  }, [dashboard.slug, embedToken, registerCollector]);

  // 화면 적용 필터 수집기 (사용자가 직접 건 필터 인지 → backend 질의·답변에 반영)
  // 워크시트별 getFiltersAsync(카테고리 필터) + 워크북 파라미터 → {field: [values]}
  useEffect(() => {
    if (!registerFilterCollector) return;
    if (embedToken === undefined) { registerFilterCollector(null); return; }
    const collect: FilterCollector = async () => {
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      const viz: any = vizRef.current;
      const sheet = viz?.workbook?.activeSheet;
      if (!sheet) return null;
      const worksheets = sheet.sheetType === 'dashboard' ? (sheet.worksheets ?? []) : [sheet];
      const out: Record<string, Set<string>> = {};
      const add = (field: string, vals: string[]) => {
        if (!field || !vals.length) return;
        const set = (out[field] ??= new Set());
        vals.forEach(v => set.add(v));
      };
      for (const w of worksheets.slice(0, 12)) {
        try {
          const fs = await w.getFiltersAsync();
          // eslint-disable-next-line @typescript-eslint/no-explicit-any
          for (const f of fs as any[]) {
            // 카테고리 필터 중 '전체'가 아닌(특정 값 선택) 것만
            if (f.filterType === 'categorical' && !f.isAllSelected && f.appliedValues?.length) {
              // eslint-disable-next-line @typescript-eslint/no-explicit-any
              add(f.fieldName, f.appliedValues.map((v: any) => String(v.formattedValue ?? v.value)));
            }
          }
        } catch { /* 시트별 실패 무시 */ }
      }
      // 파라미터(예: Region 드롭다운이 파라미터일 때) — 이름이 필드와 겹치면 backend가 필터로 사용
      try {
        // eslint-disable-next-line @typescript-eslint/no-explicit-any
        const params: any[] = (await viz.workbook.getParametersAsync?.()) ?? [];
        for (const p of params) {
          const v = p.currentValue?.formattedValue ?? p.currentValue?.value;
          const s = v == null ? '' : String(v);
          if (p.name && s && s !== '전체' && s.toLowerCase() !== 'all' && s !== '(All)') add(p.name, [s]);
        }
      } catch { /* 파라미터 API 없으면 무시 */ }
      const rec: Record<string, string[]> = {};
      for (const k of Object.keys(out)) rec[k] = Array.from(out[k]);
      return Object.keys(rec).length ? rec : null;
    };
    registerFilterCollector(collect);
    return () => registerFilterCollector(null);
  }, [dashboard.slug, embedToken, registerFilterCollector]);

  // 마크 선택 이벤트 리스닝 (3a: 마크 클릭 → D에 즉시 설명)
  // embedToken deps 포함 — viz 마운트 후 부착 보장
  useEffect(() => {
    if (embedToken === undefined) return;
    const el = vizRef.current;
    if (!el) return;
    const handler = async (e: Event) => {
      const rec = await extractMarks(e);
      if (!rec) return;
      const entries = Object.entries(rec).slice(0, 4);
      onMarkSelect(
        {
          label: entries.map(([, v]) => v).slice(0, 2).join(' · '),
          detail: entries.map(([k, v]) => `${k}=${v}`).join(', '),
        },
        rec,
      );
    };
    el.addEventListener('markselectionchanged', handler);
    return () => el.removeEventListener('markselectionchanged', handler);
  }, [dashboard.slug, embedToken, onMarkSelect]);

  return (
    <div className="dash-view">
      <div className="head">
        <div>
          <div className="ttl">{dashboard.name} <span className="badge-conf">🔒 Confidential</span></div>
          <div className="sub">{dashboard.path} · Tableau Cloud 임베드</div>
        </div>
        <div className="acts">
          <button className="iconbtn" title="Tableau에서 열기"
            onClick={() => window.open(dashboard.embedUrl, '_blank')}>↗ 열기</button>
          <button className="iconbtn" style={{ fontWeight: 700 }} title="대시보드 숨기기 (AI 크게)" onClick={onHide}>⟨</button>
        </div>
      </div>

      {/* AI 적용 필터칩 바 — AI가 실행한 VDS 질의가 여기 반영 */}
      <div className="filter-bar">
        <span className="lbl">AI 적용 필터</span>
        {filters.length === 0 && <span style={{ fontSize: 12, color: 'var(--muted)' }}>질문하면 AI가 적용한 조회 조건이 표시됩니다</span>}
        {filters.map(f => (
          <button key={`${f.field}=${f.value}`} className="chip" onClick={() => onRemoveFilter(f)}>
            {f.field} = {f.value} ✕
          </button>
        ))}
      </div>

      {/* ===== 실제 Tableau 임베드 (Embedding API v3) ===== */}
      <div className="embed-wrap">
        {/* overflow:auto — 대시보드가 고정 크기(fixed sizing)여도 잘리지 않고 스크롤 */}
        <div className="embed" ref={wrapRef} style={{ overflow: 'auto' }}>
          {embedToken === undefined ? (
            <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', height: '100%', color: 'var(--muted)', fontSize: 13 }}>
              인증 토큰 발급 중…
            </div>
          ) : (
            <tableau-viz
              key={`${dashboard.slug}-${embedToken ? 'jwt' : 'session'}`}
              ref={vizRef}
              src={dashboard.embedUrl}
              toolbar="hidden"
              {...(embedToken ? { token: embedToken } : {})}
              style={{ display: 'block' }}
            />
          )}
        </div>
      </div>

      <div className="gov-strip">
        <span className="dot" />인증 원본 · Superstore Datasource · Samples 프로젝트 · 권한: 분석가(읽기)
      </div>
    </div>
  );
}
