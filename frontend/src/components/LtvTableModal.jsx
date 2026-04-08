import { useEffect, useState } from "react";
import axios from "axios";

const API = "http://localhost:8000";

export default function LtvTableModal({ bank, baseDate, onClose }) {
  const [data, setData] = useState([]);
  const [loading, setLoading] = useState(true);
  const [filter, setFilter] = useState("");

  useEffect(() => {
    setLoading(true);
    axios.get(`${API}/api/ltv-table`, { params: { bank, base_date: baseDate } })
      .then(res => setData(res.data))
      .catch(err => console.error(err))
      .finally(() => setLoading(false));
  }, [bank, baseDate]);

  const filteredData = data.filter(row =>
    (row.구분 || "").includes(filter) ||
    (row.담보종류 || "").includes(filter)
  );

  // 컬럼 추출 (구분, 담보종류, 적용시작일, modified_regions 제외한 나머지가 지역)
  // 단, 모든 행에서 데이터가 없는(null) 지역은 제외하여 표 너비를 확보하고 해당 은행의 기준만 노출하도록 함
  const allCols = data.length > 0 ? Object.keys(data[0]) : [];
  const regionCols = allCols.filter(c =>
    !["구분", "담보종류", "적용시작일", "modified_regions"].includes(c) &&
    data.some(row => row[c] !== null && row[c] !== undefined && row[c] !== "")
  );


  return (
    <div className="fixed inset-0 z-[60] flex items-center justify-center bg-black/60 backdrop-blur-md p-4" onClick={(e) => e.target === e.currentTarget && onClose()}>
      <div className="bg-white rounded-[24px] shadow-2xl w-full max-w-[95vw] max-h-[90vh] overflow-hidden flex flex-col border border-slate-200">
        <div className="px-8 py-6 border-b border-slate-100 flex items-center justify-between bg-slate-50/50">
          <div>
            <h2 className="text-2xl font-black text-slate-900 flex items-center gap-3">
              <span className="bg-blue-600 text-white p-2 rounded-xl text-sm">📊</span>
              {bank} LTV 기준표
            </h2>
            <p className="text-sm text-slate-500 font-bold mt-1">기준일: {baseDate || "최신"} (적용된 시점의 기준 정보를 표시합니다)</p>
          </div>
          <div className="flex items-center gap-4">
            <div className="relative">
              <input
                type="text"
                placeholder="구분/담보종류 검색..."
                value={filter}
                onChange={e => setFilter(e.target.value)}
                className="pl-10 pr-4 py-2 bg-white border border-slate-200 rounded-xl text-sm focus:outline-none focus:ring-2 focus:ring-blue-500/20 focus:border-blue-500 transition-all font-bold w-64"
              />
              <span className="absolute left-3.5 top-2.5 text-slate-400">🔍</span>
            </div>
            <button onClick={onClose} className="p-2 hover:bg-slate-200 rounded-full transition-colors">
              <svg className="w-6 h-6 text-slate-400" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2.5} d="M6 18L18 6M6 6l12 12" /></svg>
            </button>
          </div>
        </div>

        <div className="flex-1 overflow-auto custom-scrollbar">
          {loading ? (
            <div className="h-64 flex flex-col items-center justify-center gap-4">
              <div className="w-12 h-12 border-4 border-blue-100 border-t-blue-600 rounded-full animate-spin" />
              <p className="font-bold text-slate-400">데이터를 불러오는 중입니다...</p>
            </div>
          ) : data.length === 0 ? (
            <div className="h-64 flex items-center justify-center text-slate-400 font-bold">데이터가 없습니다.</div>
          ) : (
            <table className="w-full border-collapse table-auto">
              <thead>
                <tr className="z-20">
                  <th className="sticky top-0 z-30 p-4 text-center text-xs font-black text-slate-400 uppercase tracking-widest bg-slate-50 border-b border-r border-slate-100 first:rounded-tl-xl w-24 whitespace-nowrap">구분</th>
                  <th className="sticky top-0 z-30 p-4 text-center text-xs font-black text-slate-400 uppercase tracking-widest bg-slate-50 border-b border-r border-slate-100 w-40 whitespace-nowrap">담보종류</th>
                  {regionCols.map(col => (
                    <th key={col} className="sticky top-0 z-30 p-4 text-center text-xs font-black text-slate-400 uppercase tracking-widest bg-slate-50 border-b border-r border-slate-100 last:border-r-0 min-w-[70px] whitespace-nowrap">{col}</th>
                  ))}
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-100">
                {filteredData.map((row, idx) => (
                  <tr key={idx} className="hover:bg-blue-50/30 transition-colors group">
                    <td className="p-4 text-sm font-bold text-slate-500 bg-slate-50/30 border-r border-slate-100 whitespace-nowrap text-center">{row.구분}</td>
                    <td className="p-4 text-[15px] font-black text-slate-800 border-r border-slate-100 leading-tight break-keep">{row.담보종류}</td>
                    {regionCols.map(col => {
                      const isModified = row.modified_regions?.includes(col);
                      return (
                        <td
                          key={col}
                          className={`p-4 text-center text-[16px] font-black border-r border-slate-100 last:border-r-0 transition-colors whitespace-nowrap ${isModified ? "bg-orange-50 text-orange-600" : "text-slate-700"
                            }`}
                        >
                          <span className={row[col] ? "" : "text-slate-300"}>
                            {row[col] != null ? `${Number(row[col]).toFixed(1)}%` : "-"}
                            {isModified && <span className="ml-1 text-[10px] align-top">●</span>}
                          </span>
                        </td>
                      );
                    })}
                  </tr>
                ))}
              </tbody>
            </table>


          )}
        </div>

        <div className="px-8 py-4 bg-slate-50 border-t border-slate-100 flex justify-between items-center text-[13px] font-bold text-slate-400">
          <div>* 위 데이터는 선택하신 기준일 시점에 실제 적용되었던 LTV 값입니다.</div>
          <button onClick={onClose} className="px-6 py-2 bg-slate-800 text-white rounded-xl hover:bg-slate-700 transition-all shadow-lg active:transform active:scale-95">닫기</button>
        </div>
      </div>
    </div>
  );
}
