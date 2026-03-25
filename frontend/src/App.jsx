import React, { useState, useEffect } from "react";
import axios from "axios";
import { Info, TrendingDown, TrendingUp } from "lucide-react";

function App() {
  const [urgentList, setUrgentList] = useState([]);
  const [matrixData, setMatrixData] = useState([]);
  const [loading, setLoading] = useState(true);

  // Filters State
  const [regionFilter, setRegionFilter] = useState("전체 지역");
  const [categoryFilter, setCategoryFilter] = useState("전체");
  const [usageFilter, setUsageFilter] = useState("전체");

  useEffect(() => {
    Promise.all([
      axios.get("http://localhost:8000/api/urgent-list"),
      axios.get("http://localhost:8000/api/matrix"),
    ])
      .then(([resUrgent, resMatrix]) => {
        setUrgentList(resUrgent.data);
        setMatrixData(resMatrix.data);
        setLoading(false);
      })
      .catch((err) => {
        console.error(err);
        setLoading(false);
      });
  }, []);

  // 파생 데이터 (요약 텍스트 생성을 위해)
  const redCnt = urgentList.filter((d) => d.signal?.tone === "red").length;
  const yellowCnt = urgentList.filter((d) => d.signal?.tone === "yellow").length;
  const downwardCnt = urgentList.filter((d) => d.relaxed_delta < 0).length;
  const upwardCnt = urgentList.filter((d) => d.relaxed_delta > 0).length;

  const usageCounts = {};
  urgentList.forEach((d) => {
    const key = `${d.reg} ${d.usage_type}`;
    if (d.reg && d.usage_type) {
        usageCounts[key] = (usageCounts[key] || 0) + 1;
    }
  });
  const topRegionUsages = Object.keys(usageCounts).slice(0, 3).join(", ") || "-";

  // 필터링 적용된 매트릭스 데이터
  const filteredMatrix = matrixData.filter((row) => {
    if (regionFilter !== "전체 지역" && row.지역 !== regionFilter) return false;
    if (categoryFilter !== "전체" && row.대분류 !== categoryFilter) return false;
    if (usageFilter !== "전체" && row.용도 !== usageFilter) return false;
    return true;
  });

  // 셀 색상 매핑 함수 (Streamlit과 동일 스타일링)
  const getCellClassName = (val) => {
    if (val === "red") return "text-red-600 font-bold bg-red-50";
    if (val === "yellow") return "text-yellow-600 font-bold bg-yellow-50";
    if (val === "green") return "text-green-600 font-bold";
    if (val === "gray") return "text-gray-400";
    return "";
  };
  const getCellLabel = (val) => {
    if (val === "red") return "부적정 (위험)";
    if (val === "yellow") return "주의 (관찰요망)";
    if (val === "green") return "적정 (안정)";
    if (val === "gray") return "모수 부족";
    return val;
  };

  return (
    <div className="min-h-screen bg-white text-gray-900 font-sans p-6 sm:p-10">
      <h1 className="text-3xl font-extrabold mb-8 pb-4 border-b border-gray-200">
        LTV 적정성 분석 대시보드
      </h1>

      {loading ? (
        <div className="flex justify-center items-center h-40">
          <div className="w-8 h-8 border-4 border-blue-500 border-t-transparent rounded-full animate-spin"></div>
          <span className="ml-3 font-bold text-gray-500">데이터 로딩 중...</span>
        </div>
      ) : (
        <div className="space-y-12">
          {/* 이번달 요약 Section */}
          <div className="bg-white border rounded-lg shadow-sm overflow-hidden">
            <div className="bg-gray-100 border-b px-4 py-2 font-bold text-gray-700 text-sm">
              이번달 결과 요약
            </div>
            <div className="p-6 text-gray-700 leading-relaxed">
              이번 달 분석 결과, 총 <b>{urgentList.length}건</b>의 LTV 점검 및 조정 대상이 확인되었습니다. (
              {redCnt > 0 && yellowCnt > 0 ? (
                <span>
                  <span className="text-red-600 font-extrabold">즉시 조정 {redCnt}건</span>과{" "}
                  <span className="text-orange-600 font-extrabold">검토 필요 {yellowCnt}건</span>
                </span>
              ) : redCnt > 0 ? (
                <span className="text-red-600 font-extrabold">즉시 조정 {redCnt}건</span>
              ) : (
                <span className="text-orange-600 font-extrabold">검토 필요 {yellowCnt}건</span>
              )}
              )<br />
              주요 변동 지역은{" "}
              <span className="bg-[#fde047] font-extrabold px-1.5 py-0.5 rounded text-gray-900">
                {topRegionUsages}
              </span>{" "}
              등이며, 시장 흐름에 따라{" "}
              {downwardCnt > 0 || upwardCnt > 0 ? (
                <>
                  {downwardCnt > 0 && (
                    <span className="text-red-600 font-extrabold">하향 조정({downwardCnt}건)</span>
                  )}
                  {downwardCnt > 0 && upwardCnt > 0 && "과 "}
                  {upwardCnt > 0 && (
                    <span className="text-green-600 font-extrabold">상향 시그널({upwardCnt}건)</span>
                  )}
                  {" "}위주로 권고안이 제시되었습니다.
                </>
              ) : (
                "상·하향 방향성이 고르게 섞여 있으므로 개별 상세 검토가 필요합니다."
              )}
            </div>
          </div>

          {/* 긴급 대상 표 */}
          <div className="space-y-3">
            <div className="flex items-center gap-2">
              <h2 className="text-lg font-bold text-gray-900 m-0">
                🔔 지금 당장 조정이 필요한 건물
              </h2>
              <div className="group relative cursor-pointer text-gray-500 hover:text-gray-800">
                <Info className="w-5 h-5" />
                <div className="absolute left-1/2 -translate-x-1/2 top-8 w-80 bg-white border shadow-lg text-sm rounded p-4 opacity-0 invisible group-hover:opacity-100 group-hover:visible transition-all z-50">
                  <div className="text-xs text-gray-600 mb-3 border-b pb-2">
                    <b>💡 기본 분석 기준</b><br />
                    • 최소 모수: 최근 3개월 누적 매각건수 5건 이상<br />
                    • 이상치 제거: 기존 LTV 대비 위아래 30% 초과 값은 평균 계산 제외
                  </div>
                  <div className="mb-2">
                    <b className="text-red-600">🔴 레드 시그널</b>
                    <br />
                    <span className="text-xs text-gray-500">3/6/12M 낙찰값이 LTV와 10%p 이상 차이, 추세 뚜렷</span>
                  </div>
                  <div>
                    <b className="text-yellow-600">🟡 옐로우 시그널</b>
                    <br />
                    <span className="text-xs text-gray-500">3/6/12M 낙찰값이 LTV와 5~9%p 차이, 방향성 존재</span>
                  </div>
                </div>
              </div>
            </div>

            <div className="border border-gray-200 rounded overflow-hidden">
              <table className="w-full text-sm text-left whitespace-nowrap">
                <thead className="bg-gray-100 text-gray-700 uppercase font-bold border-b">
                  <tr>
                    <th className="px-4 py-3">지역</th>
                    <th className="px-4 py-3">용도</th>
                    <th className="px-4 py-3">기존 LTV</th>
                    <th className="px-4 py-3">시그널</th>
                    <th className="px-4 py-3 text-right">3M</th>
                    <th className="px-4 py-3 text-right">6M</th>
                    <th className="px-4 py-3 text-right">12M</th>
                    <th className="px-4 py-3 text-center">조치 방향</th>
                    <th className="px-4 py-3">AI 조정 권고안</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-gray-200 bg-white">
                  {urgentList.length === 0 && (
                    <tr>
                      <td colSpan="9" className="px-4 py-8 text-center text-gray-500">
                        긴급 조정 대상이 없습니다.
                      </td>
                    </tr>
                  )}
                  {urgentList.map((item, idx) => {
                    const isRed = item.signal?.tone === "red";
                    const isDown = item.signal?.direction === "▼";
                    return (
                      <tr key={idx} className="hover:bg-gray-50">
                        <td className="px-4 py-3 font-bold">
                          {item.reg}
                          <div className="text-[11px] text-gray-400 font-normal">{item.category}</div>
                        </td>
                        <td className="px-4 py-3 text-gray-700 font-semibold">{item.usage_type}</td>
                        <td className="px-4 py-3 text-center">
                          <span className="bg-gray-100 px-2 py-1 rounded-lg border font-bold text-gray-700">{item.ltv_val}%</span>
                        </td>
                        <td className="px-4 py-3 text-center">
                          <span className={`px-2 py-1 rounded-full text-xs font-bold border ${isRed ? 'bg-red-50 text-red-700 border-red-200' : 'bg-yellow-50 text-yellow-700 border-yellow-200'}`}>
                            {isRed ? '🔴 레드' : '🟡 옐로우'}
                          </span>
                        </td>
                        <td className="px-4 py-3 text-right">{item.met?.avg?.[3]?.toFixed(1) || "-"}% <span className="text-[10px] text-gray-400">({item.met?.count?.[3] || 0}건)</span></td>
                        <td className="px-4 py-3 text-right">{item.met?.avg?.[6]?.toFixed(1) || "-"}% <span className="text-[10px] text-gray-400">({item.met?.count?.[6] || 0}건)</span></td>
                        <td className="px-4 py-3 text-right">{item.met?.avg?.[12]?.toFixed(1) || "-"}% <span className="text-[10px] text-gray-400">({item.met?.count?.[12] || 0}건)</span></td>
                        <td className="px-4 py-3 text-center">
                          {isDown ? (
                            <span className="text-red-500 font-bold flex items-center justify-center gap-1"><TrendingDown className="w-4 h-4"/> 하향</span>
                          ) : (
                            <span className="text-green-500 font-bold flex items-center justify-center gap-1"><TrendingUp className="w-4 h-4"/> 상향</span>
                          )}
                        </td>
                        <td className="px-4 py-3 min-w-[300px] whitespace-normal text-xs text-gray-600">
                          <b className="text-gray-800 text-sm">적정 LTV: {item.relaxed_ltv}% ({item.relaxed_delta > 0 ? '+' : ''}{item.relaxed_delta}%p 조정)</b><br/>
                          <span dangerouslySetInnerHTML={{ __html: item.reason }}></span>
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          </div>

          <hr className="my-8" />

          {/* 메인 매트릭스 표 */}
          <div className="space-y-4">
            <h2 className="text-lg font-bold text-gray-900 m-0 border-l-4 border-blue-600 pl-2">
              기간별 적정성 요약
            </h2>

            {/* Filters Row */}
            <div className="flex gap-4 p-4 bg-gray-50 border rounded text-sm font-semibold">
              <label className="flex items-center gap-2">
                지역:
                <select className="border p-1 rounded" value={regionFilter} onChange={e => setRegionFilter(e.target.value)}>
                  <option>전체 지역</option>
                  {[...new Set(matrixData.map(d => d.지역))].map(r => <option key={r}>{r}</option>)}
                </select>
              </label>
              <label className="flex items-center gap-2">
                용도:
                <select className="border p-1 rounded" value={usageFilter} onChange={e => setUsageFilter(e.target.value)}>
                  <option>전체</option>
                  {[...new Set(matrixData.map(d => d.용도))].map(u => <option key={u}>{u}</option>)}
                </select>
              </label>
            </div>

            <div className="border border-gray-200 rounded overflow-hidden">
              <table className="w-full text-sm text-center whitespace-nowrap">
                <thead className="bg-gray-100 text-gray-700 font-bold border-b">
                  <tr>
                    <th className="px-4 py-3 text-left">지역</th>
                    <th className="px-4 py-3 text-left">대분류</th>
                    <th className="px-4 py-3 text-left">용도</th>
                    <th className="px-4 py-3">기존 LTV</th>
                    <th className="px-4 py-3">최근 3개월</th>
                    <th className="px-4 py-3">최근 6개월</th>
                    <th className="px-4 py-3">최근 12개월</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-gray-200 bg-white">
                  {filteredMatrix.map((row, idx) => (
                    <tr key={idx} className="hover:bg-gray-50">
                      <td className="px-4 py-2 font-bold text-left">{row.지역}</td>
                      <td className="px-4 py-2 text-left text-gray-500">{row.대분류}</td>
                      <td className="px-4 py-2 text-left text-gray-700">{row.용도}</td>
                      <td className="px-4 py-2 font-semibold bg-gray-50">{row.LTV}%</td>
                      <td className={`px-4 py-2 ${getCellClassName(row['3개월'])}`}>
                        {getCellLabel(row['3개월'])} <div className="text-[10px] text-gray-400 font-normal">({row['3개월_count']}건)</div>
                      </td>
                      <td className={`px-4 py-2 ${getCellClassName(row['6개월'])}`}>
                        {getCellLabel(row['6개월'])} <div className="text-[10px] text-gray-400 font-normal">({row['6개월_count']}건)</div>
                      </td>
                      <td className={`px-4 py-2 ${getCellClassName(row['12개월'])}`}>
                        {getCellLabel(row['12개월'])} <div className="text-[10px] text-gray-400 font-normal">({row['12개월_count']}건)</div>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

export default App;
