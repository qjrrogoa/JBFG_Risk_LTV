import { useEffect, useState } from "react";
import axios from "axios";
import {
  ComposedChart, Line, ReferenceLine, ReferenceArea,
  XAxis, YAxis, CartesianGrid, Tooltip, Legend, ResponsiveContainer,
} from "recharts";

const API = "http://localhost:8000";

const PERIODS = [
  { key: "3",  label: "3개월" },
  { key: "6",  label: "6개월" },
  { key: "12", label: "12개월" },
  { key: "36", label: "3년" },
  { key: "60", label: "5년" },
];

export default function DetailModal({ item, bank, baseDate, onClose }) {
  const [chartData, setChartData] = useState(null);
  const [loading, setLoading] = useState(true);

  const region   = item.region   ?? item.지역;
  const usage    = item.usage    ?? item.용도;
  const ltv      = item.current_ltv ?? item.ltv_val ?? item.LTV;
  const category = item.category ?? item.대분류 ?? "";

  const met = item.met ?? {};
  const avg = met.avg ?? {};
  const cnt = met.count ?? {};

  const hasLlm = item.conservative_ltv != null || item.relaxed_ltv != null;

  useEffect(() => {
    setLoading(true);
    const params = { bank, region, usage };
    if (baseDate) params.base_date = baseDate;
    axios.get(`${API}/api/chart-data`, { params })
      .then((res) => setChartData(res.data))
      .catch(() => setChartData({ ltv, points: [] }))
      .finally(() => setLoading(false));
  }, [bank, region, usage, baseDate]);

  // 배경 클릭 시 닫기
  function handleBackdrop(e) {
    if (e.target === e.currentTarget) onClose();
  }

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 backdrop-blur-sm"
      onClick={handleBackdrop}
    >
      <div className="bg-white rounded-2xl shadow-2xl w-full max-w-3xl max-h-[90vh] overflow-y-auto mx-4 border border-slate-100">
        {/* 헤더 */}
        <div className="flex items-start justify-between px-6 py-5 border-b border-slate-100 sticky top-0 bg-white z-10 rounded-t-2xl">
          <div>
            <div className="flex items-center gap-2">
              <span className="text-xs font-bold text-blue-600 bg-blue-50 px-2 py-0.5 rounded-full">{category}</span>
              <span className="text-xs text-slate-400">{region}</span>
            </div>
            <h2 className="text-xl font-black text-slate-900 mt-1">{usage}</h2>
            <p className="text-sm text-slate-500 mt-0.5">현재 LTV <span className="font-bold text-slate-700">{ltv}%</span></p>
          </div>
          <button onClick={onClose} className="text-slate-400 hover:text-slate-700 transition-colors p-1 rounded-lg hover:bg-slate-100">
            <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
            </svg>
          </button>
        </div>

        <div className="p-6 space-y-6">
          {/* LLM 권고안 카드 */}
          {hasLlm && (
            <div className="grid grid-cols-3 gap-3">
              <LtvCard label="현재 LTV" value={`${ltv}%`} base />
              <LtvCard
                label="보수적 안"
                value={item.conservative_ltv != null ? `${item.conservative_ltv}%` : "—"}
                delta={item.conservative_delta}
              />
              <LtvCard
                label="완화적 안"
                value={item.relaxed_ltv != null ? `${item.relaxed_ltv}%` : "—"}
                delta={item.relaxed_delta}
              />
            </div>
          )}

          {/* 권고 사유 */}
          {item.reason && (
            <div className="bg-slate-50 border border-slate-100 rounded-xl p-4">
              <p className="text-xs font-bold text-slate-500 uppercase tracking-wider mb-2">권고안 산출 사유</p>
              <p className="text-sm text-slate-600 leading-relaxed"
                dangerouslySetInnerHTML={{ __html: item.reason }} />
            </div>
          )}

          {/* 기간별 통계 카드 */}
          {Object.keys(avg).length > 0 && (
            <div>
              <p className="text-xs font-bold text-slate-500 uppercase tracking-wider mb-3">기간별 매각 통계</p>
              <div className="grid grid-cols-5 gap-2">
                {PERIODS.map(({ key, label }) => {
                  const a = avg[key];
                  const c = cnt[key] ?? 0;
                  const diff = a != null ? a - ltv : null;
                  return (
                    <div key={key} className="bg-slate-50 border border-slate-100 rounded-xl p-3 text-center">
                      <div className="text-[10px] font-semibold text-slate-400 uppercase mb-1.5">{label}</div>
                      <div className="text-lg font-black text-slate-900">
                        {a != null ? `${Number(a).toFixed(1)}%` : "—"}
                      </div>
                      {diff != null && (
                        <div className={`text-xs font-semibold mt-0.5 ${diff < 0 ? "text-red-500" : diff > 0 ? "text-emerald-600" : "text-slate-400"}`}>
                          {diff > 0 ? "+" : ""}{diff.toFixed(1)}%p
                        </div>
                      )}
                      <div className="text-[10px] text-slate-400 mt-0.5">{c}건</div>
                    </div>
                  );
                })}
              </div>
            </div>
          )}

          {/* 차트 */}
          <div>
            <p className="text-xs font-bold text-slate-500 uppercase tracking-wider mb-3">
              이동평균 기반 낙찰가율 추이 (최근 2년)
            </p>
            {loading ? (
              <div className="flex items-center justify-center h-52 text-slate-400">
                <div className="w-5 h-5 border-4 border-blue-500 border-t-transparent rounded-full animate-spin mr-2" />
                차트 로딩 중...
              </div>
            ) : chartData?.points?.length > 0 ? (
              <LtvChart points={chartData.points} ltv={chartData.ltv ?? ltv} />
            ) : (
              <div className="flex items-center justify-center h-52 text-slate-400 text-sm bg-slate-50 rounded-xl border border-slate-100">
                차트 데이터가 부족합니다.
              </div>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}

function LtvCard({ label, value, delta, base }) {
  const deltaColor = delta == null ? null : delta < 0 ? "#dc2626" : delta > 0 ? "#16a34a" : "#94a3b8";
  return (
    <div className={`rounded-xl p-4 text-center border ${base ? "bg-slate-900 border-slate-800" : "bg-white border-slate-100 shadow-sm"}`}>
      <div className={`text-xs font-semibold mb-2 ${base ? "text-slate-400" : "text-slate-500"}`}>{label}</div>
      <div className={`text-2xl font-black ${base ? "text-white" : "text-slate-900"}`}>{value}</div>
      {delta != null && (
        <div className="text-sm font-bold mt-1" style={{ color: deltaColor }}>
          {delta > 0 ? "+" : ""}{delta}%p
        </div>
      )}
      {delta == null && !base && <div className="text-sm text-slate-300 mt-1">—</div>}
    </div>
  );
}

function LtvChart({ points, ltv }) {
  const allVals = points.flatMap((p) => [p.monthly, p.ma3, p.ma6, p.ma12, ltv].filter((v) => v != null));
  const yMin = Math.max(0,   Math.min(...allVals) - 8);
  const yMax = Math.max(100, Math.max(...allVals) + 8);

  return (
    <ResponsiveContainer width="100%" height={320}>
      <ComposedChart data={points} margin={{ top: 8, right: 24, left: 0, bottom: 0 }}>
        <CartesianGrid strokeDasharray="3 3" stroke="#f1f5f9" />

        <ReferenceArea y1={ltv - 5}  y2={ltv + 5}  fill="#22c55e" fillOpacity={0.07} />
        <ReferenceArea y1={ltv + 5}  y2={ltv + 10} fill="#f59e0b" fillOpacity={0.09} />
        <ReferenceArea y1={ltv - 10} y2={ltv - 5}  fill="#f59e0b" fillOpacity={0.09} />
        <ReferenceArea y1={ltv + 10} y2={yMax}      fill="#ef4444" fillOpacity={0.04} />
        <ReferenceArea y1={yMin}     y2={ltv - 10}  fill="#ef4444" fillOpacity={0.04} />

        <XAxis dataKey="month" tick={{ fontSize: 11, fill: "#94a3b8" }} tickLine={false} axisLine={false} />
        <YAxis domain={[yMin, yMax]} tick={{ fontSize: 11, fill: "#94a3b8" }} tickLine={false} axisLine={false}
          tickFormatter={(v) => `${v}%`} />
        <Tooltip
          formatter={(val, name) => val != null ? [`${Number(val).toFixed(1)}%`, name] : ["-", name]}
          labelStyle={{ fontWeight: "bold", color: "#0f172a", marginBottom: 4 }}
          contentStyle={{ border: "1px solid #e2e8f0", borderRadius: 10, fontSize: 12, boxShadow: "0 4px 20px rgba(0,0,0,0.1)" }}
        />
        <Legend wrapperStyle={{ fontSize: 11, paddingTop: 12 }} iconType="line" />

        <ReferenceLine y={ltv} stroke="#ef4444" strokeWidth={1.5} strokeDasharray="5 3"
          label={{ value: `LTV ${ltv}%`, position: "right", fontSize: 11, fill: "#ef4444", fontWeight: 700 }} />

        <Line dataKey="monthly" name="월별 평균" stroke="#cbd5e1" strokeWidth={1}
          strokeDasharray="3 3" dot={{ r: 2, fill: "#cbd5e1" }} connectNulls />
        <Line dataKey="ma3"  name="3M 이동평균"  stroke="#60a5fa" strokeWidth={1.5} strokeDasharray="4 2" dot={false} connectNulls />
        <Line dataKey="ma6"  name="6M 이동평균"  stroke="#a78bfa" strokeWidth={2}   dot={false} connectNulls />
        <Line dataKey="ma12" name="12M 이동평균" stroke="#fb923c" strokeWidth={2.5} dot={false} connectNulls />
      </ComposedChart>
    </ResponsiveContainer>
  );
}
