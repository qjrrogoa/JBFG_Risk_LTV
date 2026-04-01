import { useState } from "react";
import axios from "axios";

const API = "http://localhost:8000";

export default function LoginPage({ onLogin }) {
  const [bank, setBank] = useState("광주은행");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);

  async function handleSubmit(e) {
    e.preventDefault();
    setError("");
    setLoading(true);
    try {
      await axios.post(`${API}/api/auth/login`, { bank, password });
      onLogin(bank);
    } catch (err) {
      setError(err.response?.data?.detail || "로그인에 실패했습니다.");
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="min-h-screen flex items-center justify-center" style={{ background: "linear-gradient(135deg, #0f172a 0%, #1e293b 60%, #0f172a 100%)" }}>
      {/* 배경 그리드 패턴 */}
      <div className="absolute inset-0 opacity-5" style={{
        backgroundImage: "linear-gradient(#94a3b8 1px, transparent 1px), linear-gradient(90deg, #94a3b8 1px, transparent 1px)",
        backgroundSize: "40px 40px"
      }} />

      <div className="relative w-full max-w-sm mx-4">
        {/* 상단 로고/타이틀 */}
        <div className="text-center mb-8">
          <div className="inline-flex items-center justify-center w-14 h-14 rounded-2xl bg-blue-600 mb-5 shadow-lg shadow-blue-900/50">
            <svg className="w-7 h-7 text-white" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 19v-6a2 2 0 00-2-2H5a2 2 0 00-2 2v6a2 2 0 002 2h2a2 2 0 002-2zm0 0V9a2 2 0 012-2h2a2 2 0 012 2v10m-6 0a2 2 0 002 2h2a2 2 0 002-2m0 0V5a2 2 0 012-2h2a2 2 0 012 2v14a2 2 0 01-2 2h-2a2 2 0 01-2-2z" />
            </svg>
          </div>
          <h1 className="text-2xl font-black text-white tracking-tight">LTV 적정성 대시보드</h1>
          <p className="text-sm text-slate-400 mt-1.5">지역·용도별 낙찰가율 흐름 분석 시스템</p>
        </div>

        {/* 로그인 카드 */}
        <form onSubmit={handleSubmit} className="bg-slate-800/60 backdrop-blur border border-slate-700/50 rounded-2xl p-7 shadow-2xl space-y-4">
          <div>
            <label className="block text-xs font-semibold text-slate-400 uppercase tracking-wider mb-2">
              은행
            </label>
            <div className="grid grid-cols-2 gap-2">
              {["광주은행", "전북은행"].map((b) => (
                <button
                  key={b}
                  type="button"
                  onClick={() => setBank(b)}
                  className={`py-2.5 rounded-lg text-sm font-semibold transition-all ${
                    bank === b
                      ? "bg-blue-600 text-white shadow-lg shadow-blue-900/40"
                      : "bg-slate-700/50 text-slate-400 hover:text-white hover:bg-slate-700"
                  }`}
                >
                  {b}
                </button>
              ))}
            </div>
          </div>

          <div>
            <label className="block text-xs font-semibold text-slate-400 uppercase tracking-wider mb-2">
              비밀번호
            </label>
            <input
              type="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              placeholder="비밀번호 입력"
              className="w-full bg-slate-700/50 border border-slate-600/50 text-white placeholder-slate-500 rounded-lg px-4 py-2.5 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-transparent"
            />
          </div>

          {error && (
            <div className="flex items-center gap-2 text-red-400 bg-red-900/20 border border-red-800/30 rounded-lg px-3 py-2 text-sm">
              <svg className="w-4 h-4 shrink-0" fill="currentColor" viewBox="0 0 20 20">
                <path fillRule="evenodd" d="M18 10a8 8 0 11-16 0 8 8 0 0116 0zm-7 4a1 1 0 11-2 0 1 1 0 012 0zm-1-9a1 1 0 00-1 1v4a1 1 0 102 0V6a1 1 0 00-1-1z" clipRule="evenodd" />
              </svg>
              {error}
            </div>
          )}

          <button
            type="submit"
            disabled={loading}
            className="w-full bg-blue-600 hover:bg-blue-500 disabled:opacity-50 text-white font-bold py-3 rounded-lg text-sm transition-colors shadow-lg shadow-blue-900/30 mt-2"
          >
            {loading ? "접속 중..." : "접속하기"}
          </button>
        </form>

        <p className="text-center text-slate-600 text-xs mt-6">
          JB금융지주 리스크관리부 내부 시스템
        </p>
      </div>
    </div>
  );
}
