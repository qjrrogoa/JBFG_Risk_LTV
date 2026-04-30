import { useState } from "react";
import axios from "axios";
import { API_BASE_URL } from "../config/api";

const API = API_BASE_URL;

const FILTER_TABS = ["전체", "조정 대상", "검토 대상", "참고 대상"];

const BADGE = {
  red: { label: "조정 대상", dot: "bg-rose-500", cls: "bg-rose-50 text-rose-600 ring-1 ring-rose-200" },
  yellow: { label: "검토 대상", dot: "bg-amber-500", cls: "bg-amber-50 text-amber-600 ring-1 ring-amber-200" },
  green: { label: "참고 대상", dot: "bg-emerald-500", cls: "bg-emerald-50 text-emerald-600 ring-1 ring-emerald-200" },
};

function applyFilter(list, tab) {
  if (tab === "조정 대상") return list.filter((d) => d.tone === "red" && d.direction === "▼");
  if (tab === "검토 대상") return list.filter((d) => d.tone === "yellow" && d.direction === "▼");
  if (tab === "참고 대상") return list.filter((d) => d.direction === "▲");
  return list.filter((d) => d.tone === "red" || d.tone === "yellow");
}

function sortUrgent(list) {
  return [...list].sort((a, b) => {
    const getPriority = (row) => {
      if (row.direction === "▲") return 2;
      return row.tone === "red" ? 0 : 1;
    };

    const getDelta = (row) => row.tone === "red" ? (row.conservative_delta ?? 0) : (row.relaxed_delta ?? 0);

    const pA = getPriority(a);
    const pB = getPriority(b);
    if (pA !== pB) return pA - pB;

    return Math.abs(getDelta(b)) - Math.abs(getDelta(a));
  });
}

export default function UrgentTable({ urgentList, bank, baseDate, llmLoading, onRowClick, onSaved }) {
  const [tab, setTab] = useState("전체");
  const [finalLtvMap, setFinalLtvMap] = useState({});
  const [savingKey, setSavingKey] = useState(null);
  const [savedMsg, setSavedMsg] = useState({});

  const filtered = sortUrgent(applyFilter(urgentList, tab));

  function getDefaultLtv(item) {
    return item.tone === "red" ? (item.conservative_ltv ?? item.current_ltv) : (item.relaxed_ltv ?? item.current_ltv);
  }

  function getLtvVal(item) {
    const key = `${item.region}_${item.usage}`;
    return finalLtvMap[key] ?? getDefaultLtv(item);
  }

  function setLtvVal(item, val) {
    const key = `${item.region}_${item.usage}`;
    setFinalLtvMap((prev) => ({ ...prev, [key]: Number(val) }));
  }

  async function handleApply(item) {
    const key = `${item.region}_${item.usage}`;
    const newLtv = getLtvVal(item);
    if (!window.confirm(`[${item.region}] ${item.usage}: LTV를 ${newLtv}%로 적용하시겠습니까?`)) return;
    setSavingKey(key);
    try {
      const res = await axios.post(`${API}/api/save-ltv`, {
        bank,
        region: item.region,
        usage: item.usage,
        new_ltv: newLtv,
        base_date: baseDate
      });
      alert(res.data.message || "변경되었습니다.");
      setSavedMsg((prev) => ({ ...prev, [key]: "✓ 적용" }));
      onSaved && onSaved();
    } catch (err) {
      setSavedMsg((prev) => ({ ...prev, [key]: "✗ 실패" }));
    } finally {
      setSavingKey(null);
    }
  }

  async function handleRevert(item) {
    const key = `${item.region}_${item.usage}`;
    if (!window.confirm(`[${item.region}] ${item.usage}: 이전 LTV 기준으로 되돌리겠습니까?`)) return;
    setSavingKey(key);
    try {
      const res = await axios.post(`${API}/api/revert-ltv`, {
        bank,
        region: item.region,
        usage: item.usage,
        base_date: baseDate
      });
      alert(res.data.message || "이전 기준으로 복구되었습니다.");
      setSavedMsg((prev) => ({ ...prev, [key]: null }));
      onSaved && onSaved();
    } catch (err) {
      const errorMsg = err.response?.data?.detail;
      alert(typeof errorMsg === 'object' ? JSON.stringify(errorMsg) : (errorMsg || "복구에 실패했습니다."));
    } finally {
      setSavingKey(null);
    }
  }

  return (
    <div className="space-y-4">
      {/* 필터 탭 */}
      <div className="inline-flex gap-1.5 bg-slate-100 border border-slate-200 rounded-xl p-1">
        {FILTER_TABS.map((t) => (
          <button
            key={t}
            onClick={() => setTab(t)}
            className={`px-3 py-1 rounded-lg text-[13px] font-semibold transition-all duration-150 ${tab === t
              ? "bg-white text-slate-900 shadow-sm"
              : "text-slate-500 hover:text-slate-700"
              }`}
          >
            {t}
          </button>
        ))}
      </div>

      {/* 테이블 카드 */}
      <div className="bg-white border border-slate-200 shadow-sm rounded-2xl overflow-hidden">
        {filtered.length === 0 ? (
          <div className="py-14 text-center text-base text-slate-400">
            현재 조건을 충족하는 조정 대상이 없습니다.
          </div>
        ) : (
          <div className="overflow-x-auto overflow-y-auto max-h-[600px]">
            <table className="w-full text-base">
              <thead className="sticky top-0 z-10">
                <tr className="bg-slate-50 border-b border-slate-200">
                  <Th>상태</Th>
                  <Th>지역 / 용도</Th>
                  <Th center>현재 LTV</Th>
                  <th className="px-2 py-2.5 text-[14px] font-bold text-slate-600 whitespace-nowrap text-center min-w-[120px]">AI 조정안</th>
                  <Th>권고안 산출 사유</Th>
                  <Th center>최종 LTV</Th>
                  <Th center>상세</Th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-100">
                {filtered.map((item, idx) => {
                  const isRed = item.tone === "red";
                  const isUp = item.direction === "▲";
                  const badgeKey = isUp ? "green" : (isRed ? "red" : "yellow");
                  const badge = BADGE[badgeKey];
                  const key = `${item.region}_${item.usage}`;
                  const finalVal = getLtvVal(item);
                  const saved = savedMsg[key];

                  // 이유 텍스트 파싱 (보수적/완화적 각각)
                  const rawReason = item.reason ?? "";

                  // Markdown 링크 및 빈 괄호 제거
                  const cleanReason = rawReason
                    .replace(/\[.*?\]\([^)]+\)/g, "") // 마크다운 링크 추출 및 통째로 제거
                    .replace(/\(\s*\)/g, ""); // 빈 () 제거

                  let reasonText = cleanReason;
                  if (isRed) {
                    const parts = cleanReason.split(/완화적\s*안/);
                    reasonText = (parts[0] || cleanReason).replace(/보수적\s*안\s*[:：]?\s*/g, "").trim();
                  } else {
                    const parts = cleanReason.split(/완화적\s*안\s*[:：]/);
                    if (parts.length > 1) reasonText = parts[parts.length - 1].trim();
                  }

                  return (
                    <tr
                      key={idx}
                      onClick={(e) => {
                        // 입력창이나 버튼 클릭 시에는 모달을 띄우지 않음
                        if (e.target.closest("button") || e.target.closest("input")) return;
                        if (onRowClick) onRowClick(item);
                      }}
                      className="hover:bg-blue-50/40 transition-all duration-100 border-b border-slate-100 last:border-0 group cursor-pointer"
                    >
                      {/* 상태 */}
                      <td className="px-2 py-2 whitespace-nowrap align-middle">
                        <span className={`inline-flex items-center gap-1.5 text-[14px] font-bold px-3 py-0 rounded-full ${badge.cls}`}>
                          <span className={`w-2 h-2 rounded-full shrink-0 ${badge.dot}`} />
                          {badge.label}
                        </span>
                      </td>

                      {/* 지역/용도 */}
                      <td className="px-2 py-2 whitespace-nowrap align-middle">
                        <div className="text-[20px] font-extrabold text-slate-900 leading-snug">{item.region}</div>
                        <div className="text-[14px] text-slate-400 font-medium mt-0.5">{item.usage} / {item.category}</div>
                      </td>

                      {/* 현재 LTV */}
                      <td className="px-2 py-2 text-center whitespace-nowrap align-middle">
                        <span className="text-[22px] font-black text-slate-800">{item.current_ltv}%</span>
                      </td>

                  {/* AI 권고안 (레드는 보수적, 옐로우는 완화적) */}
                      <td className="px-2 py-2 text-center whitespace-nowrap align-middle min-w-[120px]">
                        {item.advice_status === "pending" ? (
                          <div className="text-[14px] text-slate-500 font-medium">AI 권고 생성 중</div>
                        ) : llmLoading && (isRed ? item.conservative_ltv == null : item.relaxed_ltv == null) ? (
                          <AiSkeleton />
                        ) : (
                          <div>
                            <div className="text-[22px] font-black text-slate-800">
                              {isRed ? item.conservative_ltv : item.relaxed_ltv}%
                            </div>
                            <DeltaBadge val={isRed ? item.conservative_delta : item.relaxed_delta} />
                          </div>
                        )}
                      </td>

                      {/* 권고안 사유 - 가변적으로 조절되도록 min-w 축소 */}
                      <td className="px-3 py-2 min-w-[180px] align-middle">
                        {item.advice_status === "pending" ? (
                          <div className="h-12 bg-slate-50 border border-slate-100 rounded-xl animate-pulse flex items-center justify-center text-[13px] text-slate-500 font-medium">
                            AI 권고 생성 중
                          </div>
                        ) : (
                          <div className="bg-slate-50 border border-slate-100 rounded-xl px-3 py-1.5 transition-all group-hover:bg-white group-hover:border-blue-100 max-h-[80px] overflow-y-auto w-full custom-scrollbar">
                            <p
                              className="text-[14px] text-slate-600 leading-relaxed break-keep"
                              dangerouslySetInnerHTML={{ __html: reasonText || cleanReason }}
                            />
                          </div>
                        )}
                      </td>

                      {/* 최종 설정 LTV */}
                      <td className="px-2 py-2 whitespace-nowrap align-middle">
                        <div className="flex items-center gap-2 justify-center">
                          <div className="flex items-center bg-slate-100 rounded-lg px-2 py-1 border border-slate-200 focus-within:border-blue-400 focus-within:bg-white transition-all">
                            <input
                              type="number"
                              step={5}
                              value={finalVal}
                              onChange={(e) => setLtvVal(item, e.target.value)}
                              className="w-14 text-center bg-transparent text-slate-800 text-[17px] font-bold outline-none"
                            />
                          </div>

                          <button
                            onClick={() => handleApply(item)}
                            disabled={savingKey === key || saved === "✓ 적용"}
                            className={`w-9 h-9 flex items-center justify-center rounded-lg transition-all duration-150 border ${saved === "✓ 적용"
                              ? "bg-slate-100 text-slate-400 border-slate-200 cursor-not-allowed"
                              : saved === "✗ 실패"
                                ? "bg-rose-50 text-rose-600 border-rose-200"
                                : "bg-blue-600 text-white border-blue-600 hover:bg-blue-700 shadow-sm"
                              }`}
                          >
                            {saved === "✓ 적용" ? "✓" : saved === "✗ 실패" ? "✗" : (
                              <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2.5" d="M5 13l4 4L19 7" />
                              </svg>
                            )}
                          </button>

                          <button
                            onClick={() => handleRevert(item)}
                            title="이전 기준으로 되돌리기"
                            disabled={savingKey === key}
                            className="w-9 h-9 flex items-center justify-center rounded-lg border border-slate-200 bg-white text-slate-400 hover:text-orange-600 hover:border-orange-200 hover:bg-orange-50 transition-all"
                          >
                            <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2.5" d="M3 10h10a8 8 0 018 8v2M3 10l6 6m-6-6l6-6" />
                            </svg>
                          </button>
                        </div>
                      </td>

                      {/* 상세 보기 */}
                      <td className="px-4 py-2 whitespace-nowrap align-middle text-center">
                        <button
                          onClick={() => onRowClick && onRowClick(item)}
                          className="h-9 px-5 text-[14px] font-bold text-slate-600 border border-slate-200 rounded-lg bg-white hover:bg-slate-50 transition-all"
                        >
                          보기
                        </button>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  );
}

function AiSkeleton() {
  return (
    <div className="flex flex-col items-center gap-1">
      <div className="w-12 h-4 bg-blue-50 rounded animate-pulse" />
      <div className="flex items-center gap-1 text-[10px] text-blue-600 font-semibold">
        <span className="w-1 h-1 rounded-full bg-blue-500 animate-bounce" style={{ animationDelay: "0ms" }} />
        <span className="w-1 h-1 rounded-full bg-blue-500 animate-bounce" style={{ animationDelay: "120ms" }} />
        <span className="w-1 h-1 rounded-full bg-blue-500 animate-bounce" style={{ animationDelay: "240ms" }} />
      </div>
    </div>
  );
}

function Th({ children, center }) {
  return (
    <th className={`px-2 py-2.5 text-[14px] font-bold text-slate-600 whitespace-nowrap ${center ? "text-center" : "text-left"}`}>
      {children}
    </th>
  );
}

function DeltaBadge({ val }) {
  if (val == null) return null;
  const isDown = val < 0;
  const isUp = val > 0;
  return (
    <div className={`text-[15px] font-bold mt-0.5 ${isDown ? "text-rose-600" : isUp ? "text-emerald-600" : "text-slate-400"}`}>
      {val > 0 ? "+" : ""}{val}%
    </div>
  );
}

function Tooltip() {
  return (
    <div className="group relative cursor-pointer">
      <span className="inline-flex items-center justify-center w-4 h-4 rounded-full bg-slate-100 border border-slate-300 text-slate-500 text-[10px] font-bold">?</span>
      <div className="absolute left-full ml-2 top-0 w-72 bg-zinc-900 border border-zinc-800 text-white text-xs rounded-xl p-4 shadow-2xl opacity-0 invisible group-hover:opacity-100 group-hover:visible transition-all duration-150 z-50 leading-relaxed">
        <div className="font-bold text-zinc-300 mb-2">분석 기준</div>
        <div className="text-zinc-500 mb-3">
          <span className="text-zinc-400">·</span> 최소 건수: 최근 3개월 10건 이상<br />
          <span className="text-zinc-400">·</span> 이상치: LTV ±30% 초과값 제외
        </div>
        <div className="mb-2">
          <span className="inline-flex items-center gap-1.5 text-rose-400 font-bold"><span className="w-1.5 h-1.5 rounded-full bg-rose-400" />조정 대상</span>
          <br /><span className="text-zinc-500">가중평균 낙찰가율이 LTV와 10%p 이상 차이</span>
        </div>
        <div>
          <span className="inline-flex items-center gap-1.5 text-amber-400 font-bold"><span className="w-1.5 h-1.5 rounded-full bg-amber-400" />검토 대상</span>
          <br /><span className="text-zinc-500">가중평균 낙찰가율이 LTV와 5~10%p 차이</span>
        </div>
      </div>
    </div>
  );
}
