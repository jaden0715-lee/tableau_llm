// L — 대시보드 리스트 (3a: 검색 + 즐겨찾기/전체, 인증 상태 표시)
export interface DashboardItem {
  slug: string;          // backend 채널명으로 사용
  name: string;
  sub: string;
  thumb: string;         // CSS background
  certified: boolean;
  favorite: boolean;
  embedUrl: string;      // Tableau Embedding API v3 src
  path: string;          // 워크북/뷰 경로 표시용
  viewPath: string;      // REST 뷰 이미지용 'Workbook/View' (view-image 엔드포인트에 전달)
}

// Tableau Cloud 접속 정보는 배포마다 다르다 — frontend/.env 에서 주입한다.
// VITE_TABLEAU_HOST 는 index.html 의 Embedding API 로드에도 같이 쓰인다.
const TABLEAU_HOST = import.meta.env.VITE_TABLEAU_HOST ?? 'https://YOUR-POD.online.tableau.com';
const TABLEAU_SITE = import.meta.env.VITE_TABLEAU_SITE ?? 'your-site';
const TABLEAU_BASE = `${TABLEAU_HOST}/t/${TABLEAU_SITE}`;

// 실제 Tableau Cloud(Samples 프로젝트) 뷰 2개
export const DASHBOARDS: DashboardItem[] = [
  {
    slug: 'superstore-overview', name: 'Superstore Overview', sub: 'Samples · Superstore',
    thumb: 'linear-gradient(135deg,#A50034,#D0577E)', certified: true, favorite: true,
    embedUrl: `${TABLEAU_BASE}/views/Superstore/Overview`,
    path: 'Superstore / Overview', viewPath: 'Superstore/Overview',
  },
  {
    slug: 'superstore-kpi', name: 'Superstore KPI Dashboard', sub: 'Samples · KPI',
    thumb: '#C7D6E6', certified: true, favorite: true,
    embedUrl: `${TABLEAU_BASE}/views/SuperstoreKPIDashboard/SuperstoreKPIDashboard`,
    path: 'SuperstoreKPIDashboard', viewPath: 'SuperstoreKPIDashboard/SuperstoreKPIDashboard',
  },
];

function Item({ d, active, onSelect }: { d: DashboardItem; active: boolean; onSelect: () => void }) {
  return (
    <button className={`dash-item ${active ? 'active' : ''}`} onClick={onSelect}>
      <span className="thumb" style={{ background: d.thumb }} />
      <span style={{ minWidth: 0 }}>
        <span className="nm" style={{ display: 'block' }}>{d.name}</span>
        <span className="sub">{d.sub}</span>
      </span>
      {d.certified && <span className="cert" title="인증 원본" />}
    </button>
  );
}

export default function DashboardList({ active, onSelect }: {
  active: string;
  onSelect: (slug: string) => void;
}) {
  return (
    <aside className="dash-list">
      <div className="head">
        <div className="title">대시보드</div>
        <div className="search">⌕ 대시보드 검색</div>
      </div>
      <div className="items">
        <div className="group">Samples</div>
        {DASHBOARDS.map(d => <Item key={d.slug} d={d} active={d.slug === active} onSelect={() => onSelect(d.slug)} />)}
      </div>
    </aside>
  );
}
