// App.tsx — 3a 대시보드+QA 분할 뷰 (mode: both | dash | ai, 경계 드래그 22~78%)
import { useCallback, useRef, useState } from 'react';
import './tokens.css';
import './App.css';
import Rail from './components/Rail';
import DashboardList, { DASHBOARDS } from './components/DashboardList';
import DashboardView, { type AppliedFilter, type MarkSelection, type SheetCollector, type FilterCollector } from './components/DashboardView';
import QAPanel from './components/QAPanel';

type Mode = 'both' | 'dash' | 'ai';

export default function App() {
  const [dashSlug, setDashSlug] = useState('superstore-overview');
  const [listOpen, setListOpen] = useState(true);        // 레일 ▤ 로 대시보드 목록 접기/펴기
  const [mode, setMode] = useState<Mode>('both');
  const [ratio, setRatio] = useState(0.5);
  const [filters, setFilters] = useState<AppliedFilter[]>([]);
  const [mark, setMark] = useState<{ sel: MarkSelection; raw: Record<string, string> } | null>(null);
  const [activeSheet, setActiveSheet] = useState('');           // 임베드 뷰의 현재 시트 (탭 전환 추적)
  const containerRef = useRef<HTMLDivElement>(null);
  const dragging = useRef(false);
  const sheetCollector = useRef<SheetCollector | null>(null);   // 시트 데이터 수집기 (viz 로드 후 등록)
  const registerCollector = useCallback((fn: SheetCollector | null) => { sheetCollector.current = fn; }, []);
  const collectSheets = useCallback(() => sheetCollector.current?.() ?? Promise.resolve(null), []);
  const filterCollector = useRef<FilterCollector | null>(null);  // 화면 적용 필터 수집기
  const registerFilterCollector = useCallback((fn: FilterCollector | null) => { filterCollector.current = fn; }, []);
  const collectFilters = useCallback(() => filterCollector.current?.() ?? Promise.resolve(null), []);

  const dashboard = DASHBOARDS.find(d => d.slug === dashSlug) ?? DASHBOARDS[0];

  // 경계 드래그 (3a: 22~78%)
  const startDrag = useCallback((e: React.MouseEvent) => {
    e.preventDefault();
    dragging.current = true;
    document.body.style.cursor = 'col-resize';
    const move = (ev: MouseEvent) => {
      if (!dragging.current || !containerRef.current) return;
      const r = containerRef.current.getBoundingClientRect();
      setRatio(Math.max(0.22, Math.min(0.78, (ev.clientX - r.left) / r.width)));
    };
    const up = () => {
      dragging.current = false;
      document.body.style.cursor = '';
      document.removeEventListener('mousemove', move);
      document.removeEventListener('mouseup', up);
    };
    document.addEventListener('mousemove', move);
    document.addEventListener('mouseup', up);
  }, []);

  return (
    <div className="app">
      <Rail listOpen={listOpen} onToggleList={() => setListOpen(o => !o)} />
      {listOpen && (
        <DashboardList active={dashSlug} onSelect={slug => { setDashSlug(slug); setMark(null); setActiveSheet(''); }} />
      )}

      <div className="split" ref={containerRef}>
        {/* 대시보드 접힘 → 펼치기 레일 */}
        {mode === 'ai' && (
          <button className="restore-rail left" title="대시보드 펼치기" onClick={() => setMode('both')}>
            <span>▤ 대시보드 펼치기</span>
          </button>
        )}

        {mode !== 'ai' && (
          <div className="pane" style={{ flex: mode === 'dash' ? '1 1 0' : `${ratio} 1 0` }}>
            <DashboardView
              dashboard={dashboard}
              filters={filters}
              onRemoveFilter={f => setFilters(prev => prev.filter(x => !(x.field === f.field && x.value === f.value)))}
              onMarkSelect={(sel, raw) => setMark({ sel, raw })}
              onHide={() => setMode('ai')}
              onSheetChange={setActiveSheet}
              registerCollector={registerCollector}
              registerFilterCollector={registerFilterCollector}
            />
          </div>
        )}

        {mode === 'both' && (
          <div className="divider-bar" onMouseDown={startDrag} title="드래그해서 크기 조절">
            <div className="grip" />
          </div>
        )}

        {mode !== 'dash' && (
          <div className="pane" style={{ flex: mode === 'ai' ? '1 1 0' : `${1 - ratio} 1 0`, borderLeft: '1px solid var(--line-2)' }}>
            <QAPanel
              dashboardName={dashboard.name}
              channel={dashboard.slug}
              viewPath={dashboard.viewPath}
              activeSheet={activeSheet}
              collectSheets={collectSheets}
              collectFilters={collectFilters}
              markContext={mark ? mark.raw : null}
              onVdsApplied={info => {
                // AI가 실행한 VDS 질의의 CATEGORICAL 필터 → C 패널 "AI 적용 필터" 칩 반영 (3a 시나리오)
                const chips: AppliedFilter[] = [];
                for (const f of info.query_spec?.filters ?? []) {
                  const field = f.field?.fieldCaption;
                  if (field && f.values?.length) {
                    chips.push({ field, value: f.values.join(', ') });
                  }
                }
                // 질의에 쓰인 필드도 표시 (필터 없을 때 무엇을 조회했는지 노출)
                if (chips.length === 0 && info.query_spec?.fields) {
                  const dims = info.query_spec.fields.filter(f => !f.function || f.function.startsWith('TRUNC'));
                  if (dims.length) chips.push({ field: '조회', value: dims.map(d => d.fieldCaption).join(' · ') });
                }
                setFilters(chips);
              }}
              onHide={() => setMode('dash')}
            />
          </div>
        )}

        {/* AI 접힘 → 펼치기 레일 */}
        {mode === 'dash' && (
          <button className="restore-rail right" title="AI Q&A 펼치기" onClick={() => setMode('both')}>
            <span>✦ AI Q&A 펼치기</span>
          </button>
        )}
      </div>
    </div>
  );
}
