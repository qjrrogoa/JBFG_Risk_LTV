import { useState, useEffect, useCallback, useRef, useMemo } from "react";
import axios from "axios";
import UrgentTable from "./components/UrgentTable";
import MatrixTable from "./components/MatrixTable";
import DetailModal from "./components/DetailModal";

const API = "http://localhost:8000";

/* ─── App 엔트리 ─── */
export default function App() {
    const [bank, setBank] = useState(() => sessionStorage.getItem("bank") || null);
    function handleLogin(bankName) { sessionStorage.setItem("bank", bankName); setBank(bankName); }
    function handleLogout() { sessionStorage.removeItem("bank"); setBank(null); }
    if (!bank) return <LoginPage onLogin={handleLogin} />;
    return <Dashboard bank={bank} onLogout={handleLogout} />;
}

/* ─── 대시보드 ─── */
function Dashboard({ bank, onLogout }) {
    const today = new Date().toISOString().slice(0, 7);
    const [baseDate, setBaseDate] = useState(today);
    const [showDashboard, setShowDashboard] = useState(false);
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
    const chatEndRef = useRef(null);

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
        setLoadingU(true); setLoadingLlm(true); setLoadingM(true);
        setError(""); setLlmError(false); setLlmErrMsg(""); setUrgentList([]);

        axios.get(`${API}/api/urgent-signals`, { params, signal })
            .then(r => {
                if (!signal.aborted) {
                    const data = Array.isArray(r.data) ? r.data : [];
                    setUrgentList(prev => {
                        // 만약 이미 AI 데이터(conservative_ltv가 있는 데이터)가 들어왔다면 덮어쓰지 않음
                        if (prev.length > 0 && prev[0].conservative_ltv !== null) return prev;
                        return data;
                    });
                    setLastUpdate(baseDate);
                }
            })
            .catch(e => { if (!axios.isCancel(e) && !signal.aborted) setError("긴급 신호 데이터를 불러오지 못했습니다."); })
            .finally(() => { if (!signal.aborted) setLoadingU(false); });

        axios.get(`${API}/api/urgent-list`, { params, signal, timeout: 600000 }) // 10분으로 연장
            .then(r => {
                if (!signal.aborted) {
                    const data = Array.isArray(r.data) ? r.data : [];
                    if (data.length > 0) setUrgentList(data);
                }
            })
            .catch(e => {
                if (axios.isCancel(e) || signal.aborted) return;
                setLlmError(true);
                setLlmErrMsg(e?.response?.data?.detail || e?.message || "");
            })
            .finally(() => { if (!signal.aborted) setLoadingLlm(false); });

        axios.get(`${API}/api/matrix`, { params, signal })
            .then(r => { if (!signal.aborted) setMatrixData(Array.isArray(r.data) ? r.data : []); })
            .catch(e => { if (!axios.isCancel(e) && !signal.aborted) setError(p => p || "매트릭스 데이터를 불러오지 못했습니다."); })
            .finally(() => { if (!signal.aborted) setLoadingM(false); });
    }, [bank, baseDate]);

    useEffect(() => { fetchAll(); return () => abortRef.current?.abort(); }, [fetchAll]);

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

    function openFromMatrix(row) {
        const matched = urgentList.find(i => normalize(i.region || i.지역) === normalize(row.지역 || row.region) && normalize(i.usage || i.용도) === normalize(row.용도 || row.usage));
        if (matched) { setModalItem({ ...matched, 대분류: row.대분류 || row.category || row.group, hideAdvice: true }); return; }
        setModalItem({ region: row.지역 || row.region, usage: row.용도 || row.usage, category: row.대분류 || row.category || row.group, current_ltv: row.LTV || row.current_ltv || row.ltv_val, ltv_val: row.LTV || row.current_ltv || row.ltv_val, hideAdvice: true });
    }

    const [yy, mm] = baseDate.split("-");
    const dateCard = `${yy.slice(2)}.${mm}.01`;

    return (
        <div className="min-h-screen bg-[#f0f4fa]">
            {showDashboard && (
                <nav className="sticky top-0 z-30 flex items-center justify-end gap-3 bg-white/80 backdrop-blur-md border-b border-[#dce5f0] px-8 py-2">
                    <NavBtn>
                        <span>📅</span>
                        <input type="month" value={baseDate} onChange={e => setBaseDate(e.target.value)}
                            className="bg-transparent outline-none text-[13px] font-semibold text-slate-700 cursor-pointer" />
                    </NavBtn>
                    <NavBtn onClick={fetchAll}>🔄 새로고침</NavBtn>
                    <NavBtn onClick={onLogout}>🚪 로그아웃</NavBtn>
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
                                description="LTV 조정 권고"
                                count={stats.adjustCount}
                                items={stats.adjust}
                                onItemClick={setModalItem}
                                loading={loadingU}
                                loadingLlm={loadingLlm}
                            />
                            <SummaryBucket
                                title="검토 필요"
                                tone="review"
                                description="LTV 검토 권고"
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
                                <span className="text-[15px] font-bold text-blue-800">AI 실시간 권고안을 정밀 분석 중입니다...</span>
                            </div>
                            <span className="text-[13px] font-medium text-blue-500 italic">데이터 양에 따라 1~3분 정도 소요될 수 있습니다.</span>
                        </div>
                    )}
                    {llmError && <Banner tone="warning">{llmErrMsg || "AI 권고안을 불러오지 못했습니다."}</Banner>}

                    <section className="rounded-2xl bg-white border border-[#dce5f0] shadow-sm overflow-hidden">
                        <div className="px-6 pt-5 pb-3">
                            <h2 className="text-[28px] font-black text-[#0f1d33] flex items-center gap-2">
                                <span>🏷️</span> LTV조정 및 검토 우선순위
                            </h2>
                        </div>
                        <div className="px-6 pb-5">
                            {loadingU ? <SkeletonRows rows={6} /> : (
                                <UrgentTable urgentList={urgentList} onRowClick={setModalItem} llmLoading={loadingLlm} bank={bank} baseDate={lastUpdate} />
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

            {modalItem && <DetailModal item={modalItem} bank={bank} baseDate={lastUpdate} onClose={() => setModalItem(null)} />}
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

/* ─── 챗봇 컴포넌트 ─── */
function ChatAgent({ bank, baseDate, onSetDate, onOpenDashboard }) {
    const [input, setInput] = useState("");
    const [history, setHistory] = useState([]);
    const [loading, setLoading] = useState(false);
    const scrollRef = useRef(null);
    const API = "http://localhost:8000";

    function toEndOfMonth(ym) {
        if (!ym) return null;
        let [y, m] = ym.split("-");
        return `${y}-${m.padStart(2, "0")}`;
    }

    const listRef = useRef(null);

    useEffect(() => {
        // 메시지 이력이 추가되거나 로딩 상태가 바뀔 때 브라우저 렌더링 후 스크롤
        const timer = setTimeout(() => {
            if (listRef.current) {
                listRef.current.scrollTop = listRef.current.scrollHeight;
            }
        }, 10);
        return () => clearTimeout(timer);
    }, [history, loading]);

    const handleSubmit = async (e) => {
        if (e) e.preventDefault();
        if (!input.trim() || loading) return;

        const msg = input.trim();
        setInput("");
        setHistory(prev => [...prev, { role: "user", text: msg }]);
        setLoading(true);

        try {
            const res = await axios.post(`${API}/api/chat`, {
                message: msg,
                bank,
                base_date: toEndOfMonth(baseDate)
            }, { timeout: 120000 });

            setHistory(prev => [...prev, { role: "assistant", text: res.data.answer }]);

            if (res.data.actions) {
                res.data.actions.forEach(act => {
                    if (act.action === "set_date" && onSetDate) {
                        onSetDate(act.value);
                        if (onOpenDashboard) onOpenDashboard();
                    }
                    if (act.action === "open_dashboard" && onOpenDashboard) {
                        onOpenDashboard();
                    }
                });
            }
        } catch {
            setHistory(prev => [...prev, { role: "assistant", text: "오류가 발생했습니다." }]);
        } finally {
            setLoading(false);
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
                        <div className="bg-slate-100 rounded-xl px-3 py-2 text-[13px] text-slate-400 flex items-center gap-1">
                            <span className="animate-bounce" style={{ animationDelay: "0s" }}>·</span>
                            <span className="animate-bounce" style={{ animationDelay: "0.15s" }}>·</span>
                            <span className="animate-bounce" style={{ animationDelay: "0.3s" }}>·</span>
                            <span className="ml-1">분석 중</span>
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

/* ─── 로그인 ─── */
function LoginPage({ onLogin }) {
    const banks = ["광주은행", "전북은행"];
    const [selBank, setSelBank] = useState("광주은행");
    const [pw, setPw] = useState("");
    const [isBankOpen, setIsBankOpen] = useState(false);

    const handleLogin = (e) => {
        e.preventDefault();
        if (pw === "1234") {
            onLogin(selBank);
        } else {
            alert("비밀번호가 올바르지 않습니다.");
        }
    };

    return (
        <div className="min-h-screen bg-gradient-to-br from-[#f0f4fa] to-[#dce8f8] flex items-center justify-center px-6">
            <div className="w-full max-w-[420px] bg-white/70 backdrop-blur-xl rounded-[32px] border border-white p-10 shadow-2xl relative">
                <div className="mb-10 text-center relative z-10">
                    <h1 className="text-3xl font-black tracking-[-0.05em] text-[#0f1d33]">LTV 적정성 검증 Agent</h1>
                </div>

                <form onSubmit={handleLogin} className="space-y-6 relative z-10">
                    {/* 커스텀 은행 선택 드롭다운 */}
                    <div className="space-y-2 relative">
                        <label className="text-[13px] font-bold text-slate-400 ml-1">접속 은행 선택</label>
                        <div className="relative">
                            <button
                                type="button"
                                onClick={() => setIsBankOpen(!isBankOpen)}
                                className={`w-full h-14 flex items-center justify-between px-5 rounded-2xl border transition-all duration-200 ${isBankOpen ? "bg-white border-blue-400 ring-4 ring-blue-50" : "bg-white/60 border-slate-200 hover:border-slate-300"}`}
                            >
                                <span className="text-lg font-bold text-[#1b2a40]">{selBank}</span>
                                <svg className={`w-5 h-5 text-slate-400 transition-transform duration-300 ${isBankOpen ? "rotate-180" : ""}`} fill="none" stroke="currentColor" viewBox="0 0 24 24">
                                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2.5" d="M19 9l-7 7-7-7" />
                                </svg>
                            </button>

                            {isBankOpen && (
                                <div className="absolute mt-2 w-full bg-white border border-slate-100 rounded-2xl shadow-xl z-20 py-1">
                                    {banks.map((b) => (
                                        <button
                                            key={b}
                                            type="button"
                                            onClick={() => { setSelBank(b); setIsBankOpen(false); }}
                                            className="w-full text-left px-5 py-3 text-[16px] font-bold text-slate-700 hover:bg-slate-50 hover:text-blue-600 transition-colors"
                                        >
                                            {b}
                                        </button>
                                    ))}
                                </div>
                            )}
                        </div>
                    </div>

                    <div className="space-y-2">
                        <label className="text-[13px] font-bold text-slate-400 ml-1">비밀번호 입력</label>
                        <input
                            type="password"
                            placeholder="••••••••"
                            value={pw}
                            onChange={(e) => setPw(e.target.value)}
                            className="w-full h-14 bg-white/60 border border-slate-200 rounded-2xl px-5 text-lg font-bold text-[#1b2a40] outline-none focus:border-blue-400 focus:ring-4 focus:ring-blue-50 transition-all shadow-sm"
                        />
                    </div>

                    <button
                        type="submit"
                        className="w-full h-14 bg-blue-600 text-white rounded-2xl text-lg font-black shadow-lg shadow-blue-100 hover:bg-blue-700 hover:scale-[1.02] active:scale-[0.98] transition-all duration-300 mt-2"
                    >
                        로그인
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
                <div className="text-center">권고안</div>
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
