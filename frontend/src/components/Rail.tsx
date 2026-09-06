// R — 워크스페이스 레일 (데모 범위: 대시보드 분석에 집중 — AI 허브/거버넌스 제외)
export default function Rail({ listOpen, onToggleList }: {
  listOpen: boolean;
  onToggleList: () => void;
}) {
  return (
    <nav className="rail">
      <span className="logo" title="Insight Analytics">IA</span>
      <div className="divider" />
      <button className={`item ${listOpen ? 'active' : ''}`}
        title={listOpen ? '대시보드 목록 숨기기' : '대시보드 목록 보기'}
        onClick={onToggleList}>▤</button>
      <button className="me" title="정광 · 분석가">JK</button>
    </nav>
  );
}
