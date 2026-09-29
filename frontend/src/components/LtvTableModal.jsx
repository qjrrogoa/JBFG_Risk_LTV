import { useEffect, useState } from "react";
import axios from "axios";
import { API_BASE_URL } from "../config/api";

const API = API_BASE_URL;

function currentYm() {
  const now = new Date();
  return `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, "0")}`;
}

function isValidLtv(v) {
  const n = Number(v);
  return v !== "" && Number.isFinite(n) && n > 0 && n <= 100;
}

export default function LtvTableModal({ bank, baseDate, onClose, onSaved }) {
  const [data, setData] = useState([]);
  const [loading, setLoading] = useState(true);
  const [filter, setFilter] = useState("");
  const [editMode, setEditMode] = useState(false);
  const [drafts, setDrafts] = useState({}); // key: `${담보종류}||${지역}` -> 입력 문자열
  const [saving, setSaving] = useState(false);

  // 적용시작일은 항상 이번 달 1일이라, 과거 기준월 화면에서는 수정해도 그 달 계산에 반영되지 않음
  const editable = !baseDate || baseDate >= currentYm();

  const [reloadKey, setReloadKey] = useState(0);

  useEffect(() => {
    setLoading(true);
    axios.get(`${API}/api/ltv-table`, { params: { bank, base_date: baseDate } })
      .then(res => setData(res.data))
      .catch(err => console.error(err))
      .finally(() => setLoading(false));
  }, [bank, baseDate, reloadKey]);

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

  const cellKey = (row, col) => `${row.담보종류}||${col}`;

  // 원래 값과 다른 입력만 변경분으로 취급
  const changes = [];
  const invalidKeys = new Set();
  for (const row of data) {
    for (const col of regionCols) {
      const key = cellKey(row, col);
      if (!(key in drafts) || row[col] == null) continue;
      const v = drafts[key];
      if (!isValidLtv(v)) {
        invalidKeys.add(key);
      } else if (Number(v) !== Number(row[col])) {
        changes.push({ region: col, usage: row.담보종류, old_ltv: Number(row[col]), new_ltv: Number(v) });
      }
    }
  }

  function startEdit() {
    setDrafts({});
    setEditMode(true);
  }

  function cancelEdit() {
    if (changes.length > 0 && !window.confirm("수정 중인 내용이 있습니다. 취소하시겠습니까?")) return;
    setDrafts({});
    setEditMode(false);
  }

  function handleClose() {
    if (saving) return;
    if (editMode && changes.length > 0 && !window.confirm("적용하지 않은 수정 내용이 있습니다. 닫으시겠습니까?")) return;
    onClose();
  }

  async function handleApply() {
    if (invalidKeys.size > 0) {
      alert("LTV는 0 초과 100 이하의 숫자로 입력해 주세요.");
      return;
    }
    if (changes.length === 0) {
      alert("변경된 LTV 값이 없습니다.");
      return;
    }

    const MAX_LINES = 15;
    const lines = changes.slice(0, MAX_LINES).map(c => `· [${c.region}] ${c.usage}: ${c.old_ltv}% → ${c.new_ltv}%`);
    if (changes.length > MAX_LINES) lines.push(`… 외 ${changes.length - MAX_LINES}건`);
    const msg = `아래 ${changes.length}건의 LTV를 ${currentYm()}-01부터 적용하시겠습니까?\n\n${lines.join("\n")}\n\n적용 후 이번 달 시그널이 새 LTV 기준으로 다시 계산됩니다.`;
    if (!window.confirm(msg)) return;

    setSaving(true);
    try {
      const res = await axios.post(`${API}/api/ltv-table/bulk-save`, {
        bank,
        base_date: baseDate,
        changes: changes.map(({ region, usage, new_ltv }) => ({ region, usage, new_ltv })),
      }, { timeout: 600000 });
      alert(res.data.message || "적용되었습니다.");
      setDrafts({});
      setEditMode(false);
      setReloadKey(k => k + 1);
      onSaved && onSaved();
    } catch (err) {
      const detail = err.response?.data?.detail;
      alert(typeof detail === "string" ? detail : "LTV 적용에 실패했습니다.");
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="fixed inset-0 z-[60] flex items-center justify-center bg-black/60 backdrop-blur-md p-4" onClick={(e) => e.target === e.currentTarget && handleClose()}>
      <div className="relative bg-white rounded-[24px] shadow-2xl w-full max-w-[95vw] max-h-[90vh] overflow-hidden flex flex-col border border-slate-200">
        <div className="px-8 py-6 border-b border-slate-100 flex items-center justify-between bg-slate-50/50">
          <div>
            <h2 className="text-2xl font-black text-slate-900 flex items-center gap-3">
              <span className="bg-blue-600 text-white p-2 rounded-xl text-sm">📊</span>
              {bank} LTV 기준표
              {editMode && <span className="text-xs font-black bg-orange-100 text-orange-600 px-2 py-1 rounded-lg">수정 중</span>}
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
            <button onClick={handleClose} className="p-2 hover:bg-slate-200 rounded-full transition-colors">
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
                    <th key={col} className="sticky top-0 z-30 px-2 py-3 text-center text-xs font-black text-slate-400 uppercase tracking-widest bg-slate-50 border-b border-r border-slate-100 last:border-r-0 min-w-[45px] whitespace-nowrap">{col}</th>
                  ))}
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-100">
                {filteredData.map((row, idx) => (
                  <tr key={idx} className="hover:bg-blue-50/30 transition-colors group">
                    <td className="px-2 py-3 text-sm font-bold text-slate-500 bg-slate-50/30 border-r border-slate-100 whitespace-nowrap text-center">{row.구분}</td>
                    <td className="px-2 py-3 text-[15px] font-black text-slate-800 border-r border-slate-100 leading-tight break-keep">{row.담보종류}</td>
                    {regionCols.map(col => {
                      const isModified = row.modified_regions?.includes(col);
                      const key = cellKey(row, col);

                      if (editMode && row[col] != null) {
                        const value = key in drafts ? drafts[key] : String(Number(row[col]));
                        const isInvalid = invalidKeys.has(key);
                        const isChanged = !isInvalid && key in drafts && Number(drafts[key]) !== Number(row[col]);
                        return (
                          <td key={col} className={`px-1 py-2 text-center border-r border-slate-100 last:border-r-0 ${isInvalid ? "bg-red-50" : isChanged ? "bg-blue-50" : ""}`}>
                            <input
                              type="number"
                              min="0"
                              max="100"
                              step="1"
                              value={value}
                              disabled={saving}
                              title={isChanged ? `기존 ${Math.round(Number(row[col]))}%` : undefined}
                              onChange={e => setDrafts(prev => ({ ...prev, [key]: e.target.value }))}
                              className={`w-14 px-1 py-1 text-center text-[14px] font-black rounded-lg border focus:outline-none focus:ring-2 focus:ring-blue-500/30 ${isInvalid ? "border-red-400 text-red-600" : isChanged ? "border-blue-400 text-blue-700" : "border-slate-200 text-slate-700"}`}
                            />
                          </td>
                        );
                      }

                      return (
                        <td
                          key={col}
                          className={`px-2 py-3 text-center text-[15px] font-black border-r border-slate-100 last:border-r-0 transition-colors whitespace-nowrap ${isModified ? "bg-orange-50 text-orange-600" : "text-slate-700"
                            }`}
                        >
                          <span className={row[col] ? "" : "text-slate-300"}>
                            {row[col] != null ? `${Math.round(Number(row[col]))}%` : "-"}
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
          {editMode ? (
            <div>
              <span className="text-blue-600">{changes.length}건 변경</span>
              {invalidKeys.size > 0 && <span className="ml-3 text-red-500">{invalidKeys.size}건 입력 오류 (0 초과 100 이하)</span>}
              <span className="ml-3">* 적용 시 이번 달 1일부터 새 LTV가 적용되고 이번 달 시그널이 다시 계산됩니다.</span>
            </div>
          ) : (
            <div>
              * 위 데이터는 선택하신 기준일 시점에 실제 적용되었던 LTV 값입니다.
              {!editable && <span className="ml-2 text-slate-400">(과거 기준월은 수정할 수 없습니다)</span>}
            </div>
          )}
          <div className="flex items-center gap-2">
            {editMode ? (
              <>
                <button onClick={cancelEdit} disabled={saving} className="px-6 py-2 bg-white text-slate-600 border border-slate-200 rounded-xl hover:bg-slate-100 transition-all disabled:opacity-50">취소</button>
                <button onClick={handleApply} disabled={saving || changes.length === 0 || invalidKeys.size > 0} className="px-6 py-2 bg-blue-600 text-white rounded-xl hover:bg-blue-500 transition-all shadow-lg active:transform active:scale-95 disabled:opacity-40 disabled:cursor-not-allowed">
                  {saving ? "적용 중..." : "적용"}
                </button>
              </>
            ) : (
              <>
                {editable && data.length > 0 && (
                  <button onClick={startEdit} className="px-6 py-2 bg-white text-blue-600 border border-blue-200 rounded-xl hover:bg-blue-50 transition-all">✏️ 수정</button>
                )}
                <button onClick={handleClose} className="px-6 py-2 bg-slate-800 text-white rounded-xl hover:bg-slate-700 transition-all shadow-lg active:transform active:scale-95">닫기</button>
              </>
            )}
          </div>
        </div>

        {saving && (
          <div className="absolute inset-0 z-40 flex flex-col items-center justify-center gap-4 bg-white/70 backdrop-blur-sm">
            <div className="w-12 h-12 border-4 border-blue-100 border-t-blue-600 rounded-full animate-spin" />
            <p className="font-bold text-slate-600">LTV 저장 및 시그널 재계산 중입니다...</p>
          </div>
        )}
      </div>
    </div>
  );
}
