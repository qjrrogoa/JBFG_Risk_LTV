import { useState, useEffect, useCallback, useRef, useMemo } from "react";
import axios from "axios";
import UrgentTable from "./components/UrgentTable";
import MatrixTable from "./components/MatrixTable";
import DetailModal from "./components/DetailModal";
import LtvTableModal from "./components/LtvTableModal";
import { API_BASE_URL } from "./config/api";

const API = API_BASE_URL;

/* ─── App 엔트리 ─── */
export default function App() {
    const [bank, setBank] = useState(() => sessionStorage.getItem("bank") || null);
    const [user, setUser] = useState(() => sessionStorage.getItem("user") || null);

    function handleLogin(bankName, username) {
        sessionStorage.setItem("bank", bankName);
        sessionStorage.setItem("user", username);
        setBank(bankName);
        setUser(username);
    }

    function handleLogout() {
        sessionStorage.removeItem("bank");
        sessionStorage.removeItem("user");
        setBank(null);
        setUser(null);
    }

    if (!bank) return <AuthPage onLogin={handleLogin} />;
    return <Dashboard bank={bank} user={user} onLogout={handleLogout} />;
}

/* ─── 대시보드 ─── */
function Dashboard({ bank, user, onLogout }) {
    const today = new Date().toISOString().slice(0, 7);
    const [baseDate, setBaseDate] = useState(today);
    const [showDashboard, setShowDashboard] = useState(false);
    const [pageLoading, setPageLoading] = useState({ active: false, message: "" }); // 글로벌 로딩 상태
    const [isSidebarOpen, setIsSidebarOpen] = useState(false);
    const [isChatFullscreen, setIsChatFullscreen] = useState(false);
    const [urgentList, setUrgentList] = useState([]);
    const [matrixData, setMatrixData] = useState([]);
    const [loadingU, setLoadingU] = useState(true);
    const [loadingLlm, setLoadingLlm] = useState(true);
    const [loadingM, setLoadingM] = useState(true);
    const [error, setError] = useState("");
    const [llmError, setLlmError] = useState(false);
    const [llmErrMsg, setLlmErrMsg] = useState("");
    const [modalItem, setModalItem] = useState(null);
    const [lastUpdate, setLastUpdate] = useState("");
    const abortRef = useRef(null);
    const [chatInput, setChatInput] = useState("");
    const [chatHistory, setChatHistory] = useState([]);
    const [isChatting, setIsChatting] = useState(false);
    const [isLtvTableOpen, setIsLtvTableOpen] = useState(false);
    const [isLogModalOpen, setIsLogModalOpen] = useState(false);
    const chatEndRef = useRef(null);
    const advicePollRef = useRef(null);
    const ADVICE_MAX_RETRY = 12;
    const ADVICE_POLL_INTERVAL_MS = 5000;
    function clearAdvicePoll() {
        if (advicePollRef.current) {
            clearTimeout(advicePollRef.current);
            advicePollRef.current = null;
        }
    }

    function isHistoricalMonth(ym) {
        if (!ym) return false;
        try {
            const target = new Date(`${ym}-01T00:00:00`);
            const now = new Date();
            const targetMonth = target.getFullYear() * 12 + target.getMonth();
            const nowMonth = now.getFullYear() * 12 + now.getMonth();
            return targetMonth < nowMonth;
        } catch {
            return false;
        }
    }

    function normalizeUrgentRows(rows, isHistorical = false) {
        if (!Array.isArray(rows)) return [];
        if (!isHistorical) return rows;
        return rows.map((row) => ({
            ...row,
            advice_status: row.advice_status === "pending" ? "ready" : row.advice_status,
        }));
    }

    function hasPendingAdvice(rows, isHistorical = false) {
        if (isHistorical) return false;
        return rows.some((row) => (row?.advice_status || "ready") === "pending");
    }

    function toEndOfMonth(ym) {
        if (!ym) return null;
        const [y, m] = ym.split("-").map(Number);
        return `${ym}-${String(new Date(y, m, 0).getDate()).padStart(2, "0")}`;
    }

    const fetchAll = useCallback(() => {
        if (abortRef.current) abortRef.current.abort();
        const controller = new AbortController();
        abortRef.current = controller;
        const signal = controller.signal;
        const params = { bank, base_date: toEndOfMonth(baseDate) };
        const isHistorical = isHistoricalMonth(baseDate);
        let retry = 0;
        let isMatrixDone = false;
        let isUrgentDone = false;
        let hasPending = false;
        setLoadingU(true); setLoadingLlm(true); setLoadingM(true);
        setError(""); setLlmError(false); setLlmErrMsg(""); setUrgentList([]);
        clearAdvicePoll();

        // 1단계: 매트릭스 집계
        setPageLoading({ active: true, message: "매트릭스 집계 및 AI 권고 생성 중입니다..." });

        const finalizeLoadingState = () => {
            if (isMatrixDone && isUrgentDone && !hasPending) {
                setPageLoading({ active: false, message: "" });
            }
        };

        const handlePollUrgentAdvice = () => {
            if (signal.aborted) return;
            if (retry >= ADVICE_MAX_RETRY) {
                if (!signal.aborted) {
                    setLoadingLlm(false);
                    setPageLoading({ active: false, message: "AI 조정안 생성 타임아웃. 잠시 후 다시 시도해 주세요." });
                }
                return;
            }

            retry += 1;
            advicePollRef.current = setTimeout(() => {
                axios.get(`${API}/api/advice-status`, { params, signal, timeout: 600000 })
                    .then((statusRes) => {
                        if (signal.aborted) return;
                        if (statusRes.data?.pending) {
                            setPageLoading({ active: true, message: "AI 권고 생성 중입니다..." });
                            handlePollUrgentAdvice();
                            return;
                        }

                        axios.get(`${API}/api/urgent-list`, { params: { ...params, sync_ai: true }, signal, timeout: 600000 })
                            .then((pollRes) => {
                                if (signal.aborted) return;
                                const nextData = normalizeUrgentRows(Array.isArray(pollRes.data) ? pollRes.data : [], isHistorical);
                                setUrgentList(nextData);

                                hasPending = hasPendingAdvice(nextData, isHistorical);
                                if (hasPending) {
                                    setPageLoading({ active: true, message: "AI 권고 생성 중입니다..." });
                                    handlePollUrgentAdvice();
                                } else {
                                    setLoadingLlm(false);
                                    setPageLoading({ active: false, message: "" });
                                    finalizeLoadingState();
                                }
                            })
                            .catch((pollErr) => {
                                if (axios.isCancel(pollErr) || signal.aborted) return;
                                if (!signal.aborted) {
                                    setLoadingLlm(false);
                                    setLlmError(true);
                                    setLlmErrMsg(pollErr?.response?.data?.detail || pollErr?.message || "AI 조정안 재조회 중 오류가 발생했습니다.");
                                    setPageLoading({ active: false, message: "" });
                                }
                            });
                    })
                    .catch((pollErr) => {
                        if (axios.isCancel(pollErr) || signal.aborted) return;
                        if (!signal.aborted) {
                            setLoadingLlm(false);
                            setLlmError(true);
                            setLlmErrMsg(pollErr?.response?.data?.detail || pollErr?.message || "AI 조정안 재조회 중 오류가 발생했습니다.");
                            setPageLoading({ active: false, message: "" });
                        }
                    });
            }, ADVICE_POLL_INTERVAL_MS);
        };

        axios.get(`${API}/api/matrix`, { params, signal })
            .then(r => {
                if (!signal.aborted) {
                    setMatrixData(Array.isArray(r.data) ? r.data : []);
                }
            })
            .catch(e => { if (!axios.isCancel(e) && !signal.aborted) setError(p => p || "매트릭스 집계 조회 중 오류가 발생했습니다."); })
            .finally(() => {
                if (signal.aborted) return;
                isMatrixDone = true;
                setLoadingM(false);
                finalizeLoadingState();
            });

        axios.get(`${API}/api/urgent-list`, { params: { ...params, sync_ai: true }, signal, timeout: 600000 })
            .then(r => {
                if (!signal.aborted) {
                    const data = Array.isArray(r.data) ? r.data : [];
                    const nextData = normalizeUrgentRows(data, isHistorical);
                    setUrgentList(nextData);
                    setLastUpdate(baseDate);
                    isUrgentDone = true;

                    hasPending = hasPendingAdvice(nextData, isHistorical);
                    if (hasPending) {
                        setLoadingLlm(true);
                        setPageLoading(prev => ({ ...prev, message: "AI 권고 생성 중입니다..." }));
                        handlePollUrgentAdvice();
                    } else {
                        setLoadingLlm(false);
                        finalizeLoadingState();
                    }
                }
            })
            .catch(e => {
                if (axios.isCancel(e) || signal.aborted) return;
                setLlmError(true);
                setLlmErrMsg(e?.response?.data?.detail || e?.message || "");
                setLoadingLlm(false);
                setPageLoading({ active: false, message: "" });
            })
            .finally(() => {
                if (signal.aborted) return;
                isUrgentDone = true;
                setLoadingU(false);
                finalizeLoadingState();
            });
    }, [bank, baseDate]);

    useEffect(() => {
        fetchAll();
        return () => {
            clearAdvicePoll();
            abortRef.current?.abort();
        };
    }, [fetchAll]);

    const stats = useMemo(() => {
        const adjust = urgentList.filter(d => d.tone === "red" && d.direction === "▼");
        const review = urgentList.filter(d => d.tone === "yellow" && d.direction === "▼");
        const ref = urgentList.filter(d => d.direction === "▲");

        const grouped = adjust.reduce((acc, item) => {
            const reg = item.region || item.지역 || "-";
            const use = item.usage || item.용도 || "-";
            if (!acc[reg]) acc[reg] = new Set();
            acc[reg].add(use);
            return acc;
        }, {});

        const summaryDetail = Object.entries(grouped)
            .map(([reg, uses]) => `${reg}(${Array.from(uses).join(", ")})`)
            .join(", ");

        return {
            adjust, review, ref,
            adjustCount: adjust.length,
            reviewCount: review.length,
            refCount: ref.length,
            summaryDetail
        };
    }, [urgentList]);

    const normalize = (s) => (s || "").replace(/\s+/g, "");

    function openFromMatrix(row) {
        const matched = urgentList.find(i => normalize(i.region || i.지역) === normalize(row.지역 || row.region) && normalize(i.usage || i.용도) === normalize(row.용도 || row.usage));
        if (matched) {
            setModalItem({
                ...matched,
                대분류: row.대분류 || row.category || row.group,
                detailMode: "signal",
                signal_tone: matched.tone || row.signal_tone,
                signal_direction: matched.direction || row.signal_direction,
                signal_reason: row.signal_reason,
                hideAdvice: false
            });
            return;
        }
        const isSignal = ["red", "yellow"].includes(row.signal_tone) || Boolean(row.signal_direction);
        setModalItem({
            region: row.지역 || row.region,
            usage: row.용도 || row.usage,
            category: row.대분류 || row.category || row.group,
            current_ltv: row.LTV || row.current_ltv || row.ltv_val,
            ltv_val: row.LTV || row.current_ltv || row.ltv_val,
            met: row.met,
            tone: row.signal_tone,
            direction: row.signal_direction,
            signal_tone: row.signal_tone,
            signal_direction: row.signal_direction,
            signal_reason: row.signal_reason,
            detailMode: isSignal ? "signal" : "normal",
            hideAdvice: !isSignal
        });
    }

    function closeDetailModal() {
        setModalItem(null);
    }

    function closeLtvTableModal() {
        setIsLtvTableOpen(false);
    }

    const [yy, mm] = baseDate.split("-");
    const dateCard = `${yy.slice(2)}.${mm}.01`;

    return (
        <div className="min-h-screen bg-[#f0f4fa]">
            {showDashboard && (
                <nav className="sticky top-0 z-30 flex items-center justify-end gap-3 bg-white/80 backdrop-blur-md border-b border-[#dce5f0] px-8 py-2">
                    <div className="mr-auto flex items-center gap-2">
                        <div className="w-8 h-8 rounded-full bg-blue-100 flex items-center justify-center text-blue-600 font-bold text-xs uppercase">
                            {user ? user.slice(0, 2) : "??"}
                        </div>
                        <span className="text-[13px] font-bold text-slate-700 mr-1">{bank} {user}님</span>
                        <button onClick={onLogout} className="text-[11px] font-bold text-slate-400 hover:text-red-500 border border-slate-200 px-2 py-1 rounded-lg hover:bg-red-50 transition-all">🚪 로그아웃</button>
                    </div>
                    <MonthPicker value={baseDate} onChange={setBaseDate} />
                    <NavBtn onClick={() => setIsLtvTableOpen(true)}>📑 LTV 기준표 보기</NavBtn>
                    {user === "admin" && (
                        <NavBtn onClick={() => setIsLogModalOpen(true)}>📜 변경 이력 로그</NavBtn>
                    )}
                </nav>
            )}

            <div className="mx-auto w-full max-w-[1920px] px-6 lg:px-10 py-6 flex items-start transition-all duration-500">
                <div className={`flex-1 min-w-0 space-y-5 transition-all duration-500 ${isSidebarOpen ? "pr-6" : ""} ${!showDashboard ? "hidden" : ""}`}>
                    <div className="rounded-3xl bg-gradient-to-br from-[#e8f0fb] via-[#f0f5fc] to-[#dce8f8] border border-[#cddcf0] p-10 relative overflow-hidden">
                        <div className="absolute top-4 right-8 w-48 h-28 rounded-full bg-white/30 blur-2xl" />
                        <div className="absolute top-12 right-28 w-32 h-20 rounded-full bg-white/20 blur-xl" />
                        <h1 className="text-[34px] font-black tracking-[-0.04em] text-[#0f1d33] relative z-10">LTV 적정성 검증 Agent</h1>
                        <p className="mt-1 text-[16px] text-[#6b7d95] relative z-10">지역·용도별 낙찰가율 흐름을 기반으로 조정 우선순위를 빠르게 확인하세요.</p>
                        <div className="mt-6 bg-white/60 backdrop-blur-sm rounded-2xl border border-white/40 px-6 py-5 relative z-10 shadow-sm">
                            <p className="text-[25px] font-bold text-[#1e2d44] leading-relaxed">
                                이번달 LTV 점검 결과, 총{" "}
                                <span className="text-[#e54040] font-black">{stats.adjustCount}건</span>의{" "}
                                <span className="font-black">조정 대상</span>과{" "}
                                <span className="text-[#e8991b] font-black">{stats.reviewCount}건</span>의{" "}
                                <span className="font-black">검토 대상</span>이 확인되었습니다.
                                {stats.adjustCount > 0 && (
                                    <>
                                        <br />
                                        주요 조정 대상은 <span className="font-black text-[#1e2d44]">{stats.summaryDetail}</span> 등입니다.
                                    </>
                                )}
                            </p>
                        </div>
                        <div className="mt-5 grid grid-cols-1 lg:grid-cols-2 gap-5 relative z-10">
                            <SummaryBucket
                                title="조정 필요"
                                tone="adjust"
                                description="LTV 조정 대상"
                                count={stats.adjustCount}
                                items={stats.adjust}
                                onItemClick={setModalItem}
                                loading={loadingU}
                                loadingLlm={loadingLlm}
                            />
                            <SummaryBucket
                                title="검토 필요"
                                tone="review"
                                description="LTV 검토 대상"
                                count={stats.reviewCount}
                                items={stats.review}
                                onItemClick={setModalItem}
                                loading={loadingU}
                                loadingLlm={loadingLlm}
                            />
                        </div>
                    </div>

                    {error && <Banner tone="error">{error}</Banner>}
                    {!loadingU && loadingLlm && (
                        <div className="mb-4 rounded-xl border border-blue-200 bg-blue-50/50 px-5 py-3.5 flex items-center justify-between">
                            <div className="flex items-center gap-3">
                                <span className="relative flex h-3 w-3">
                                    <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-blue-400 opacity-75"></span>
                                    <span className="relative inline-flex rounded-full h-3 w-3 bg-blue-500"></span>
                                </span>
                            <span className="text-[15px] font-bold text-blue-800">AI 권고 생성 중입니다.</span>
                            </div>
                            <span className="text-[13px] font-medium text-blue-500 italic">AI 권고 생성 완료 전까지는 추가 시간이 소요될 수 있습니다.</span>
                        </div>
                    )}
                    {llmError && <Banner tone="warning">{llmErrMsg || "AI 조정안을 불러오지 못했습니다."}</Banner>}

                    <section className="rounded-2xl bg-white border border-[#dce5f0] shadow-sm overflow-hidden">
                        <div className="px-6 pt-5 pb-3">
                            <h2 className="text-[28px] font-black text-[#0f1d33] flex items-center gap-2">
                                <span>🏷️</span> LTV조정 및 검토 우선순위
                            </h2>
                        </div>
                        <div className="px-6 pb-5">
                            {loadingU ? <SkeletonRows rows={6} /> : (
                                <UrgentTable urgentList={urgentList} onRowClick={setModalItem} llmLoading={loadingLlm} bank={bank} baseDate={lastUpdate} onSaved={fetchAll} />
                            )}
                        </div>
                    </section>

                    <section className="rounded-2xl bg-white border border-[#dce5f0] px-6 py-5 shadow-sm">
                        {loadingM ? <SkeletonRows rows={8} /> : (
                            <MatrixTable matrixData={matrixData} urgentList={urgentList} onRowClick={openFromMatrix} />
                        )}
                    </section>
                </div>

                <div className={!showDashboard
                    ? "fixed inset-0 z-[100] bg-[#f8fafc] flex flex-col items-center justify-center p-6"
                    : `shrink-0 overflow-hidden transition-all duration-500 ${isSidebarOpen ? "w-[360px] opacity-100" : "w-0 opacity-0"}`
                }>
                    {!showDashboard && (
                        <div className="absolute top-8 right-8">
                            <button onClick={onLogout} className="px-5 py-2.5 rounded-xl bg-white shadow-sm border border-slate-200 text-[14px] font-bold text-slate-500 hover:text-slate-800 hover:bg-slate-50 transition-colors">
                                🚪 로그아웃
                            </button>
                        </div>
                    )}
                    <aside className={!showDashboard
                        ? "w-full max-w-5xl h-[85vh] flex flex-col bg-white border border-slate-200 shadow-2xl rounded-[32px] overflow-hidden"
                        : `transition-all duration-500 ease-[cubic-bezier(0.2,0.8,0.2,1)] flex flex-col ${isChatFullscreen ? "fixed right-0 top-0 z-50 bg-[#f0f4fa]/95 backdrop-blur-md p-8 w-screen h-screen shadow-[-20px_0_40px_rgba(0,0,0,0.1)]" : "w-[360px] space-y-4 sticky top-14 h-[calc(100vh-80px)] bg-transparent"}`
                    }>
                        {!showDashboard ? (
                            <div className="p-8 pb-5 flex items-center justify-between border-b border-slate-100 shrink-0 bg-white">
                                <div className="flex items-center gap-4">
                                    <div className="w-14 h-14 bg-gradient-to-br from-[#42a5f5] to-[#26c6da] rounded-2xl flex items-center justify-center shadow-lg shadow-blue-100/50">
                                        <span className="text-3xl relative z-10 drop-shadow-md">🤖</span>
                                    </div>
                                    <div>
                                        <h1 className="text-2xl font-black text-[#0f1d33]">LTV 적정성 검증 Agent</h1>
                                        <p className="text-[15px] font-bold text-slate-400 mt-1">대화형 AI 데이터 분석 지원창구</p>
                                    </div>
                                </div>
                                <button
                                    onClick={() => { setShowDashboard(true); setIsSidebarOpen(true); }}
                                    className="px-6 py-3.5 bg-blue-600 text-white font-bold rounded-2xl hover:bg-blue-700 shadow-xl shadow-blue-600/20 transition-all hover:scale-[1.02] active:scale-95 flex items-center gap-2"
                                >
                                    전체 대시보드 보기 ➔
                                </button>
                            </div>
                        ) : (
                            <div className={`flex items-center justify-between shrink-0 ${isChatFullscreen ? "mb-6" : "mt-1 mb-0"}`}>
                                <h2 className={`font-black text-[#0f1d33] flex items-center gap-2 ${isChatFullscreen ? "text-[24px]" : "text-[17px]"}`}>
                                    <span className={`rounded-full bg-gradient-to-br from-[#42a5f5] to-[#26c6da] flex items-center justify-center text-sm shadow-sm relative z-10 ${isChatFullscreen ? "w-10 h-10 text-lg" : "w-8 h-8"}`}>🤖</span>
                                    AI 챗봇 {isChatFullscreen && "- 전체 화면"}
                                </h2>
                                <div className="flex items-center gap-2">
                                    <button onClick={() => setIsChatFullscreen(!isChatFullscreen)} className="text-slate-400 hover:text-slate-700 bg-white hover:bg-slate-200 border border-[#dce5f0] p-1.5 rounded-full transition-colors shadow-sm" title={isChatFullscreen ? "축소하기" : "전체화면"}>
                                        {isChatFullscreen ? (
                                            <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2.5} d="M9 9V4.5M9 9H4.5M9 9L3.75 3.75M9 15v4.5M9 15H4.5M9 15l-5.25 5.25M15 9h4.5M15 9V4.5M15 9l5.25-5.25M15 15h4.5M15 15v4.5m0-4.5l5.25 5.25" /></svg>
                                        ) : (
                                            <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2.5} d="M3.75 3.75v4.5m0-4.5h4.5m-4.5 0L9 9M3.75 20.25v-4.5m0 4.5h4.5m-4.5 0L9 15M20.25 3.75h-4.5m4.5 0v4.5m0-4.5L15 9m5.25 11.25h-4.5m4.5 0v-4.5m0 4.5L15 15" /></svg>
                                        )}
                                    </button>
                                    {!isChatFullscreen && (
                                        <button onClick={() => setIsSidebarOpen(false)} className="text-slate-400 hover:text-slate-700 bg-white hover:bg-slate-200 border border-[#dce5f0] p-1.5 rounded-full transition-colors shadow-sm">
                                            <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2.5} d="M6 18L18 6M6 6l12 12" /></svg>
                                        </button>
                                    )}
                                </div>
                            </div>
                        )}

                        {showDashboard && !isChatFullscreen && (
                            <div className="rounded-2xl border border-[#dce5f0] bg-white px-5 py-4 shadow-sm relative overflow-hidden shrink-0 mt-4">
                                <div className="text-[12px] font-semibold text-[#7c8da6] tracking-wider relative z-10">검토 기준일</div>
                                <div className="mt-2 text-[30px] font-black tracking-[-0.03em] text-[#0f1d33] leading-none relative z-10">{dateCard}</div>
                            </div>
                        )}

                        <div className={`flex flex-col flex-1 min-h-0 ${!showDashboard ? "p-6 bg-slate-50 relative" : "mt-4"}`}>
                            <ChatAgent
                                bank={bank}
                                baseDate={baseDate}
                                onSetDate={setBaseDate}
                                onOpenDashboard={() => { setShowDashboard(true); setIsSidebarOpen(true); }}
                            />
                        </div>
                    </aside>
                </div>
            </div>

            {/* Floating Action Button */}
            {!isSidebarOpen && showDashboard && (
                <button
                    onClick={() => setIsSidebarOpen(true)}
                    className="fixed bottom-8 right-8 z-40 flex items-center gap-2 bg-[#0f1d33] text-white px-5 py-3.5 rounded-full shadow-2xl hover:bg-[#1e3a8a] transition-all hover:scale-105 active:scale-95"
                >
                    <span className="text-xl drop-shadow-md">🤖</span>
                    <span className="font-bold tracking-wide">AI 챗봇 열기</span>
                </button>
            )}

            {modalItem && <DetailModal item={modalItem} bank={bank} baseDate={lastUpdate} onClose={closeDetailModal} setPageLoading={setPageLoading} />}
            {isLtvTableOpen && <LtvTableModal bank={bank} baseDate={lastUpdate} onClose={closeLtvTableModal} setPageLoading={setPageLoading} />}
            {isLogModalOpen && <LtvLogModal bank={bank} onClose={() => setIsLogModalOpen(false)} />}

            {/* 글로벌 로딩 레이어 */}
            {pageLoading.active && <GlobalLoadingOverlay message={pageLoading.message} />}
        </div>
    );
}

/* ─── Nav 버튼 ─── */
function NavBtn({ children, onClick }) {
    return (
        <button onClick={onClick}
            className="flex items-center gap-1.5 rounded-full border border-[#dce5f0] bg-white px-4 py-1.5 text-[13px] font-semibold text-[#5b6f86] hover:bg-slate-50 transition shadow-sm">
            {children}
        </button>
    );
}

function MonthPicker({ value, onChange }) {
    const [open, setOpen] = useState(false);
    const [y, m] = value.split("-").map(Number);
    const [viewYear, setViewYear] = useState(y);
    const ref = useRef(null);

    const now = new Date();
    const nowYear = now.getFullYear();
    const nowMonth = now.getMonth() + 1; // 1~12

    useEffect(() => { setViewYear(y); }, [y]);

    useEffect(() => {
        if (!open) return;
        function handleClick(e) { if (ref.current && !ref.current.contains(e.target)) setOpen(false); }
        document.addEventListener("mousedown", handleClick);
        return () => document.removeEventListener("mousedown", handleClick);
    }, [open]);

    const months = ["1월", "2월", "3월", "4월", "5월", "6월", "7월", "8월", "9월", "10월", "11월", "12월"];

    function pick(mi) {
        onChange(`${viewYear}-${String(mi + 1).padStart(2, "0")}`);
        setOpen(false);
    }

    function isFuture(mi) {
        return viewYear > nowYear || (viewYear === nowYear && mi + 1 > nowMonth);
    }

    return (
        <div className="relative" ref={ref}>
            <button
                onClick={() => setOpen(p => !p)}
                className="flex items-center gap-2 bg-white/70 backdrop-blur-sm rounded-xl border border-slate-200 px-3 py-1.5 shadow-sm cursor-pointer hover:border-blue-300 hover:bg-white transition-all"
            >
                <span className="text-[14px]">📅</span>
                <span className="text-[14px] font-black text-slate-700 tracking-tight">{value.replace("-", ". ")}.</span>
                <svg className={`w-3 h-3 text-slate-400 transition-transform ${open ? "rotate-180" : ""}`} fill="none" stroke="currentColor" viewBox="0 0 24 24"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2.5" d="M19 9l-7 7-7-7" /></svg>
            </button>

            {open && (
                <div className="absolute top-full mt-2 right-0 z-50 bg-white rounded-2xl shadow-2xl border border-slate-200 p-4 w-[260px]">
                    <div className="flex items-center justify-between mb-3">
                        <button onClick={() => setViewYear(v => v - 1)} className="w-8 h-8 flex items-center justify-center rounded-lg text-slate-400 hover:text-blue-600 hover:bg-blue-50 transition-all text-lg font-bold">‹</button>
                        <span className="text-[16px] font-black text-slate-800">{viewYear}</span>
                        <button
                            onClick={() => viewYear < nowYear && setViewYear(v => v + 1)}
                            disabled={viewYear >= nowYear}
                            className={`w-8 h-8 flex items-center justify-center rounded-lg transition-all text-lg font-bold ${viewYear >= nowYear ? "text-slate-200 cursor-not-allowed" : "text-slate-400 hover:text-blue-600 hover:bg-blue-50"}`}
                        >›</button>
                    </div>
                    <div className="grid grid-cols-3 gap-2">
                        {months.map((label, i) => {
                            const isSelected = viewYear === y && i + 1 === m;
                            const disabled = isFuture(i);
                            return (
                                <button
                                    key={i}
                                    onClick={() => !disabled && pick(i)}
                                    disabled={disabled}
                                    className={`py-2 rounded-xl text-[13px] font-bold transition-all ${isSelected
                                        ? "bg-blue-600 text-white shadow-md shadow-blue-200"
                                        : disabled
                                            ? "text-slate-300 cursor-not-allowed bg-slate-50"
                                            : "text-slate-600 hover:bg-slate-100 border border-slate-150"
                                        }`}
                                >
                                    {label}
                                </button>
                            );
                        })}
                    </div>
                </div>
            )}
        </div>
    );
}


/* ─── 챗봇 컴포넌트 ─── */
function ChatAgent({ bank, baseDate, onSetDate, onOpenDashboard }) {
    const [input, setInput] = useState("");
    const [history, setHistory] = useState([]);
    const [loading, setLoading] = useState(false);
    const [currentTask, setCurrentTask] = useState(""); // AI가 현재 수행 중인 작업 명칭
    const scrollRef = useRef(null);
    const API_BASE = API_BASE_URL;

    function toEndOfMonth(ym) {
        if (!ym) return null;
        let [y, m] = ym.split("-");
        return `${y}-${m.padStart(2, "0")}`;
    }

    const listRef = useRef(null);

    useEffect(() => {
        const timer = setTimeout(() => {
            if (listRef.current) {
                listRef.current.scrollTop = listRef.current.scrollHeight;
            }
        }, 10);
        return () => clearTimeout(timer);
    }, [history, loading, currentTask]);

    const handleSubmit = async (e) => {
        if (e) e.preventDefault();
        if (!input.trim() || loading) return;

        const msg = input.trim();
        setInput("");
        setHistory(prev => [...prev, { role: "user", text: msg }]);
        setLoading(true);
        setCurrentTask("답변을 준비하고 있습니다...");

        try {
            // 스트리밍 응답을 받기 위해 fetch 사용
            const response = await fetch(`${API_BASE}/api/chat`, {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({
                    message: msg,
                    bank,
                    base_date: toEndOfMonth(baseDate)
                })
            });

            if (!response.ok) throw new Error("서버 응답 오류");

            const reader = response.body.getReader();
            const decoder = new TextDecoder();
            let accumulated = "";

            while (true) {
                const { value, done } = await reader.read();
                if (done) break;

                accumulated += decoder.decode(value, { stream: true });
                const lines = accumulated.split("\n");
                accumulated = lines.pop(); // 아직 완료되지 않은 마지막 줄 저장

                for (const line of lines) {
                    if (!line.trim()) continue;
                    try {
                        const data = JSON.parse(line);

                        // 1) 상태 업데이트인 경우
                        if (data.status) {
                            setCurrentTask(data.status);
                        }

                        // 2) 최종 답변인 경우
                        if (data.answer) {
                            setHistory(prev => [...prev, { role: "assistant", text: data.answer }]);
                            if (data.actions) {
                                data.actions.forEach(act => {
                                    if (act.action === "set_date" && onSetDate) {
                                        onSetDate(act.value);
                                        if (onOpenDashboard) onOpenDashboard();
                                    }
                                    if (act.action === "open_dashboard" && onOpenDashboard) {
                                        onOpenDashboard();
                                    }
                                });
                            }
                        }

                        // 3) 에러인 경우
                        if (data.error) {
                            setHistory(prev => [...prev, { role: "assistant", text: `오류: ${data.error}` }]);
                        }
                    } catch (e) {
                        console.error("데이터 파싱 실패:", e);
                    }
                }
            }
        } catch (e) {
            setHistory(prev => [...prev, { role: "assistant", text: "챗봇 연결 중 오류가 발생했습니다." }]);
        } finally {
            setLoading(false);
            setCurrentTask("");
        }
    };

    return (
        <div className="rounded-2xl border border-[#dce5f0] bg-white px-5 py-5 shadow-sm flex flex-col flex-1 min-h-[300px]">
            <div className="text-[15px] font-bold text-[#0f1d33] mb-3 shrink-0 flex items-center gap-2">
                <div className="w-7 h-7 rounded-full bg-gradient-to-br from-[#42a5f5] to-[#26c6da] flex items-center justify-center text-sm shadow-sm">🤖</div>
                LTV 적정성 Agent
            </div>

            <div ref={listRef} className="flex-1 overflow-y-auto space-y-3 mb-3 pr-1 custom-scrollbar min-h-[80px]">
                <div className="flex items-start gap-2.5">
                    <div className="w-7 h-7 rounded-full bg-gradient-to-br from-[#42a5f5] to-[#26c6da] flex items-center justify-center text-sm shadow-sm">🤖</div>
                    <div className="bg-[#f3f6fa] border border-[#e3eaf3] rounded-xl px-3 py-2 text-[13px] text-[#5b6f86] leading-relaxed">
                        <p className="font-bold text-[#1e2d44]">안녕하세요!</p>
                        <p className="mt-0.5">궁금한 점이 있다면 무엇이든 물어보세요.</p>
                    </div>
                </div>

                {history.map((chat, i) => (
                    <div key={i} className={`flex items-start gap-2.5 ${chat.role === "user" ? "flex-row-reverse" : ""}`}>
                        {chat.role === "assistant" && (
                            <div className="w-7 h-7 rounded-full bg-gradient-to-br from-[#42a5f5] to-[#26c6da] flex items-center justify-center text-xs shrink-0 shadow-sm">🤖</div>
                        )}
                        <div className={`rounded-xl px-3 py-2 text-[13px] leading-relaxed max-w-[88%] whitespace-pre-wrap ${chat.role === "user" ? "bg-blue-600 text-white font-medium" : "bg-[#f3f6fa] border border-[#e3eaf3] text-[#3a4d63]"
                            }`}>
                            {chat.text}
                        </div>
                    </div>
                ))}

                {loading && (
                    <div className="flex items-start gap-2.5">
                        <div className="w-7 h-7 rounded-full bg-slate-200 flex items-center justify-center text-xs shrink-0 animate-pulse">🤖</div>
                        <div className="bg-slate-100/80 backdrop-blur-sm border border-slate-200 rounded-xl px-3 py-2 text-[13px] text-slate-500 flex items-center gap-2">
                            <div className="flex gap-0.5">
                                <span className="animate-bounce w-1 h-1 bg-slate-400 rounded-full" style={{ animationDelay: "0s" }}></span>
                                <span className="animate-bounce w-1 h-1 bg-slate-400 rounded-full" style={{ animationDelay: "0.15s" }}></span>
                                <span className="animate-bounce w-1 h-1 bg-slate-400 rounded-full" style={{ animationDelay: "0.3s" }}></span>
                            </div>
                            <span className="font-semibold text-slate-600 animate-pulse">{currentTask || "분석 중..."}</span>
                        </div>
                    </div>
                )}
            </div>

            <form onSubmit={handleSubmit} className="flex gap-2 shrink-0">
                <input
                    type="text"
                    value={input}
                    onChange={e => setInput(e.target.value)}
                    placeholder={loading ? "분석 중..." : "질문을 입력해 보세요..."}
                    disabled={loading}
                    className="flex-1 rounded-lg border border-[#dce5f0] bg-[#f8fafc] px-3 py-2 text-[13px] text-slate-700 outline-none focus:border-blue-400 focus:bg-white transition disabled:opacity-50"
                />
                <button
                    type="submit"
                    disabled={loading || !input.trim()}
                    className="w-9 h-9 rounded-lg bg-[#3b82f6] text-white flex items-center justify-center hover:bg-[#2563eb] transition shrink-0 shadow-sm disabled:opacity-40"
                >
                    <svg className="w-4 h-4" fill="currentColor" viewBox="0 0 24 24"><path d="M2.01 21L23 12 2.01 3 2 10l15 2-15 2z" /></svg>
                </button>
            </form>
        </div>
    );
}

/* ─── 로그인 & 회원가입 ─── */
function AuthPage({ onLogin }) {
    const banks = ["광주은행", "전북은행"];
    const [selBank, setSelBank] = useState("광주은행");
    const [username, setUsername] = useState("");
    const [pw, setPw] = useState("");
    const [isBankOpen, setIsBankOpen] = useState(false);
    const [isLoading, setIsLoading] = useState(false);
    const [isSignupOpen, setIsSignupOpen] = useState(false);

    const handleLogin = async (e) => {
        e.preventDefault();
        const trimmedUsername = username.trim();
        if (!trimmedUsername || !pw.trim()) return alert("아이디와 비밀번호를 입력해주세요.");

        setIsLoading(true);
        try {
            const res = await axios.post(`${API}/api/auth/login`, {
                bank: selBank,
                username: trimmedUsername,
                password: pw
            });
            if (res.data.ok) {
                onLogin(res.data.bank, res.data.username);
            }
        } catch (err) {
            alert(err.response?.data?.detail || "로그인 실패: 아이디와 비밀번호를 확인해주세요.");
        } finally {
            setIsLoading(false);
        }
    };

    return (
        <div className="min-h-screen bg-gradient-to-br from-[#f0f4fa] to-[#dce8f8] flex items-center justify-center px-6">
            <div className="w-full max-w-[450px] bg-white/70 backdrop-blur-xl rounded-[40px] border border-white p-12 shadow-2xl relative">
                <div className="mb-10 text-center relative z-10">
                    <div className="inline-block px-4 py-1.5 rounded-full bg-blue-50 text-blue-600 text-[13px] font-black mb-4 tracking-widest">
                        MEMBER LOGIN
                    </div>
                    <h1 className="text-4xl font-black tracking-[-0.05em] text-[#0f1d33]">로그인</h1>
                    <p className="text-slate-400 font-bold mt-2">서비스 이용을 위해 계정에 접속하세요</p>
                </div>

                <form onSubmit={handleLogin} className="space-y-6 relative z-10">
                    <div className="space-y-2 relative">
                        <label className="text-[13px] font-bold text-slate-400 ml-1">접속 은행</label>
                        <div className="relative">
                            <button
                                type="button"
                                onClick={() => setIsBankOpen(!isBankOpen)}
                                className={`w-full h-14 flex items-center justify-between px-6 rounded-2xl border transition-all duration-300 ${isBankOpen ? "bg-white border-blue-400 ring-4 ring-blue-50" : "bg-white/60 border-slate-200 hover:border-slate-300"}`}
                            >
                                <span className="text-lg font-bold text-[#1b2a40]">{selBank}</span>
                                <svg className={`w-5 h-5 text-slate-400 transition-transform duration-300 ${isBankOpen ? "rotate-180" : ""}`} fill="none" stroke="currentColor" viewBox="0 0 24 24">
                                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="3" d="M19 9l-7 7-7-7" />
                                </svg>
                            </button>

                            {isBankOpen && (
                                <div className="absolute mt-2 w-full bg-white border border-slate-100 rounded-2xl shadow-2xl z-20 py-2 overflow-hidden animate-in fade-in slide-in-from-top-2">
                                    {banks.map((b) => (
                                        <button
                                            key={b}
                                            type="button"
                                            onClick={() => { setSelBank(b); setIsBankOpen(false); }}
                                            className="w-full text-left px-6 py-3.5 text-[16px] font-bold text-slate-700 hover:bg-blue-50 hover:text-blue-600 transition-colors"
                                        >
                                            {b}
                                        </button>
                                    ))}
                                </div>
                            )}
                        </div>
                    </div>

                    <div className="space-y-2">
                        <label className="text-[13px] font-bold text-slate-400 ml-1">아이디</label>
                        <input
                            type="text"
                            placeholder="사용자 아이디"
                            value={username}
                            onChange={(e) => setUsername(e.target.value)}
                            className="w-full h-14 bg-white/60 border border-slate-200 rounded-2xl px-6 text-lg font-bold text-[#1b2a40] outline-none focus:border-blue-400 focus:ring-4 focus:ring-blue-50 transition-all shadow-sm"
                        />
                    </div>

                    <div className="space-y-2">
                        <label className="text-[13px] font-bold text-slate-400 ml-1">비밀번호</label>
                        <input
                            type="password"
                            placeholder="••••••••"
                            value={pw}
                            onChange={(e) => setPw(e.target.value)}
                            className="w-full h-14 bg-white/60 border border-slate-200 rounded-2xl px-6 text-lg font-bold text-[#1b2a40] outline-none focus:border-blue-400 focus:ring-4 focus:ring-blue-50 transition-all shadow-sm"
                        />
                    </div>

                    <button
                        type="submit"
                        disabled={isLoading}
                        className="w-full h-16 bg-blue-600 text-white rounded-2xl text-[18px] font-black shadow-xl shadow-blue-200/50 hover:bg-blue-700 hover:scale-[1.02] active:scale-[0.98] transition-all duration-300 mt-4 disabled:opacity-50 disabled:scale-100"
                    >
                        {isLoading ? (
                            <div className="flex items-center justify-center gap-2">
                                <div className="w-5 h-5 border-3 border-white/30 border-t-white rounded-full animate-spin" />
                                로그인 중...
                            </div>
                        ) : "로그인하기"}
                    </button>
                </form>

                <div className="mt-8 text-center relative z-10 flex items-center justify-center gap-2 text-slate-400 font-bold text-[14px]">
                    아직 회원이 아니신가요?
                    <button
                        onClick={() => setIsSignupOpen(true)}
                        className="text-blue-600 hover:underline transition-all"
                    >
                        회원가입
                    </button>
                </div>
            </div>

            {isSignupOpen && <SignupModal onClose={() => setIsSignupOpen(false)} />}
        </div>
    );
}

function SignupModal({ onClose }) {
    const banks = ["광주은행", "전북은행"];
    const [selBank, setSelBank] = useState("광주은행");
    const [username, setUsername] = useState("");
    const [pw, setPw] = useState("");
    const [pwConfirm, setPwConfirm] = useState("");
    const [isBankOpen, setIsBankOpen] = useState(false);
    const [isChecking, setIsChecking] = useState(false);
    const [idChecked, setIdChecked] = useState(false);
    const [lastCheckedUsername, setLastCheckedUsername] = useState("");
    const [checkFeedback, setCheckFeedback] = useState({ tone: "", message: "" });
    const [isLoading, setIsLoading] = useState(false);
    const checkAbortRef = useRef(null);
    const checkCacheRef = useRef(new Map());

    const handleCheckId = async () => {
        if (!username.trim()) return alert("아이디를 입력해주세요.");
        setIsChecking(true);
        try {
            const res = await axios.get(`${API}/api/auth/check-username`, { params: { bank: selBank, username } });
            if (res.data.exists) {
                alert("이미 존재하는 아이디입니다.");
                setIdChecked(false);
            } else {
                alert("사용 가능한 아이디입니다.");
                setIdChecked(true);
            }
        } catch {
            alert("아이디 중복 확인 중 오류가 발생했습니다.");
        } finally {
            setIsChecking(false);
        }
    };

    const handleCheckIdFast = async () => {
        const trimmedUsername = username.trim();

        if (!trimmedUsername) {
            setIdChecked(false);
            setLastCheckedUsername("");
            setCheckFeedback({ tone: "error", message: "아이디를 입력해주세요." });
            return;
        }

        if (trimmedUsername.length < 4) {
            setIdChecked(false);
            setLastCheckedUsername("");
            setCheckFeedback({ tone: "error", message: "아이디는 4자 이상 입력해주세요." });
            return;
        }

        if (checkCacheRef.current.has(trimmedUsername)) {
            const exists = checkCacheRef.current.get(trimmedUsername);
            setIdChecked(!exists);
            setLastCheckedUsername(trimmedUsername);
            setCheckFeedback({
                tone: exists ? "error" : "success",
                message: exists ? "이미 사용 중인 아이디입니다." : "사용 가능한 아이디입니다."
            });
            return;
        }

        checkAbortRef.current?.abort();
        const controller = new AbortController();
        checkAbortRef.current = controller;

        setIsChecking(true);
        setCheckFeedback({ tone: "info", message: "아이디 사용 가능 여부를 확인하고 있습니다..." });
        try {
            const res = await axios.get(`${API}/api/auth/check-username`, {
                params: { bank: selBank, username: trimmedUsername },
                signal: controller.signal,
                timeout: 5000,
            });
            const exists = !!res.data.exists;
            checkCacheRef.current.set(trimmedUsername, exists);
            setIdChecked(!exists);
            setLastCheckedUsername(trimmedUsername);
            setCheckFeedback({
                tone: exists ? "error" : "success",
                message: exists ? "이미 사용 중인 아이디입니다." : "사용 가능한 아이디입니다."
            });
        } catch (err) {
            if (axios.isCancel(err)) return;
            setIdChecked(false);
            setLastCheckedUsername("");
            setCheckFeedback({
                tone: "error",
                message: err.code === "ECONNABORTED"
                    ? "중복 확인 시간이 초과되었습니다. 잠시 후 다시 시도해주세요."
                    : "아이디 중복 확인 중 오류가 발생했습니다."
            });
        } finally {
            setIsChecking(false);
        }
    };

    const handleSignup = async (e) => {
        e.preventDefault();
        const trimmedUsername = username.trim();
        if (!idChecked) return alert("아이디 중복 확인이 필요합니다.");
        if (!pw.trim()) return alert("비밀번호를 입력해주세요.");
        if (pw !== pwConfirm) return alert("비밀번호가 일치하지 않습니다.");

        setIsLoading(true);
        try {
            const res = await axios.post(`${API}/api/auth/signup`, {
                bank: selBank,
                username: trimmedUsername,
                password: pw
            });
            if (res.data.ok) {
                alert("회원가입이 완료되었습니다! 로그인해주세요.");
                onClose();
            }
        } catch (err) {
            alert(err.response?.data?.detail || "회원가입 실패: 관리자에게 문의하세요.");
        } finally {
            setIsLoading(false);
        }
    };

    return (
        <div className="fixed inset-0 z-[100] flex items-center justify-center bg-black/60 backdrop-blur-sm p-4" onClick={(e) => e.target === e.currentTarget && onClose()}>
            <div className="bg-white rounded-[32px] shadow-2xl w-full max-w-[480px] p-10 animate-in zoom-in-95 fade-in duration-300">
                <div className="mb-8 flex items-center justify-between">
                    <div>
                        <h2 className="text-[28px] font-black text-slate-900 tracking-tight">회원가입</h2>
                        <p className="text-[14px] font-bold text-slate-400 mt-1">계정을 생성하여 서비스를 시작하세요</p>
                    </div>
                    <button onClick={onClose} className="p-2 hover:bg-slate-100 rounded-full transition-colors text-slate-400">
                        <svg className="w-6 h-6" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={3} d="M6 18L18 6M6 6l12 12" /></svg>
                    </button>
                </div>

                <form onSubmit={handleSignup} className="space-y-5">
                    <div className="space-y-1.5 relative">
                        <label className="text-[13px] font-bold text-slate-500 ml-1">소속 은행</label>
                        <button
                            type="button"
                            onClick={() => setIsBankOpen(!isBankOpen)}
                            className={`w-full h-12 flex items-center justify-between px-5 rounded-xl border transition-all ${isBankOpen ? "border-blue-500 bg-white ring-4 ring-blue-50" : "bg-slate-50 border-slate-200"}`}
                        >
                            <span className="font-bold text-slate-700">{selBank}</span>
                            <svg className={`w-4 h-4 text-slate-400 transition-transform ${isBankOpen ? "rotate-180" : ""}`} fill="none" stroke="currentColor" viewBox="0 0 24 24"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2.5" d="M19 9l-7 7-7-7" /></svg>
                        </button>
                        {isBankOpen && (
                            <div className="absolute top-full left-0 mt-2 w-full bg-white border border-slate-100 rounded-xl shadow-xl z-50 py-1">
                                {banks.map(b => (
                                    <button key={b} type="button" onClick={() => { setSelBank(b); setIsBankOpen(false); }} className="w-full text-left px-5 py-2.5 font-bold text-slate-600 hover:bg-blue-50 hover:text-blue-600 transition-colors">{b}</button>
                                ))}
                            </div>
                        )}
                    </div>

                    <div className="space-y-1.5 text-left">
                        <label className="text-[13px] font-bold text-slate-500 ml-1">아이디</label>
                        <div className="flex gap-2">
                            <input
                                type="text"
                                value={username}
                                onChange={e => {
                                    setUsername(e.target.value);
                                    setIdChecked(false);
                                    setLastCheckedUsername("");
                                    setCheckFeedback({ tone: "", message: "" });
                                }}
                                className={`flex-1 h-12 bg-slate-50 border rounded-xl px-5 font-bold text-slate-700 outline-none transition-all ${idChecked ? "border-green-500 ring-4 ring-green-50" : "focus:border-blue-500 focus:bg-white border-slate-200"}`}
                                placeholder="사용할 아이디"
                            />
                            <button
                                type="button"
                                onClick={handleCheckIdFast}
                                disabled={isChecking || (idChecked && lastCheckedUsername === username.trim())}
                                className="px-4 h-12 bg-slate-800 text-white rounded-xl font-bold text-[13px] hover:bg-slate-700 transition-all disabled:opacity-50 whitespace-nowrap"
                            >
                                {isChecking ? "확인 중.." : (idChecked ? "확인됨" : "중복 확인")}
                            </button>
                        </div>
                        {checkFeedback.message && (
                            <p className={`text-[12px] font-bold mt-1.5 ml-1 animate-in fade-in slide-in-from-top-1 ${checkFeedback.tone === "error" ? "text-red-500" :
                                checkFeedback.tone === "success" ? "text-green-600" : "text-blue-500"
                                }`}>
                                {checkFeedback.tone === "error" ? "✕ " : (checkFeedback.tone === "success" ? "✓ " : "● ")}
                                {checkFeedback.message}
                            </p>
                        )}
                    </div>

                    <div className="space-y-1.5 text-left">
                        <label className="text-[13px] font-bold text-slate-500 ml-1">비밀번호</label>
                        <input
                            type="password"
                            value={pw}
                            onChange={e => setPw(e.target.value)}
                            className="w-full h-12 bg-slate-50 border border-slate-200 rounded-xl px-5 font-bold text-slate-700 outline-none focus:border-blue-500 focus:bg-white transition-all"
                            placeholder="••••••••"
                        />
                    </div>

                    <div className="space-y-1.5 text-left">
                        <label className="text-[13px] font-bold text-slate-500 ml-1">비밀번호 확인</label>
                        <input
                            type="password"
                            value={pwConfirm}
                            onChange={e => setPwConfirm(e.target.value)}
                            className={`w-full h-12 bg-slate-50 border rounded-xl px-5 font-bold text-slate-700 outline-none transition-all ${pwConfirm && pw === pwConfirm ? "border-green-500 ring-4 ring-green-50" : (pwConfirm && pw !== pwConfirm ? "border-red-500 ring-4 ring-red-50" : "focus:border-blue-500 focus:bg-white border-slate-200")}`}
                            placeholder="비밀번호 다시 입력"
                        />
                    </div>

                    <button
                        type="submit"
                        disabled={isLoading || isChecking}
                        className="w-full h-14 bg-blue-600 text-white rounded-2xl font-black text-lg shadow-xl shadow-blue-100 hover:bg-blue-700 transition-all mt-4 disabled:opacity-50"
                    >
                        {isLoading ? "가입 처리 중..." : "회원가입 완료"}
                    </button>
                </form>
            </div>
        </div>
    );
}


/* ─── 배너 ─── */
function Banner({ tone, children }) {
    const cls = {
        error: "bg-red-50 border-red-200 text-red-700",
        warning: "bg-amber-50 border-amber-200 text-amber-700",
        info: "bg-blue-50 border-blue-200 text-blue-700"
    }[tone] || "bg-slate-50 border-slate-200 text-slate-700";
    return <div className={`rounded-xl border px-4 py-3 text-sm font-semibold shadow-sm ${cls}`}>{children}</div>;
}

function SkeletonRows({ rows = 5 }) {
    return <div className="space-y-3">{Array.from({ length: rows }).map((_, i) => <div key={i} className="h-14 w-full animate-pulse rounded-xl bg-slate-100" />)}</div>;
}

/* ─── 요약 버킷 (상단 목록용) ─── */
function SummaryBucket({ title, tone, description, count, items, onItemClick, loading, loadingLlm }) {
    if (loading) {
        return <div className="h-[485px] rounded-3xl border border-white/60 bg-white/40 animate-pulse" />;
    }

    return (
        <div className="rounded-3xl border border-white/60 bg-white/60 backdrop-blur-md p-6 shadow-md flex flex-col h-[485px]">
            <div className="mb-4 flex items-start justify-between">
                <div className="flex items-center gap-2.5">
                    <span className={`inline-flex items-center gap-1.5 rounded-full px-3.5 py-1.5 text-[14px] font-black ${tone === "adjust" ? "bg-red-50 text-red-600" : "bg-amber-50 text-amber-600"}`}>
                        <span className={`h-2.5 w-2.5 rounded-full ${tone === "adjust" ? "bg-red-500" : "bg-amber-500"}`} />
                        {title}
                    </span>
                    <span className="text-[14px] font-bold text-slate-500">{description}</span>
                </div>
                <div className="text-[22px] font-black text-slate-800 leading-none">{count}건</div>
            </div>

            <div className="grid grid-cols-[1.5fr_0.7fr_0.7fr] items-center border-b border-slate-200/50 px-4 pb-2.5 text-[14px] font-bold text-slate-400 uppercase tracking-widest">
                <div>지역 / 용도</div>
                <div className="text-center">현재</div>
                <div className="text-center">조정안</div>
            </div>

            <div className="mt-4 space-y-2.5 overflow-y-auto flex-1 custom-scrollbar pr-1.5">
                {items.length === 0 ? (
                    <div className="h-full flex items-center justify-center text-slate-400 text-[15px] font-bold">해당 항목이 없습니다.</div>
                ) : items.map((item, index) => (
                    <button
                        key={`${item.region || item.지역}-${item.usage || item.용도}-${index}`}
                        type="button"
                        onClick={() => onItemClick(item)}
                        className="grid w-full grid-cols-[1.5fr_0.7fr_0.7fr] items-center rounded-2xl border border-slate-100 bg-white/80 px-5 py-2.5 text-left transition hover:border-blue-300 hover:shadow-xl group"
                    >
                        <div className="text-[21px] font-black text-slate-700 truncate group-hover:text-blue-600 transition-colors">
                            {displayValue(item.region || item.지역)} / {displayValue(item.usage || item.용도)}
                        </div>
                        <div className="text-center text-[22px] font-black text-slate-400">{formatPercent(item.current_ltv || item.LTV || item.ltv_val)}</div>
                        <div className={`text-center text-[24px] font-black ${tone === "adjust" ? "text-red-500" : "text-amber-500"}`}>
                            {loadingLlm ? (
                                <span className="text-[14px] text-blue-500 font-bold animate-pulse">AI 분석 중..</span>
                            ) : (
                                formatPercent(tone === "adjust" ? item.conservative_ltv : (item.relaxed_ltv || item.recommended_ltv || item.llm_ltv || item.suggested_ltv))
                            )}
                        </div>
                    </button>
                ))}
            </div>
        </div>
    );
}

/* ─── 유틸 ─── */
function normalize(v) { return String(v ?? "").trim().toLowerCase(); }

function formatPercent(value) {
    if (value == null || value === "") return "-";
    if (typeof value === "string" && value.includes("%")) return value;
    const num = typeof value === "number" ? value : parseFloat(String(value).replace(/[^\d.-]/g, ""));
    if (Number.isNaN(num)) return String(value);
    const normalized = num <= 1 ? num * 100 : num;
    return `${normalized.toFixed(normalized % 1 === 0 ? 0 : 1)}%`;
}

function displayValue(value, fallback = "-") {
    return value == null || value === "" ? fallback : String(value);
}

function LtvLogModal({ bank, onClose }) {
    const [logs, setLogs] = useState([]);
    const [loading, setLoading] = useState(true);

    useEffect(() => {
        const fetchLogs = async () => {
            try {
                const res = await axios.get(`${API}/api/ltv-logs`, { params: { bank } });
                setLogs(res.data || []);
            } catch (err) {
                console.error("로그 로딩 실패:", err);
            } finally {
                setLoading(false);
            }
        };
        fetchLogs();
    }, [bank]);

    return (
        <div className="fixed inset-0 z-[110] flex items-center justify-center p-6 bg-slate-900/40 backdrop-blur-sm animate-in fade-in duration-300">
            <div className="w-full max-w-4xl bg-white rounded-[32px] shadow-2xl overflow-hidden flex flex-col max-h-[85vh] animate-in zoom-in-95 duration-300">
                <div className="p-8 border-b border-slate-100 flex items-center justify-between shrink-0 bg-slate-50/50">
                    <div>
                        <h2 className="text-2xl font-black text-slate-800 flex items-center gap-2">
                            📜 {bank} LTV 변경 이력
                        </h2>
                        <p className="text-slate-500 font-bold mt-1">시스템에서 발생한 모든 LTV 조정 로그를 시간순으로 확인합니다.</p>
                    </div>
                    <button onClick={onClose} className="w-12 h-12 flex items-center justify-center rounded-2xl hover:bg-white hover:shadow-md transition-all text-slate-400 hover:text-slate-600">
                        <svg className="w-6 h-6" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2.5" d="M6 18L18 6M6 6l12 12" /></svg>
                    </button>
                </div>

                <div className="flex-1 overflow-y-auto p-8 bg-slate-50">
                    {loading ? (
                        <div className="space-y-3">
                            {Array.from({ length: 10 }).map((_, i) => (
                                <div key={i} className="h-14 w-full animate-pulse rounded-xl bg-slate-200" />
                            ))}
                        </div>
                    ) : logs.length === 0 ? (
                        <div className="text-center py-20 bg-white rounded-3xl border border-dashed border-slate-200">
                            <span className="text-4xl">📭</span>
                            <p className="text-slate-400 font-bold mt-4">저장된 변경 이력이 없습니다.</p>
                        </div>
                    ) : (
                        <div className="space-y-2">
                            {logs.map((log) => (
                                <div key={log.id} className="font-mono text-[13px] text-slate-700 bg-white p-4 rounded-xl border border-slate-100 shadow-sm leading-relaxed whitespace-pre-wrap">
                                    <span className="text-slate-400 font-bold">[{log.created_at}]</span> 은행: {log.bank} | 지역: {log.region} | 용도: {log.usage} | 변경: <span className="text-blue-600 font-bold">{log.old_ltv}%</span> → <span className="text-red-600 font-black">{log.new_ltv}%</span> | 적용시작일: {log.effective_date} <span className="text-slate-400">{log.suffix || ""}</span>
                                </div>
                            ))}
                        </div>
                    )}
                </div>

                <div className="p-6 border-t border-slate-100 flex justify-end shrink-0 bg-white">
                    <button onClick={onClose} className="px-8 py-3 bg-slate-800 text-white font-black rounded-xl hover:bg-slate-700 transition-all shadow-lg shadow-slate-200">
                        닫기
                    </button>
                </div>
            </div>
        </div>
    );
}

/* ─── 상단 슬라이드 로딩 바 ─── */
function GlobalLoadingOverlay({ message }) {
    return (
        <div className="fixed top-0 left-0 right-0 z-[10000] flex justify-center pointer-events-none animate-in slide-in-from-top-full duration-500 ease-[cubic-bezier(0.2,0.8,0.2,1)]">
            <div className="mt-4 mx-6 w-full max-w-[500px] bg-white/80 backdrop-blur-xl border border-blue-100 shadow-[0_20px_40px_rgba(0,0,0,0.1)] rounded-2xl overflow-hidden pointer-events-auto">
                <div className="px-6 py-3.5 flex items-center justify-between gap-4">
                    <div className="flex items-center gap-3 min-w-0">
                        <div className="relative flex-shrink-0">
                            <div className="w-8 h-8 bg-blue-50 rounded-lg flex items-center justify-center">
                                <span className="text-lg animate-spin" style={{ animationDuration: '3s' }}>⚙️</span>
                            </div>
                        </div>
                        <div className="min-w-0">
                            <div className="text-[11px] font-black text-blue-500 uppercase tracking-widest mb-0.5">System Processing</div>
                            <div className="text-[14px] font-bold text-slate-800 truncate">{message}</div>
                        </div>
                    </div>
                    <div className="flex items-center gap-2 px-3 py-1 bg-blue-50 rounded-full shrink-0">
                        <span className="w-1.5 h-1.5 bg-blue-500 rounded-full animate-pulse"></span>
                        <span className="text-[11px] font-black text-blue-600">ACTIVE</span>
                    </div>
                </div>
                {/* 하단 진행 상태 바 애니메이션 */}
                <div className="h-1 w-full bg-slate-100 relative overflow-hidden">
                    <div className="absolute top-0 bottom-0 left-0 bg-gradient-to-r from-blue-400 via-blue-600 to-blue-400 w-1/3 animate-[loading-slide_1.5s_infinite_ease-in-out]"></div>
                </div>
            </div>
            <style dangerouslySetInnerHTML={{
                __html: `
                @keyframes loading-slide {
                    0% { transform: translateX(-100%); width: 30%; }
                    50% { width: 50%; }
                    100% { transform: translateX(300%); width: 30%; }
                }
            `}} />
        </div>
    );
}
