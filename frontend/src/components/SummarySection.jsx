export default function SummarySection({ urgentList, onItemClick }) {
  const downList = urgentList.filter((d) => d.direction === "▼");
  const redList = downList.filter((d) => d.tone === "red");
  const yellowList = downList.filter((d) => d.tone === "yellow");

  const redCnt = redList.length;
  const yellowCnt = yellowList.length;

  // 요약 문구 생성
  const topRegions = [...new Set(redList.slice(0, 3).map((d) => d.region))];
  const regionStr = topRegions.length ? topRegions.join(" · ") : "-";

  return (
    <div className="space-y-4">
      {/* KPI 카드 2개 */}
      <div className="grid grid-cols-2 gap-4">
        {/* 조정 대상 카드 */}
        <div className="bg-rose-50 border border-rose-100 rounded-2xl p-5">
          <div className="text-xs font-semibold text-rose-600 uppercase tracking-wider flex items-center gap-1.5">
            <span className="w-1.5 h-1.5 rounded-full bg-rose-500 shrink-0" />
            조정 대상
          </div>
          <div className="text-5xl font-black text-rose-600 leading-none tabular-nums mt-3">{redCnt}</div>
          <div className="text-xs text-slate-400 mt-1.5">격차 10%p 이상 · 하락 추세</div>
        </div>

        {/* 검토 대상 카드 */}
        <div className="bg-amber-50 border border-amber-100 rounded-2xl p-5">
          <div className="text-xs font-semibold text-amber-600 uppercase tracking-wider flex items-center gap-1.5">
            <span className="w-1.5 h-1.5 rounded-full bg-amber-500 shrink-0" />
            검토 대상
          </div>
          <div className="text-5xl font-black text-amber-600 leading-none tabular-nums mt-3">{yellowCnt}</div>
          <div className="text-xs text-slate-400 mt-1.5">격차 5~10%p · 하락 추세</div>
        </div>
      </div>

      {/* 요약 텍스트 바 */}
      <div className="bg-white border border-slate-200 shadow-sm rounded-xl px-5 py-3.5 text-sm text-slate-600">
        이번달 LTV 점검 결과, 총{" "}
        <span className="font-bold text-rose-600">{redCnt}건</span>의 조정 대상과{" "}
        <span className="font-bold text-amber-600">{yellowCnt}건</span>의 검토 대상이 확인되었습니다.
        {redCnt > 0 && (
          <>
            {" "}주요 조정 대상 지역은 <span className="font-semibold text-slate-900">{regionStr}</span> 등입니다.
          </>
        )}
      </div>

      {/* 2칼럼 패널 */}
      <div className="grid grid-cols-2 gap-4">
        <SummaryPanel
          tone="red"
          title="조정 대상"
          desc="격차 10%p 이상 & 하락 추세"
          items={redList}
          onItemClick={onItemClick}
        />
        <SummaryPanel
          tone="yellow"
          title="검토 대상"
          desc="격차 5~10%p & 하락 추세"
          items={yellowList}
          onItemClick={onItemClick}
        />
      </div>
    </div>
  );
}

function SummaryPanel({ tone, title, desc, items, onItemClick }) {
  const isRed = tone === "red";

  const dotCls  = isRed ? "bg-rose-500"  : "bg-amber-500";
  const badgeCls = isRed
    ? "bg-rose-50 text-rose-600 border border-rose-200"
    : "bg-amber-50 text-amber-600 border border-amber-200";
  const recoCls = isRed ? "text-rose-600 font-bold" : "text-amber-600 font-bold";
  const deltaCls = isRed ? "text-rose-500" : "text-amber-500";

  return (
    <div className="bg-white border border-slate-200 shadow-sm rounded-2xl overflow-hidden">
      {/* 패널 헤더 */}
      <div className="bg-slate-50 px-4 py-3 flex items-center justify-between border-b border-slate-200">
        <div className="flex items-center gap-2">
          <span className={`inline-flex items-center gap-1.5 text-xs font-bold px-2.5 py-0.5 rounded-full ${badgeCls}`}>
            <span className={`w-1.5 h-1.5 rounded-full shrink-0 ${dotCls}`} />
            {title}
          </span>
          <span className="text-xs text-slate-400">{desc}</span>
        </div>
        <span className="text-xs font-bold text-slate-400">{items.length}건</span>
      </div>

      {/* 컬럼 헤더 */}
      <div className="grid grid-cols-3 px-4 py-2 border-b border-slate-100 bg-slate-50/50">
        <span className="text-[11px] font-semibold text-slate-400 uppercase tracking-wider">지역 / 용도</span>
        <span className="text-[11px] font-semibold text-slate-400 uppercase tracking-wider text-center">현재</span>
        <span className="text-[11px] font-semibold text-slate-400 uppercase tracking-wider text-center">권고안</span>
      </div>

      {/* 리스트 */}
      <div className="overflow-y-auto max-h-64">
        {items.length === 0 ? (
          <div className="py-8 text-center text-sm text-slate-400">
            해당 대상이 없습니다.
          </div>
        ) : (
          items.map((item, idx) => {
            const reco = isRed ? item.conservative_ltv : item.relaxed_ltv;
            const delta = isRed ? item.conservative_delta : item.relaxed_delta;
            return (
              <button
                key={idx}
                onClick={() => onItemClick && onItemClick(item)}
                className="w-full grid grid-cols-3 px-4 py-2.5 hover:bg-slate-50 transition-all duration-150 border-b border-slate-100 last:border-0 text-left"
              >
                <div>
                  <div className="text-sm font-semibold text-slate-900 leading-tight">{item.region}</div>
                  <div className="text-xs text-slate-400 mt-0.5">{item.usage}</div>
                </div>
                <div className="text-sm font-bold text-slate-500 text-center self-center">
                  {item.current_ltv}%
                </div>
                <div className="text-center self-center">
                  <span className={`text-sm ${recoCls}`}>{reco}%</span>
                  {delta != null && (
                    <span className={`text-xs ml-1 ${deltaCls}`}>
                      ({delta > 0 ? "+" : ""}{delta}%p)
                    </span>
                  )}
                </div>
              </button>
            );
          })
        )}
      </div>
    </div>
  );
}
