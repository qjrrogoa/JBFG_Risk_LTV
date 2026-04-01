import { useState, useEffect, useCallback, useRef } from "react";
import axios from "axios";

import LoginPage    from "./components/LoginPage";
import SummarySection from "./components/SummarySection";
import UrgentTable  from "./components/UrgentTable";
import MatrixTable  from "./components/MatrixTable";
import DetailModal  from "./components/DetailModal";

const API = "http://localhost:8000";

// ─── 인증 라우팅 ────────────────────────────────────────────
export default function App() {
  const [bank, setBank] = useState(() => sessionStorage.getItem("bank") || null);

  function handleLogin(bankName) {
    sessionStorage.setItem("bank", bankName);
    setBank(bankName);
  }
  function handleLogout() {
    sessionStorage.removeItem("bank");
    setBank(null);
  }

  if (!bank) return <LoginPage onLogin={handleLogin} />;
  return <Dashboard bank={bank} onLogout={handleLogout} />;
}

// ─── 메인 대시보드 ──────────────────────────────────────────
function Dashboard({ bank, onLogout }) {
  const today = new Date().toISOString().slice(0, 7);   // YYYY-MM
  const [baseDate, setBaseDate]     = useState(today);

  // 월말 날짜로 변환 (API는 YYYY-MM-DD 필요)
  function toEndOfMonth(ym) {
    if (!ym) return null;
    const [y, m] = ym.split("-").map(Number);
    const lastDay = new Date(y, m, 0).getDate();
    return `${ym}-${String(lastDay).padStart(2, "00")}`;
  }
  const [urgentList, setUrgentList]   = useState([]);
  const [matrixData, setMatrixData]   = useState([]);
  const [loadingU, setLoadingU]       = useState(true);   // 시그널 로딩
  const [loadingLlm, setLoadingLlm]   = useState(true);   // LLM 로딩
  const [loadingM, setLoadingM]       = useState(true);
  const [error, setError]             = useState("");
  const [llmError, setLlmError]       = useState(false);
  const [llmErrMsg, setLlmErrMsg]     = useState("");
  const abortRef = useRef(null);   // 이전 요청 취소용
  const [modalItem, setModalItem]     = useState(null);
  const [lastUpdate, setLastUpdate]   = useState("");

  const fetchAll = useCallback(() => {
    // ── 이전 요청 취소 ──
    if (abortRef.current) abortRef.current.abort();
    const controller = new AbortController();
    abortRef.current = controller;
    const signal = controller.signal;

    const params = { bank, base_date: toEndOfMonth(baseDate) };
    setLoadingU(true);
    setLoadingLlm(true);
    setLoadingM(true);
    setError("");
    setLlmError(false);
    setLlmErrMsg("");
    setUrgentList([]);

    // ── Phase 1: 시그널만 (LLM 없음, 빠름) ──
    axios.get(`${API}/api/urgent-signals`, { params, signal })
      .then((res) => {
        setUrgentList(res.data);
        setLastUpdate(baseDate);
      })
      .catch((err) => { if (!axios.isCancel(err)) setError("긴급 신호 로딩 실패"); })
      .finally(() => { if (!signal.aborted) setLoadingU(false); });

    // ── Phase 2: LLM 권고안 포함 (느림, 최대 3분) ──
    axios.get(`${API}/api/urgent-list`, { params, signal, timeout: 180000 })
      .then((res) => { if (!signal.aborted) setUrgentList(res.data); })
      .catch((err) => {
        if (axios.isCancel(err) || signal.aborted) return;
        setLlmError(true);
        const msg = err?.response?.data?.detail || err?.message || String(err);
        setLlmErrMsg(msg);
        console.error("[urgent-list 실패]", err);
      })
      .finally(() => { if (!signal.aborted) setLoadingLlm(false); });

    // ── 매트릭스 ──
    axios.get(`${API}/api/matrix`, { params, signal })
      .then((res) => { if (!signal.aborted) setMatrixData(res.data); })
      .catch((err) => { if (!axios.isCancel(err)) setError("매트릭스 데이터 로딩 실패"); })
      .finally(() => { if (!signal.aborted) setLoadingM(false); });
  }, [bank, baseDate]);

  useEffect(() => { fetchAll(); }, [fetchAll]);

  // 매트릭스 행 클릭 → urgent 매칭 후 모달 오픈
  function handleMatrixRowClick(row, matched) {
    if (matched) {
      setModalItem({ ...matched, 대분류: row.대분류 });
    } else {
      setModalItem({
        region: row.지역, usage: row.용도, category: row.대분류,
        current_ltv: row.LTV, ltv_val: row.LTV,
      });
    }
  }

  // 요약 패널 클릭
  function handleSummaryClick(item) {
    setModalItem(item);
  }

  const isLoading = loadingU || loadingM;

  // 헤더 카운트
  const redCnt    = urgentList.filter((d) => d.tone === "red"    && d.direction === "▼").length;
  const yellowCnt = urgentList.filter((d) => d.tone === "yellow" && d.direction === "▼").length;
  const refCnt    = urgentList.filter((d) => d.direction === "▲").length;

  return (
    <div className="min-h-screen bg-slate-50">
      {/* ── 상단 네비게이션 (keep dark) ── */}
      <header className="bg-zinc-950/95 backdrop-blur border-b border-zinc-800/60 sticky top-0 z-30">
        <div className="max-w-screen-xl mx-auto px-6 py-0 flex items-center gap-6 h-14">
          {/* 로고 */}
          <div className="flex items-center gap-2.5 shrink-0">
            <div className="w-7 h-7 rounded-lg bg-gradient-to-br from-blue-500 to-blue-700 flex items-center justify-center shadow-lg shadow-blue-900/40">
              <svg className="w-4 h-4 text-white" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2}
                  d="M9 19v-6a2 2 0 00-2-2H5a2 2 0 00-2 2v6a2 2 0 002 2h2a2 2 0 002-2zm0 0V9a2 2 0 012-2h2a2 2 0 012 2v10m-6 0a2 2 0 002 2h2a2 2 0 002-2m0 0V5a2 2 0 012-2h2a2 2 0 012 2v14a2 2 0 01-2 2h-2a2 2 0 01-2-2z" />
              </svg>
            </div>
            <span className="text-zinc-50 font-black text-sm tracking-tight">LTV 적정성</span>
            <span className="inline-flex items-center px-2 py-0.5 rounded-full bg-zinc-800 border border-zinc-700 text-zinc-400 text-[11px] font-medium">
              {bank}
            </span>
          </div>

          {/* 상태 뱃지 */}
          <div className="flex items-center gap-2 flex-1">
            {!loadingU && (
              <>
                {redCnt > 0 && (
                  <span className="inline-flex items-center gap-1.5 text-xs font-bold px-2.5 py-1 rounded-full bg-rose-500/10 text-rose-400 border border-rose-500/20">
                    <span className="w-1.5 h-1.5 rounded-full bg-rose-400 shrink-0" />
                    조정 {redCnt}건
                  </span>
                )}
                {yellowCnt > 0 && (
                  <span className="inline-flex items-center gap-1.5 text-xs font-bold px-2.5 py-1 rounded-full bg-amber-500/10 text-amber-400 border border-amber-500/20">
                    <span className="w-1.5 h-1.5 rounded-full bg-amber-400 shrink-0" />
                    검토 {yellowCnt}건
                  </span>
                )}
                {refCnt > 0 && (
                  <span className="inline-flex items-center gap-1.5 text-xs font-bold px-2.5 py-1 rounded-full bg-emerald-500/10 text-emerald-400 border border-emerald-500/20">
                    <span className="w-1.5 h-1.5 rounded-full bg-emerald-400 shrink-0" />
                    참고 {refCnt}건
                  </span>
                )}
              </>
            )}
          </div>

          {/* 우측 컨트롤 */}
          <div className="flex items-center gap-3 shrink-0">
            {/* 기준일 */}
            <div className="flex items-center gap-2 text-xs text-zinc-400">
              <svg className="w-3.5 h-3.5 text-zinc-500" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2}
                  d="M8 7V3m8 4V3m-9 8h10M5 21h14a2 2 0 002-2V7a2 2 0 00-2-2H5a2 2 0 00-2 2v12a2 2 0 002 2z" />
              </svg>
              <input
                type="month"
                value={baseDate}
                max={today}
                onChange={(e) => setBaseDate(e.target.value)}
                className="bg-zinc-800 border border-zinc-700 text-zinc-300 rounded-lg px-2.5 py-1 text-xs focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-transparent transition-all duration-150"
              />
            </div>

            {/* 새로고침 */}
            <button
              onClick={fetchAll}
              disabled={isLoading}
              className="flex items-center gap-1.5 text-xs text-zinc-400 hover:text-zinc-100 border border-zinc-700 hover:border-zinc-500 rounded-lg px-3 py-1.5 transition-all duration-150 disabled:opacity-40 disabled:cursor-not-allowed"
            >
              <svg className={`w-3.5 h-3.5 ${isLoading ? "animate-spin" : ""}`} fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2}
                  d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15" />
              </svg>
              {isLoading ? "로딩 중" : "새로고침"}
            </button>

            {/* 로그아웃 */}
            <button
              onClick={onLogout}
              className="flex items-center gap-1.5 text-xs text-zinc-500 hover:text-rose-400 border border-zinc-700 hover:border-rose-900/60 rounded-lg px-3 py-1.5 transition-all duration-150"
            >
              <svg className="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2}
                  d="M17 16l4-4m0 0l-4-4m4 4H7m6 4v1a3 3 0 01-3 3H6a3 3 0 01-3-3V7a3 3 0 013-3h4a3 3 0 013 3v1" />
              </svg>
              로그아웃
            </button>
          </div>
        </div>
      </header>

      {/* ── 본문 ── */}
      <main className="max-w-screen-xl mx-auto px-6 py-8 space-y-10">
        {error && (
          <div className="bg-red-50 border border-red-200 text-red-700 rounded-xl px-4 py-3 text-sm flex items-center gap-2">
            <svg className="w-4 h-4 shrink-0 text-red-500" fill="currentColor" viewBox="0 0 20 20">
              <path fillRule="evenodd" d="M18 10a8 8 0 11-16 0 8 8 0 0116 0zm-7 4a1 1 0 11-2 0 1 1 0 012 0zm-1-9a1 1 0 00-1 1v4a1 1 0 102 0V6a1 1 0 00-1-1z" clipRule="evenodd" />
            </svg>
            {error}
          </div>
        )}

        {/* 이번달 요약 */}
        <Section title="이번달 요약" subtitle="조정·검토 대상 항목을 빠르게 확인합니다.">
          {loadingU
            ? <Skeleton h={220} />
            : <SummarySection urgentList={urgentList} onItemClick={handleSummaryClick} />
          }
        </Section>

        {/* 긴급 조정 대상 */}
        <Section>
          {loadingU
            ? <Skeleton h={300} />
            : <UrgentTable
                urgentList={urgentList}
                bank={bank}
                baseDate={baseDate}
                llmLoading={loadingLlm}
                onRowClick={(item) => setModalItem(item)}
                onSaved={fetchAll}
              />
          }
        </Section>

        {/* 매트릭스 */}
        <Section>
          {loadingM
            ? <Skeleton h={400} />
            : <MatrixTable
                matrixData={matrixData}
                urgentList={urgentList}
                onRowClick={handleMatrixRowClick}
              />
          }
        </Section>
      </main>

      {/* ── AI 분석 로딩 / 에러 배너 (keep dark for visibility) ── */}
      {!loadingU && (loadingLlm || llmError) && (
        <div className={`fixed bottom-6 right-6 z-50 flex items-center gap-3.5 rounded-2xl px-4 py-3.5 shadow-2xl border
          ${llmError
            ? "bg-red-950/80 border-red-800/50 text-white"
            : "bg-slate-900 border-slate-700 text-white"
          }`}>
          {llmError ? (
            /* 에러 상태 */
            <>
              <div className="w-8 h-8 rounded-full bg-red-800/50 flex items-center justify-center shrink-0">
                <svg className="w-4 h-4 text-red-400" fill="currentColor" viewBox="0 0 20 20">
                  <path fillRule="evenodd" d="M8.257 3.099c.765-1.36 2.722-1.36 3.486 0l5.58 9.92c.75 1.334-.213 2.98-1.742 2.98H4.42c-1.53 0-2.493-1.646-1.743-2.98l5.58-9.92zM11 13a1 1 0 11-2 0 1 1 0 012 0zm-1-8a1 1 0 00-1 1v3a1 1 0 002 0V6a1 1 0 00-1-1z" clipRule="evenodd" />
                </svg>
              </div>
              <div>
                <div className="text-sm font-bold text-zinc-100">AI 분석 실패</div>
                <div className="text-xs text-red-300 mt-0.5 max-w-[220px] truncate">{llmErrMsg || "LLM 응답 없음 — 시그널 데이터만 표시 중"}</div>
              </div>
              <button
                onClick={fetchAll}
                className="text-xs font-bold bg-red-700 hover:bg-red-600 px-3 py-1.5 rounded-lg transition-all duration-150 shrink-0"
              >
                재시도
              </button>
            </>
          ) : (
            /* 로딩 상태 */
            <>
              <div className="relative shrink-0 w-8 h-8">
                <div className="absolute inset-0 rounded-full border-2 border-zinc-700" />
                <div className="absolute inset-0 rounded-full border-2 border-blue-500 border-t-transparent animate-spin" />
              </div>
              <div>
                <div className="text-sm font-bold text-zinc-100">AI 분석 중입니다…</div>
                <div className="text-xs text-zinc-500 mt-0.5">LLM이 LTV 권고안을 생성하고 있습니다</div>
              </div>
              <div className="flex items-center gap-1 shrink-0 ml-1">
                <span className="w-1.5 h-1.5 rounded-full bg-blue-400 animate-bounce" style={{ animationDelay: "0ms" }} />
                <span className="w-1.5 h-1.5 rounded-full bg-blue-400 animate-bounce" style={{ animationDelay: "150ms" }} />
                <span className="w-1.5 h-1.5 rounded-full bg-blue-400 animate-bounce" style={{ animationDelay: "300ms" }} />
              </div>
            </>
          )}
        </div>
      )}

      {/* 상세 모달 */}
      {modalItem && (
        <DetailModal
          item={modalItem}
          bank={bank}
          baseDate={baseDate}
          onClose={() => setModalItem(null)}
        />
      )}
    </div>
  );
}

function Section({ title, subtitle, children }) {
  return (
    <div className="space-y-3">
      {title && (
        <div>
          <h2 className="text-xs font-semibold text-slate-400 uppercase tracking-widest">{title}</h2>
          {subtitle && <p className="text-xs text-slate-400 mt-0.5">{subtitle}</p>}
        </div>
      )}
      {children}
    </div>
  );
}

function Skeleton({ h = 200 }) {
  return (
    <div
      className="bg-slate-200 animate-pulse rounded-2xl"
      style={{ height: h }}
    />
  );
}
