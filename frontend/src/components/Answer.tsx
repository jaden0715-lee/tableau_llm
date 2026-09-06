// Answer.tsx — 답변 렌더: 마크다운(표 포함) + ```chart 블록 → 간단 SVG 차트
// 데이터 배관 없이, 모델이 답변에 명시한 표/차트를 그대로 렌더 (docs/ 계획 참고)
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';

interface ChartSpec {
  type: 'bar' | 'line';
  title?: string;
  x: string[];
  series: { name: string; data: number[] }[];
}

const PALETTE = ['#A50034', '#D0577E', '#E8A0B8', '#6B2740'];

function fmt(n: number): string {
  const a = Math.abs(n);
  if (a >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`;
  if (a >= 1_000) return `${(n / 1_000).toFixed(1)}K`;
  return `${Math.round(n * 100) / 100}`;
}

/** 유효한 ChartSpec인지 검증 (숫자 데이터 + 길이 일치) */
function validSpec(o: unknown): o is ChartSpec {
  const s = o as ChartSpec;
  if (!s || (s.type !== 'bar' && s.type !== 'line')) return false;
  if (!Array.isArray(s.x) || !s.x.length) return false;
  if (!Array.isArray(s.series) || !s.series.length) return false;
  return s.series.every(se => Array.isArray(se.data)
    && se.data.length === s.x.length
    && se.data.every(v => typeof v === 'number' && isFinite(v)));
}

function MiniChart({ spec }: { spec: ChartSpec }) {
  const W = 360, H = 210, padL = 38, padR = 12, padT = 20, padB = 42;
  const plotW = W - padL - padR, plotH = H - padT - padB;
  const all = spec.series.flatMap(s => s.data);
  const max = Math.max(0, ...all), min = Math.min(0, ...all);
  const range = max - min || 1;
  const y = (v: number) => padT + plotH - ((v - min) / range) * plotH;
  const nS = spec.series.length;

  return (
    <div className="chart-card">
      {spec.title && <div className="chart-title">{spec.title}</div>}
      <svg viewBox={`0 0 ${W} ${H}`} width="100%" role="img" aria-label={spec.title || 'chart'}>
        {/* y=0(또는 하단) 기준선 */}
        <line x1={padL} y1={y(Math.max(min, 0))} x2={W - padR} y2={y(Math.max(min, 0))} stroke="#E3E5E9" />
        <text x={padL - 6} y={padT + 4} textAnchor="end" fontSize="8.5" fill="#9AA0A8">{fmt(max)}</text>
        {spec.type === 'bar'
          ? spec.x.map((label, i) => {
              const groupW = plotW / spec.x.length;
              const barW = Math.min(28, (groupW * 0.7) / nS);
              const gx = padL + groupW * i + groupW / 2;
              return (
                <g key={i}>
                  {spec.series.map((se, si) => {
                    const v = se.data[i];
                    const bx = gx - (nS * barW) / 2 + si * barW;
                    const y0 = y(Math.max(min, 0)), yv = y(v);
                    return (
                      <g key={si}>
                        <rect x={bx} y={Math.min(y0, yv)} width={barW - 1.5}
                          height={Math.abs(yv - y0)} fill={PALETTE[si % PALETTE.length]} rx="1.5" />
                        {nS === 1 && <text x={bx + (barW - 1.5) / 2} y={yv - 3}
                          textAnchor="middle" fontSize="8" fill="#6B7078">{fmt(v)}</text>}
                      </g>
                    );
                  })}
                  <text x={gx} y={H - padB + 13} textAnchor="middle" fontSize="8.5" fill="#6B7078">
                    {label.length > 7 ? label.slice(0, 6) + '…' : label}
                  </text>
                </g>
              );
            })
          : spec.series.map((se, si) => {
              const step = plotW / Math.max(1, spec.x.length - 1);
              const pts = se.data.map((v, i) => `${padL + step * i},${y(v)}`).join(' ');
              return (
                <g key={si}>
                  <polyline points={pts} fill="none" stroke={PALETTE[si % PALETTE.length]} strokeWidth="2" />
                  {se.data.map((v, i) => (
                    <circle key={i} cx={padL + step * i} cy={y(v)} r="2.5" fill={PALETTE[si % PALETTE.length]} />
                  ))}
                </g>
              );
            })}
        {spec.type === 'line' && spec.x.map((label, i) => {
          const step = plotW / Math.max(1, spec.x.length - 1);
          return (
            <text key={i} x={padL + step * i} y={H - padB + 13} textAnchor="middle" fontSize="8.5" fill="#6B7078">
              {label.length > 7 ? label.slice(0, 6) + '…' : label}
            </text>
          );
        })}
      </svg>
      {nS > 1 && (
        <div className="chart-legend">
          {spec.series.map((se, si) => (
            <span key={si}><i style={{ background: PALETTE[si % PALETTE.length] }} />{se.name}</span>
          ))}
        </div>
      )}
    </div>
  );
}

/** 로컬 모델이 흔히 내는 깨진 GFM 표 구분행(예: `| :---/---|---:|`)을 교정.
 * 헤더 다음의 구분행 셀을 정렬 마커(---, :---, ---:, :---:)로 정규화 → remark-gfm이 표로 인식. */
function normalizeMd(src: string): string {
  const lines = src.split('\n');
  for (let i = 1; i < lines.length; i++) {
    const cur = lines[i].trim(), prev = lines[i - 1].trim();
    if (!cur.includes('|') || !prev.includes('|')) continue;         // 앞줄(헤더)·현재줄 모두 파이프 필요
    const cells = cur.replace(/^\|/, '').replace(/\|$/, '').split('|').map(c => c.trim());
    const isDelim = cells.length >= 1 && cells.every(c => /^[:\-/ ]*$/.test(c)) && cells.some(c => c.includes('-'));
    if (!isDelim) continue;                                          // 데이터행·수평선(---)은 건드리지 않음
    const fixed = cells.map(c => {
      const l = c.startsWith(':'), r = c.endsWith(':');
      return l && r ? ':---:' : r ? '---:' : l ? ':---' : '---';
    });
    lines[i] = '| ' + fixed.join(' | ') + ' |';
  }
  return lines.join('\n');
}

type Part = { kind: 'md'; text: string } | { kind: 'chart'; spec: ChartSpec };

/** 본문에서 ```chart 블록을 분리. 파싱 실패/미완성(스트리밍)은 md로 남겨 그대로 렌더 */
function splitParts(content: string): Part[] {
  const re = /```chart\s*\n([\s\S]*?)```/g;
  const parts: Part[] = [];
  let last = 0, m: RegExpExecArray | null;
  while ((m = re.exec(content))) {
    let spec: ChartSpec | null = null;
    try { const o = JSON.parse(m[1]); if (validSpec(o)) spec = o; } catch { /* 폴백: md로 */ }
    if (spec) {
      if (m.index > last) parts.push({ kind: 'md', text: content.slice(last, m.index) });
      parts.push({ kind: 'chart', spec });
      last = re.lastIndex;
    }
    // spec 무효면 last 유지 → 해당 블록은 뒤의 md에 포함되어 코드로 표시
  }
  if (last < content.length) parts.push({ kind: 'md', text: content.slice(last) });
  return parts.length ? parts : [{ kind: 'md', text: content }];
}

export default function Answer({ content }: { content: string }) {
  return (
    <div className="answer">
      {splitParts(content).map((p, i) =>
        p.kind === 'chart'
          ? <MiniChart key={i} spec={p.spec} />
          : <ReactMarkdown key={i} remarkPlugins={[remarkGfm]}>{normalizeMd(p.text)}</ReactMarkdown>,
      )}
    </div>
  );
}
