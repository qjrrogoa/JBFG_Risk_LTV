import { useEffect, useState } from "react";
import axios from "axios";
import {
  ComposedChart, Line, ReferenceLine, ReferenceArea, Bar,
  XAxis, YAxis, CartesianGrid, Tooltip, Legend, ResponsiveContainer,
} from "recharts";

const API = "http://localhost:8000";

const PERIODS = [
  { key: "3", label: "3개월" },
  { key: "6", label: "6개월" },
  { key: "12", label: "12개월" },
  { key: "36", label: "3년" },
  { key: "60", label: "5년" },
];

export default function DetailModal({ item, bank, baseDate, onClose }) {
  const [chartData, setChartData] = useState(null);
  const [loading, setLoading] = useState(true);
  const [showFullReason, setShowFullReason] = useState(false);

  const region = item.region ?? item.지역;
  const usage = item.usage ?? item.용도;
  const ltv = item.current_ltv ?? item.ltv_val ?? item.LTV;
  const category = item.category ?? item.대분류 ?? "";

  const met = item.met ?? {};
  const avg = met.avg ?? {};
  const cnt = met.count ?? {};

  const showAdvice = !item.hideAdvice && (item.conservative_ltv != null || item.relaxed_ltv != null);

  useEffect(() => {
    setLoading(true);
    const params = { bank, region, usage };
    if (baseDate) params.base_date = baseDate;
    axios.get(`${API}/api/chart-data`, { params })
      .then((res) => setChartData(res.data))
      .catch(() => setChartData({ ltv, points: [] }))
      .finally(() => setLoading(false));
  }, [bank, region, usage, baseDate]);

  function handleBackdrop(e) {
    if (e.target === e.currentTarget) onClose();
  }

  const last12mPoints = chartData?.points?.slice(-12) || [];

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 backdrop-blur-sm"
      onClick={handleBackdrop}
    >
      <div className="bg-white rounded-2xl shadow-2xl w-full max-w-6xl max-h-[90vh] overflow-y-auto mx-4 border border-slate-100 flex flex-col">
        {/* 헤더 */}
        <div className="flex items-start justify-between px-6 py-5 border-b border-slate-100 sticky top-0 bg-white z-10 rounded-t-2xl">
          <div>
            <div className="flex items-center gap-2">
              <span className="text-sm font-bold text-blue-600 bg-blue-50 px-2 py-0.5 rounded-full">{region}</span>
              <span className="text-sm text-slate-400 font-medium">{category}</span>
            </div>
            <h2 className="text-2xl font-black text-slate-900 mt-1">{usage}</h2>
          </div>
          <button onClick={onClose} className="text-slate-400 hover:text-slate-700 transition-colors p-1 rounded-lg hover:bg-slate-100">
            <svg className="w-6 h-6" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
            </svg>
          </button>
        </div>

        <div className="p-6 space-y-8">
          {/* 상단 통합 섹션 */}
          {showAdvice ? (
            <div className="grid grid-cols-1 xl:grid-cols-[1fr_1fr_1fr_3.5fr] gap-3 items-stretch">
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
              <div className="bg-slate-50 border border-slate-100 rounded-xl p-4 flex flex-col min-h-[100px] max-h-[180px] relative">
                <div className="flex items-center justify-between mb-2 shrink-0">
                  <p className="text-xs font-bold text-slate-500 uppercase tracking-widest">권고안 산출 사유</p>
                  {item.reason && (
                    <button
                      onClick={() => setShowFullReason(true)}
                      className="text-[11px] font-bold text-blue-600 bg-blue-50 hover:bg-blue-100 px-2.5 py-1 rounded transition-colors"
                    >
                      상세 보기
                    </button>
                  )}
                </div>
                <div className="overflow-y-auto pr-2 custom-scrollbar">
                  <p className="text-[14px] text-slate-700 leading-relaxed font-bold whitespace-pre-wrap break-keep">
                    {item.reason ? item.reason.replace(/<br>/g, "\n").replace(/\[.*?\]\([^)]+\)/g, "").replace(/\(\s*\)/g, "") : "—"}
                  </p>
                </div>
              </div>
            </div>
          ) : (
            <div className="grid grid-cols-1 md:grid-cols-[1.2fr_4fr] gap-3 items-stretch">
              <LtvCard label="현재 LTV" value={`${ltv}%`} base />
              <div className="bg-slate-50 border border-slate-100 rounded-xl p-5 flex flex-col justify-center">
                <p className="text-xs font-black text-slate-400 uppercase tracking-widest mb-2">Notice</p>
                <p className="text-[15px] font-bold text-slate-500 italic">이 항목은 긴급 조정 대상이 아니므로 상세 권고안이 제공되지 않았습니다.</p>
              </div>
            </div>
          )}

          {/* 기간별 통계 */}
          <div>
            <p className="text-sm font-bold text-slate-500 uppercase tracking-wider mb-4 px-1">기간별 매각 통계 분석</p>
            <div className="grid grid-cols-5 gap-2">
              {PERIODS.map(({ key, label }) => {
                const a = avg[key];
                const c = cnt[key] ?? 0;
                const diff = (a != null && ltv != null) ? a - ltv : null;
                return (
                  <div key={key} className="bg-slate-50 border border-slate-100 rounded-xl p-4 text-center">
                    <div className="text-xs font-bold text-slate-400 uppercase mb-2">{label}</div>
                    <div className="text-lg font-black text-slate-800 leading-none">
                      {a != null ? `${Number(a).toFixed(1)}%` : "—"}
                    </div>
                    {diff != null && (
                      <div className={`text-[12px] font-bold mt-1.5 ${diff < 0 ? "text-red-700" : diff > 0 ? "text-emerald-700" : "text-slate-400"}`}>
                        {diff > 0 ? "+" : ""}{diff.toFixed(1)}%p
                      </div>
                    )}
                    <div className="text-[11px] font-bold text-slate-400 mt-2">{c}건</div>
                  </div>
                );
              })}
            </div>
          </div>

          <div className="space-y-10">
            <div>
              <p className="text-sm font-bold text-slate-500 uppercase tracking-wider mb-4 px-1">이동평균 기반 낙찰가율 추이 (최근 2년)</p>
              {loading ? <SkeletonChart /> : chartData?.points?.length > 0 ? (
                <LtvChart points={chartData.points} ltv={chartData.ltv ?? ltv} />
              ) : <NoData />}
            </div>

            <div>
              <p className="text-sm font-bold text-slate-500 uppercase tracking-wider mb-4 px-1">12개월 시계열 상세 시각화</p>
              {loading ? <SkeletonChart /> : last12mPoints.length > 0 ? (
                <LtvTimeSeriesChart points={last12mPoints} ltv={chartData.ltv ?? ltv} />
              ) : <NoData />}
            </div>
          </div>
        </div>
      </div>

      {/* 권고안 풀 텍스트 오버레이 */}
      {showFullReason && (
        <div className="fixed inset-0 z-[60] flex items-center justify-center p-6 bg-black/60 shadow-2xl backdrop-blur-md" onClick={() => setShowFullReason(false)}>
          <div className="bg-white max-w-3xl w-full rounded-2xl shadow-2xl flex flex-col overflow-hidden max-h-[85vh]" onClick={e => e.stopPropagation()}>
            <div className="flex items-center justify-between px-6 py-4 border-b border-slate-100 bg-[#f8fafc]">
              <h3 className="text-lg font-black text-slate-800 flex items-center gap-2">
                🤖 AI 권고안 산출 상세 근거
              </h3>
              <button onClick={() => setShowFullReason(false)} className="p-1.5 text-slate-400 hover:text-slate-600 hover:bg-slate-200 rounded-lg transition-colors">
                <svg className="w-6 h-6" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2.5} d="M6 18L18 6M6 6l12 12" /></svg>
              </button>
            </div>
            <div className="p-8 overflow-y-auto custom-scrollbar">
              <p className="text-[16px] text-slate-700 leading-[1.8] font-medium whitespace-pre-wrap break-keep">
                {item.reason ? item.reason.replace(/<br>/g, "\n").replace(/\[.*?\]\([^)]+\)/g, "").replace(/\(\s*\)/g, "") : "입력된 권고안 상세 이유가 없습니다."}
              </p>
            </div>
            <div className="px-6 py-4 border-t border-slate-100 bg-slate-50 flex justify-end">
              <button
                onClick={() => setShowFullReason(false)}
                className="px-5 py-2 bg-slate-800 text-white font-bold text-sm rounded-xl hover:bg-slate-700 transition"
              >
                닫기
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

function LtvCard({ label, value, delta, base }) {
  const deltaColor = delta == null ? null : delta < 0 ? "#dc2626" : delta > 0 ? "#16a34a" : "#94a3b8";
  return (
    <div className="rounded-xl p-4 text-center border bg-white border-slate-200 shadow-sm flex flex-col justify-center min-h-[110px]">
      <div className="text-xs font-bold mb-2.5 text-slate-500">{label}</div>
      <div className="text-lg font-black text-slate-800 leading-none">{value}</div>
      {delta != null && (
        <div className="text-xs font-bold mt-2" style={{ color: deltaColor }}>
          {delta > 0 ? "+" : ""}{delta}%p
        </div>
      )}
      {delta == null && !base && <div className="text-xs text-slate-300 mt-2">—</div>}
    </div>
  );
}

function LtvChart({ points, ltv }) {
  const allVals = points.flatMap((p) => [p.ma3, p.ma6, p.ma12, ltv].filter((v) => v != null));
  const yMin = 0;
  const yMax = Math.max(100, Math.max(...allVals) + 12);

  return (
    <ResponsiveContainer width="100%" height={320}>
      <ComposedChart data={points} margin={{ top: 8, right: 60, left: 0, bottom: 0 }}>
        <CartesianGrid strokeDasharray="3 3" stroke="#f1f5f9" vertical={false} />

        {/* Background Zones (app_reworked Plotly style) */}
        <ReferenceArea y1={ltv - 5} y2={ltv + 5} fill="green" fillOpacity={0.1} />
        <ReferenceArea y1={ltv + 5} y2={ltv + 10} fill="yellow" fillOpacity={0.1} />
        <ReferenceArea y1={ltv - 10} y2={ltv - 5} fill="yellow" fillOpacity={0.1} />
        <ReferenceArea y1={ltv + 10} y2={yMax} fill="red" fillOpacity={0.05} />
        <ReferenceArea y1={0} y2={ltv - 10} fill="red" fillOpacity={0.05} />

        <XAxis
          dataKey="month"
          interval={2}
          tick={{ fontSize: 10, fill: "#64748b", fontWeight: 700 }}
          tickLine={false}
          axisLine={false}
        />
        <YAxis domain={[yMin, yMax]} tick={{ fontSize: 11, fill: "#64748b", fontWeight: 700 }} tickLine={false} axisLine={false} tickFormatter={(v) => `${v}%`} />

        <Tooltip
          formatter={(val, name) => val != null ? [`${Number(val).toFixed(1)}%`, name] : ["-", name]}
          labelStyle={{ fontWeight: "bold", color: "#0f172a", marginBottom: 6 }}
          contentStyle={{ border: "1px solid #e2e8f0", borderRadius: 10, fontSize: 12, boxShadow: "0 4px 15px rgba(0,0,0,0.05)" }}
        />
        <Legend wrapperStyle={{ fontSize: 11, fontWeight: 700, paddingTop: 10 }} iconType="line" />

        {/* Plotly Lines (app_reworked style) */}
        <ReferenceLine y={ltv} stroke="red" strokeWidth={1} label={{ value: `LTV ${ltv}%`, position: "right", fontSize: 11, fill: "red", fontWeight: 800 }} />
        <Line dataKey="ma3" name="3개월 이동평균" stroke="#1f77b4" strokeWidth={1.5} strokeDasharray="3 3" dot={false} connectNulls />
        <Line dataKey="ma6" name="6개월 이동평균" stroke="#9467bd" strokeWidth={2} dot={false} connectNulls />
        <Line dataKey="ma12" name="12개월 이동평균" stroke="#ff7f0e" strokeWidth={3} dot={false} connectNulls />
      </ComposedChart>
    </ResponsiveContainer>
  );
}

function LtvTimeSeriesChart({ points, ltv }) {
  const allVals = points.flatMap((p) => [p.monthly, ltv].filter((v) => v != null));
  const yMin = 0;
  const yMax = Math.max(100, Math.max(...allVals) + 15);

  return (
    <ResponsiveContainer width="100%" height={300}>
      <ComposedChart data={points} margin={{ top: 8, right: 60, left: 0, bottom: 0 }}>
        <CartesianGrid strokeDasharray="3 3" stroke="#f1f5f9" vertical={false} />

        <ReferenceArea y1={ltv - 5} y2={ltv + 5} fill="green" fillOpacity={0.1} />
        <ReferenceArea y1={ltv + 5} y2={ltv + 10} fill="yellow" fillOpacity={0.1} />
        <ReferenceArea y1={ltv - 10} y2={ltv - 5} fill="yellow" fillOpacity={0.1} />
        <ReferenceArea y1={ltv + 10} y2={yMax} fill="red" fillOpacity={0.05} />
        <ReferenceArea y1={0} y2={ltv - 10} fill="red" fillOpacity={0.05} />

        <XAxis dataKey="month" tick={{ fontSize: 11, fill: "#64748b", fontWeight: 700 }} tickLine={false} axisLine={false} />
        <YAxis domain={[yMin, yMax]} tick={{ fontSize: 11, fill: "#64748b", fontWeight: 700 }} tickLine={false} axisLine={false} tickFormatter={(v) => `${v}%`} />

        <Tooltip
          formatter={(val, name) => val != null ? [`${Number(val).toFixed(2)}%`, name] : ["-", name]}
          labelStyle={{ fontWeight: "bold", color: "#0f172a" }}
          contentStyle={{ border: "1px solid #e2e8f0", borderRadius: 10, fontSize: 12 }}
        />
        <Legend wrapperStyle={{ fontSize: 11, fontWeight: 700, paddingTop: 10 }} />

        <ReferenceLine y={ltv} stroke="black" strokeDasharray="3 3" strokeWidth={1} label={{ value: `LTV ${ltv}%`, position: "right", fontSize: 11, fill: "black", fontWeight: 700 }} />

        {/* Plotly line+markers (app_reworked style) */}
        <Line dataKey="monthly" name="낙찰율 (월별)" stroke="blue" strokeWidth={2} dot={{ r: 4, fill: "blue" }} connectNulls />

        {/* Dummy Legend Items to match app_reworked Plotly legend */}
        <Line name="현행유지" stroke="transparent" marker={{ symbol: "square", fill: "green" }} />
        <Line name="조정검토" stroke="transparent" marker={{ symbol: "square", fill: "#facc15" }} />
        <Line name="조정필요" stroke="transparent" marker={{ symbol: "square", fill: "#ef4444" }} />
      </ComposedChart>
    </ResponsiveContainer>
  );
}

function SkeletonChart() {
  return <div className="flex items-center justify-center h-48 text-slate-300 animate-pulse bg-slate-50 rounded-xl border border-slate-100">Chart Loading...</div>;
}

function NoData() {
  return <div className="flex items-center justify-center h-48 text-slate-400 text-sm bg-slate-50 rounded-xl border border-slate-100">데이터가 부족합니다.</div>;
}
