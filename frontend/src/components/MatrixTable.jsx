import { useState } from "react";

const PERIODS = ["3개월", "6개월", "12개월", "3년", "5년"];

const DOT = {
  red:    { color: "#ef4444", shadow: "rgba(239,68,68,0.35)",   label: "부적정" },
  yellow: { color: "#f59e0b", shadow: "rgba(245,158,11,0.35)",  label: "주의"   },
  green:  { color: "#22c55e", shadow: "rgba(34,197,94,0.35)",   label: "적정"   },
  gray:   { color: "#cbd5e1", shadow: "none",                   label: "모수 부족" },
};

function StatusDot({ val }) {
  const d = DOT[val] ?? DOT.gray;
  if (val === "gray" || !val) {
    return <span className="inline-block w-3 h-3 rounded-full bg-slate-300" title={d.label} />;
  }
  return (
    <span
      className="inline-block w-3 h-3 rounded-full"
      title={d.label}
      style={{ background: d.color, boxShadow: `0 0 0 3px ${d.shadow}` }}
    />
  );
}

const STATUS_FILTER = ["전체", "조정 대상", "검토 대상"];

export default function MatrixTable({ matrixData, urgentList, onRowClick }) {
  const [regionFilter, setRegionFilter] = useState("전체 지역");
  const [categoryFilter, setCategoryFilter] = useState("전체");
  const [usageFilter, setUsageFilter] = useState("전체");
  const [statusFilter, setStatusFilter] = useState("전체");

  const regions   = [...new Set(matrixData.map((d) => d.지역))].filter(Boolean).sort();
  const categories = [...new Set(matrixData.map((d) => d.대분류))].filter(Boolean).sort();
  const usages    = [...new Set(matrixData.map((d) => d.용도))].filter(Boolean).sort();

  const filtered = matrixData.filter((row) => {
    if (regionFilter !== "전체 지역" && row.지역 !== regionFilter) return false;
    if (categoryFilter !== "전체"    && row.대분류 !== categoryFilter) return false;
    if (usageFilter !== "전체"       && row.용도 !== usageFilter) return false;
    if (statusFilter === "조정 대상" && row.signal_tone !== "red")    return false;
    if (statusFilter === "검토 대상" && row.signal_tone !== "yellow") return false;
    // 데이터가 하나도 없는 행 제거
    const hasCnt = PERIODS.some((p) => (row[`${p}_count`] ?? 0) >= 1);
    if (!hasCnt) return false;
    return true;
  });

  function handleReset() {
    setRegionFilter("전체 지역");
    setCategoryFilter("전체");
    setUsageFilter("전체");
    setStatusFilter("전체");
  }

  function handleRowClick(row) {
    if (!onRowClick) return;
    // urgent_list에서 LLM 데이터 매칭
    const matched = urgentList?.find((u) => u.region === row.지역 && u.usage === row.용도);
    onRowClick(row, matched ?? null);
  }

  return (
    <div className="space-y-4">
      {/* 섹션 헤더 */}
      <div>
        <h2 className="text-sm font-bold text-slate-900 flex items-center gap-2">
          <span className="w-0.5 h-4 rounded-full bg-blue-500 inline-block" />
          지역·용도별 LTV 적정성 매트릭스
        </h2>
        <p className="text-xs text-slate-400 mt-1 ml-3">상태·지역·용도 필터로 필요한 항목만 추려서 기간별 적정성을 비교할 수 있습니다.</p>
      </div>

      {/* 필터 바 */}
      <div className="bg-white border border-slate-200 shadow-sm rounded-2xl p-4 flex flex-wrap items-center gap-3">
        {/* 상태 필터 (pill 라디오) */}
        <div className="flex gap-1 bg-slate-100 rounded-xl p-1">
          {STATUS_FILTER.map((s) => (
            <button
              key={s}
              onClick={() => setStatusFilter(s)}
              className={`px-3 py-1 rounded-lg text-xs font-semibold transition-all duration-150 ${
                statusFilter === s ? "bg-white text-slate-900 shadow-sm" : "text-slate-500 hover:text-slate-700"
              }`}
            >
              {s === "조정 대상" ? (
                <span className="flex items-center gap-1.5">
                  <span className="w-1.5 h-1.5 rounded-full bg-rose-500" />조정
                </span>
              ) : s === "검토 대상" ? (
                <span className="flex items-center gap-1.5">
                  <span className="w-1.5 h-1.5 rounded-full bg-amber-500" />검토
                </span>
              ) : s}
            </button>
          ))}
        </div>

        <div className="w-px h-5 bg-slate-200" />

        <Select label="지역" value={regionFilter} onChange={setRegionFilter}
          options={["전체 지역", ...regions]} />
        <Select label="대분류" value={categoryFilter} onChange={setCategoryFilter}
          options={["전체", ...categories]} />
        <Select label="용도" value={usageFilter} onChange={setUsageFilter}
          options={["전체", ...usages]} />

        <button
          onClick={handleReset}
          className="ml-auto text-slate-500 hover:text-slate-700 border border-slate-300 hover:border-slate-400 rounded-lg px-3 py-1.5 text-xs font-medium transition-all duration-150"
        >
          초기화
        </button>
        <span className="bg-slate-100 text-slate-500 text-xs px-2.5 py-1 rounded-full font-medium">{filtered.length}건</span>
      </div>

      {/* 범례 */}
      <div className="flex items-center gap-5 px-1">
        {Object.entries(DOT).map(([key, d]) => (
          <div key={key} className="flex items-center gap-1.5">
            <StatusDot val={key} />
            <span className="text-xs text-slate-500">{d.label}</span>
          </div>
        ))}
      </div>

      {/* 테이블 카드 */}
      <div className="bg-white border border-slate-200 shadow-sm rounded-2xl overflow-hidden">
        {filtered.length === 0 ? (
          <div className="py-14 text-center text-sm text-slate-400">
            현재 필터 조건에 맞는 항목이 없습니다.
          </div>
        ) : (
          <div className="overflow-x-auto overflow-y-auto max-h-[600px]">
            <table className="w-full text-sm">
              <thead className="sticky top-0 z-10">
                <tr className="bg-slate-50 border-b border-slate-200 backdrop-blur">
                  <Th>지역</Th>
                  <Th>대분류</Th>
                  <Th>용도</Th>
                  <Th center>기존 LTV</Th>
                  {PERIODS.map((p) => <Th key={p} center>{p}</Th>)}
                  <Th center>상세</Th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-100">
                {filtered.map((row, idx) => (
                  <tr key={idx} className="hover:bg-slate-50/70 transition-all duration-100">
                    <td className="px-4 py-2.5 font-semibold text-slate-900 whitespace-nowrap">{row.지역}</td>
                    <td className="px-4 py-2.5 text-slate-400 text-xs whitespace-nowrap">{row.대분류}</td>
                    <td className="px-4 py-2.5 text-slate-700 whitespace-nowrap">{row.용도}</td>
                    <td className="px-4 py-2.5 text-center font-bold text-slate-800 bg-slate-50 whitespace-nowrap">
                      {row.LTV}%
                    </td>
                    {PERIODS.map((p) => (
                      <td key={p} className="px-4 py-2.5 text-center whitespace-nowrap">
                        <div className="flex flex-col items-center gap-1">
                          <StatusDot val={row[p]} />
                          <span className="text-[10px] text-slate-400">{row[`${p}_count`] ?? 0}건</span>
                        </div>
                      </td>
                    ))}
                    <td className="px-4 py-2.5 text-center">
                      <button
                        onClick={() => handleRowClick(row)}
                        className="text-xs font-semibold text-blue-600 hover:text-blue-800 transition-all duration-150"
                      >
                        상세 →
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  );
}

function Th({ children, center }) {
  return (
    <th className={`px-4 py-2.5 text-[11px] font-semibold text-slate-400 uppercase tracking-wider whitespace-nowrap ${center ? "text-center" : "text-left"}`}>
      {children}
    </th>
  );
}

function Select({ label, value, onChange, options }) {
  return (
    <label className="flex items-center gap-1.5 text-xs font-semibold text-slate-500">
      {label}
      <select
        value={value}
        onChange={(e) => onChange(e.target.value)}
        className="bg-white border border-slate-300 text-slate-700 rounded-lg px-2 py-1.5 focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-transparent text-xs transition-all duration-150"
      >
        {options.map((o) => <option key={o}>{o}</option>)}
      </select>
    </label>
  );
}
