"""
LTV 적정성 챗봇 에이전트 (LangChain 기반)
==========================================

[설계 원칙]
- LLM이 직접 데이터를 계산하지 않음
- pandas가 실제 조회/집계를 수행 → LLM은 질문 해석 + 결과 설명만 담당
- 안전한 조회 함수들을 @tool로 등록하여 에이전트에 제공

[흐름]
  사용자 질문 → LLM이 질문 해석 → 적절한 tool 호출 → pandas 조회 → 결과 반환 → LLM이 한국어로 설명
"""

import os
import sys
import json
import pandas as pd
import time
from datetime import datetime
from typing import Optional
try:
    from duckduckgo_search import DDGS
except ImportError:
    DDGS = None

# ─── LangChain 핵심 임포트 ───
# langchain_core.tools: LLM이 호출할 수 있는 "도구(tool)"를 정의하는 데코레이터
from langchain_core.tools import tool
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder

# langchain_openai: OpenAI LLM을 LangChain에서 사용하기 위한 래퍼
from langchain_openai import ChatOpenAI

# langgraph: 최신 LangChain의 에이전트 실행 프레임워크
# create_react_agent: "ReAct" 패턴 에이전트를 생성
#   - ReAct = Reasoning + Acting
#   - LLM이 "생각 → 도구 호출 → 결과로 다시 생각 → 최종 답변" 루프를 자동으로 수행
from langgraph.prebuilt import create_react_agent

# ─── 프로젝트 내부 모듈 임포트 ───
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

# llm_advisor.py에서 API 키와 모델 설정만 가져옴
from llm_advisor import OPENAI_API_KEY, DEFAULT_MODEL

# services.py에서 기존 데이터 로딩/집계 함수 재사용
import services


# ==========================================
# 1단계: pandas 조회 함수 정의 (@tool)
# ==========================================
# @tool 데코레이터를 사용하면 일반 Python 함수가 LangChain의 "도구"가 됩니다.
# LLM은 함수의 이름, docstring, 파라미터 타입을 보고 어떤 도구를 호출할지 결정합니다.
# 따라서 docstring을 명확하게 작성하는 것이 매우 중요합니다.


@tool
def get_urgent_signals(bank: str, base_date: Optional[str] = None) -> str:
    """현재 LTV 조정이 필요한 긴급 대상 목록을 조회합니다.
    조정대상(red)과 검토대상(yellow)으로 분류된 지역/담보 유형 리스트를 반환합니다.

    Args:
        bank: 은행명 (예: "광주은행", "전북은행")
        base_date: 기준일 (예: "2026-02-28"). None이면 최신 데이터 사용.
    """
    try:
        # services.get_aggregated_data 대신 fetch_all_advice를 직접 호출하여 리포트(reason)까지 포함
        _, raw_urgent_list = services.get_aggregated_data(bank, base_date)
        urgent_list = services.fetch_all_advice(raw_urgent_list, bank, base_date)

        if not urgent_list:
            return json.dumps({"message": "현재 긴급 조정 대상이 없습니다.", "count": 0}, ensure_ascii=False)

        results = []
        for item in urgent_list:
            results.append({
                "지역": item["region"],
                "대분류": item["category"],
                "담보유형": item["usage"],
                "현재LTV": item["current_ltv"],
                "권고_보수": item["conservative_ltv"],
                "권고_완화": item["relaxed_ltv"],
                "판정": "조정필요" if item["tone"] == "red" else "검토필요",
                "방향": item["direction"],
                "산출사유_리포트": item["reason"].replace("<br>", "\n") if item.get("reason") else "사유 없음",
            })

        return json.dumps({
            "count": len(results),
            "items": results
        }, ensure_ascii=False)

    except Exception as e:
        return json.dumps({"error": str(e)}, ensure_ascii=False)


@tool
def get_region_detail(bank: str, region: str, base_date: Optional[str] = None) -> str:
    """특정 지역의 담보 유형별 LTV 현황과 매각 통계를 조회합니다.
    지역별로 어떤 담보 유형이 조정 대상인지, 각 기간별 평균 낙찰가율은 얼마인지 확인할 수 있습니다.

    Args:
        bank: 은행명 (예: "광주은행", "전북은행")
        region: 지역명 (예: "서울", "경기", "광주", "전남")
        base_date: 기준일 (예: "2026-02-28"). None이면 최신 데이터 사용.
    """
    try:
        matrix_df, urgent_list = services.get_aggregated_data(bank, base_date)

        # 매트릭스에서 해당 지역 필터링
        region_data = matrix_df[matrix_df["지역"] == region]

        if region_data.empty:
            return json.dumps({"message": f"'{region}' 지역의 데이터가 없습니다."}, ensure_ascii=False)

        results = []
        for _, row in region_data.iterrows():
            item = {
                "담보유형": row["용도"],
                "대분류": row["대분류"],
                "현재LTV": row["LTV"],
                "신호": row.get("signal_tone", "정상"),
            }
            # 기간별 판정 결과 추가
            for period in ["3개월", "6개월", "12개월", "3년", "5년"]:
                item[period] = row.get(period, "gray")
                item[f"{period}_건수"] = row.get(f"{period}_count", 0)
            results.append(item)

        # 긴급 대상 중 이 지역 것만 추출
        urgent_in_region = [u for u in urgent_list if u["reg"] == region]

        return json.dumps({
            "지역": region,
            "전체항목수": len(results),
            "긴급대상수": len(urgent_in_region),
            "items": results
        }, ensure_ascii=False)

    except Exception as e:
        return json.dumps({"error": str(e)}, ensure_ascii=False)


@tool
def get_auction_stats(bank: str, region: str, usage: str, base_date: Optional[str] = None) -> str:
    """특정 지역/담보유형의 기간별 낙찰가율 통계를 조회합니다.
    3개월, 6개월, 12개월, 3년, 5년 단위의 평균 낙찰가율과 건수를 반환합니다.

    Args:
        bank: 은행명 (예: "광주은행", "전북은행")
        region: 지역명 (예: "서울", "경기")
        usage: 담보유형 (예: "아파트", "다세대주택", "오피스텔")
        base_date: 기준일 (예: "2026-02-28"). None이면 최신 데이터 사용.
    """
    try:
        # 가벼운 지역별 로드 사용
        winning_df = services.get_processed_region_df(bank, region)
        if winning_df.empty:
            return json.dumps({"message": f"'{region}' 지역 데이터가 없습니다."}, ensure_ascii=False)

        selected_dt = pd.to_datetime(base_date) if base_date else winning_df["매각일"].max()
        reg_df = winning_df[winning_df["매각일"] <= selected_dt]

        # LTV 값 찾기
        target_df = reg_df[reg_df["분석용도"] == usage]
        ltv_val = float(target_df["적용LTV"].iloc[-1]) if not target_df.empty and "적용LTV" in target_df.columns else 80.0

        # 기간별 통계 계산
        met = services.calculate_metrics(reg_df, usage, ltv_val, selected_dt)

        result = {
            "지역": region, "담보유형": usage, "현재LTV": ltv_val, "통계": {}
        }
        period_labels = {3: "3개월", 6: "6개월", 12: "12개월", 36: "3년", 60: "5년"}
        for m, label in period_labels.items():
            avg = met["avg"].get(m)
            result["통계"][label] = {
                "평균낙찰가율": round(avg, 2) if avg is not None else None,
                "건수": met["count"].get(m, 0),
                "LTV대비차이": round(avg - ltv_val, 2) if avg is not None else None,
            }

        del winning_df; services.clear_memory() # 메모리 해제
        return json.dumps(result, ensure_ascii=False)

    except Exception as e:
        return json.dumps({"error": str(e)}, ensure_ascii=False)


@tool
def get_ltv_standards(bank: str) -> str:
    """은행의 현재 LTV 기준표를 조회합니다.
    각 담보유형별/지역별 적용 LTV 비율을 확인할 수 있습니다.

    Args:
        bank: 은행명 (예: "광주은행", "전북은행")
    """
    try:
        ltv_std = services.load_ltv_standards(bank)
        if ltv_std is None:
            return json.dumps({"message": f"'{bank}'의 LTV 기준표를 찾을 수 없습니다."}, ensure_ascii=False)

        # 사이즈가 클 수 있으므로 요약 형태로 반환
        records = ltv_std.to_dict(orient="records")
        return json.dumps({
            "은행": bank,
            "항목수": len(records),
            "기준표": records
        }, ensure_ascii=False)

    except Exception as e:
        return json.dumps({"error": str(e)}, ensure_ascii=False)


@tool
def get_available_regions(bank: str) -> str:
    """조회 가능한 지역 목록을 반환합니다.

    Args:
        bank: 은행명 (예: "광주은행", "전북은행")
    """
    try:
        # 전체 로딩 없이 설정된 지역 상수로 응답
        return json.dumps({"은행": bank, "지역목록": services.REGIONS_ALL}, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"error": str(e)}, ensure_ascii=False)


@tool
def get_available_usages(bank: str, region: Optional[str] = None) -> str:
    """조회 가능한 담보유형(용도) 목록을 반환합니다.

    Args:
        bank: 은행명 (예: "광주은행", "전북은행")
        region: 특정 지역으로 필터링할 경우 지역명. None이면 전체.
    """
    try:
        # 전체 데이터 로딩 대신 기준표에서 용도 목록만 추출
        ltv_std = services.load_ltv_standards(bank)
        if ltv_std is None:
            return json.dumps({"message": f"'{bank}'의 LTV 기준표를 찾을 수 없습니다."}, ensure_ascii=False)
        
        usages = sorted(ltv_std["담보종류"].dropna().unique().tolist())
        return json.dumps({"은행": bank, "지역": region or "전체", "담보유형목록": usages}, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"error": str(e)}, ensure_ascii=False)


# ── UI 액션 도구 ──
# 이 도구는 데이터를 조회하는 것이 아니라, 프론트엔드 대시보드의 화면을 변경하는 명령을 생성합니다.
# 반환값에 "__UI_ACTION__" 마커가 포함되면 chat() 함수가 이를 감지하여 actions 배열로 분리합니다.

@tool
def navigate_dashboard(target_date: str) -> str:
    """사용자가 특정 시점의 대시보드 화면을 보고 싶어할 때 호출합니다.
    대시보드의 분석 기준일을 변경하여 해당 시점의 조정/검토 결과를 화면에 표시합니다.
    예: "2025년 9월 결과 보여줘", "작년 데이터로 바꿔줘", "2024년 12월 기준으로 봐줘"

    Args:
        target_date: 변경할 기준 연월 (YYYY-MM 형식, 예: "2025-09")
    """
    # 날짜 유효성 검증
    try:
        dt = datetime.strptime(target_date, "%Y-%m")
        formatted = dt.strftime("%Y-%m")
        return json.dumps({
            "__UI_ACTION__": True,
            "action": "set_date",
            "value": formatted,
            "message": f"대시보드를 {dt.year}년 {dt.month}월 기준으로 전환합니다."
        }, ensure_ascii=False)
    except ValueError:
        return json.dumps({"error": f"'{target_date}'는 올바른 날짜 형식이 아닙니다. YYYY-MM 형식으로 입력해주세요."}, ensure_ascii=False)


@tool
def open_dashboard() -> str:
    """사용자가 전체 대시보드 화면, 전체 데이터 표, 테이블, 혹은 분석 결과를 시각적으로 확실히 "보여줘"라고 할 때 호출합니다.
    이 도구는 현재 전체화면인 챗봇을 사이드로 밀어내고, 메인 데이터 대시보드(LTV 적정성 검증 데이터 화면)를 열어주는 역할을 합니다.
    """
    return json.dumps({
        "__UI_ACTION__": True,
        "action": "open_dashboard",
        "message": "요청에 따라 실시간 LTV 데이터 대시보드 화면을 엽니다."
    }, ensure_ascii=False)
@tool
def search_market_news(query: str, region: Optional[str] = None) -> str:
    """부동산 시장 최신 뉴스, 금리 동향, 정책 기사 등을 OpenAI 내장 웹 검색을 통해 실시간으로 검색합니다.
    조정대상 분석 시 시장 흐름, 영향 요인, 향후 전망을 파악하기 위해 활용합니다.

    Args:
        query: 검색어 (예: "부동산 낙찰가율 하락 원인", "평택 지식산업센터 고점론", "상가 시장 리스크 전망")
        region: 지역명 (예: "서울", "경기")
    """
    try:
        from openai import OpenAI
        import llm_advisor

        current_model = llm_advisor.DEFAULT_MODEL
        api_key = llm_advisor.OPENAI_API_KEY
        search_target = f"{region} {query}" if region else query
        
        # OpenAI 내장 검색 전용 (DuckDuckGo 제거됨)
        client = OpenAI(api_key=api_key)
        response = client.responses.create(
            model=current_model,
            input=f"부동산 리스크 관리 전문가로서 다음 주제에 대해 최신 뉴스를 검색하고 리스크 관점의 요약을 제공해라: {search_target}",
            temperature=1,
            tools=[{"type": "web_search"}],
        )
        if hasattr(response, 'output_text'):
            return str(response.output_text)
        return str(response)

    except Exception as e:
        import traceback
        err_detail = traceback.format_exc()
        print(f"[Search Tool Error] {err_detail}")
        return f"인터넷 검색 중 오류가 발생했습니다: {str(e)}"


@tool
def compare_regions(bank: str, region1: str, region2: str, usage: str, base_date: Optional[str] = None) -> str:
    """두 지역의 동일 담보유형에 대한 낙찰가율 통계를 비교합니다.

    Args:
        bank: 은행명 (예: "광주은행")
        region1: 첫 번째 지역명 (예: "서울")
        region2: 두 번째 지역명 (예: "경기")
        usage: 담보유형 (예: "아파트")
        base_date: 기준일. None이면 최신 데이터 사용.
    """
    try:
        results = {}
        for region in [region1, region2]:
            reg_df = services.get_processed_region_df(bank, region)
            if reg_df.empty:
                results[region] = "데이터 없음"
                continue

            selected_dt = pd.to_datetime(base_date) if base_date else reg_df["매각일"].max()
            reg_df = reg_df[reg_df["매각일"] <= selected_dt]
            target_df = reg_df[reg_df["분석용도"] == usage]
            
            ltv_val = float(target_df["적용LTV"].iloc[-1]) if not target_df.empty and "적용LTV" in target_df.columns else 80.0
            met = services.calculate_metrics(reg_df, usage, ltv_val, selected_dt)

            results[region] = {
                "현재LTV": ltv_val,
                "3개월_평균": round(met["avg"][3], 2) if met["avg"][3] else None,
                "6개월_평균": round(met["avg"][6], 2) if met["avg"][6] else None,
                "12개월_평균": round(met["avg"][12], 2) if met["avg"][12] else None,
                "3개월_건수": met["count"][3],
                "6개월_건수": met["count"][6],
                "12개월_건수": met["count"][12],
            }
            del reg_df; services.clear_memory()

        return json.dumps({"비교결과": results, "담보유형": usage}, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"error": str(e)}, ensure_ascii=False)


# ==========================================
# 2단계: 에이전트 생성 함수
# ==========================================

# 시스템 프롬프트: 에이전트의 "역할"과 "규칙"을 정의합니다.
# 이 프롬프트가 LLM이 도구를 어떻게 활용할지를 결정합니다.
SYSTEM_PROMPT = """
너는 LTV 적정성 분석 전문 AI 도우미다.
은행의 부동산 담보 LTV(Loan-to-Value) 리스크를 분석하고 설명하는 것이 너의 역할이다.

[핵심 규칙]
1. 숫자는 반드시 tool 호출 결과만 사용한다. 절대 추측하지 않는다.
2. 데이터가 필요한 질문이면 반드시 적절한 tool을 먼저 호출한다.
3. 사용자가 특정 시점의 대시보드나 상세 내용을 "보여줘", "보고싶어", "바꿔줘"라고 요청하면 반드시 navigate_dashboard 도구를 호출한다.
   - [중요] navigate_dashboard를 호출한 경우에는 **절대로 화면 내용을 요약하거나 설명하지 않는다.**
   - 답변은 **무조건 한 문장**으로 제한한다. (예: "네, 2025년 4월 기준 데이터로 화면을 전환합니다.")
   - 추가적인 질문 제안이나 서비스 안내를 덧붙이지 않는다.
4. 결과가 많으면 핵심 항목 위주로 요약하고, 전체 건수도 함께 알려준다.
5. 화면 전환 요청이 아닌 분석 요청(예: "서울 아파트 낙찰가율 어때?", "조정대상 요약해줘")에 대해서만 상세한 데이터 분석과 설명을 수행한다.

[답변 스타일]
- 한국어로 답변한다.
- 금융 전문가 수준의 정확한 용어를 사용한다.
- 불필요한 서술은 줄이고, 핵심 정보를 구조화하여(번호나 표 등) 간결하게 답변한다.

[조정대상 심층 분석 및 시장 상황 규칙]
- 사용자가 "시장 흐름", "동향", "전망", "왜 조정해야 해?", "원인이 뭐야?", "앞으로 어떻게 될 것 같아?" 등의 시장 상황이나 원인/분석을 물어보면 **반드시 search_market_news 도구를 먼저 호출하여** 외부 언론 기사 및 거시적 트렌드를 확인한 후 답변에 반영한다.
- 단순 내부 데이터(LTV, 낙찰가율)만으로 "부동산 흐름"을 추측해서 대답하지 말고, 반드시 검색 도구를 거쳐 실제 뉴스나 시장 동향을 결합하라.
- 답변 시 포함할 핵심 요소:
  1. **현재 시장 흐름**: search_market_news에서 파악한 해당 지역의 부동산 거시적/미시적 동향 (예: 거래량, 심리, 대출 규제 등)
  2. **내부 데이터 연계**: 낙찰가율 데이터(tool 결과)가 의미하는 바
  3. **지역 특징**: 특정 지역의 구조적 영향 (예: 인구 유입, 특정 산업 영향 등)
  4. **향후 전망**: 단기적 하방/상방 리스크 및 전망
- 뉴스 데이터를 인용할 때는 출처 URL이나 언론사명을 무작위로 나열하지 말고, 핵심 내용 위주로 전문가처럼 요약하여 서술한다.

[지원하는 은행]
- 광주은행, 전북은행

[용어 설명]
- LTV: 담보인정비율 (Loan-to-Value ratio)
- 낙찰가율: 경매 낙찰가 / 감정가 비율
- 조정대상(red): LTV 조정이 시급한 항목 (가중평균 괴리 10%p 이상)
- 검토대상(yellow): LTV 검토가 필요한 항목 (가중평균 괴리 5~10%p)
- ▼: 하향 추세 (낙찰가율이 LTV보다 낮음)
- ▲: 상향 추세 (낙찰가율이 LTV보다 높음)

[UI 조작 가이드]
- 사용자가 "대시보드 보여줘", "표로 보여줘", "결과를 시각적으로 띄워줘" 같이 화면 이동이나 전체 데이터를 요청하면 반드시 `open_dashboard` 도구를 함께 호출하라. 
- 이 도구는 대화창을 사이드로 밀어버리고 메인 데이터 화면을 유저에게 표시해주는 기능을 한다.
"""


# 도구별 한글 상태 메시지 매핑
TOOL_STATUS_MAP = {
    "get_urgent_signals": "긴급 조정 대상 리스트 분석 중...",
    "get_region_detail": "지역별 상세 현황 데이터 조회 중...",
    "get_auction_stats": "지역 및 담보유형별 낙찰가율 통계 산출 중...",
    "get_ltv_standards": "은행 LTV 적용 기준표 확인 중...",
    "get_available_regions": "조회 가능한 지역 목록 확인 중...",
    "get_available_usages": "조회 가능한 담보유형 조회 중...",
    "navigate_dashboard": "대시보드 화면 및 날짜 전환 요청 처리 중...",
    "open_dashboard": "실시간 데이터 매트릭스 화면으로 전환 중...",
    "search_market_news": "외부 포털 최신 부동산 시장 뉴스 검색 중...",
    "compare_regions": "선택한 두 지역 간의 데이터 격차 비교 중..."
}

def create_chat_agent():
    """
    LangChain ReAct 에이전트를 생성합니다.

    [구조 설명]
    1. ChatOpenAI: OpenAI의 Chat 모델을 LangChain 인터페이스로 감싼 것
       - tool-calling을 지원하는 모델이어야 함 (gpt-4, gpt-4o, gpt-3.5-turbo 등)

    2. tools: LLM이 호출할 수 있는 Python 함수 목록
       - @tool 데코레이터로 정의한 함수들
       - LLM은 함수의 이름과 docstring을 보고 어떤 도구를 쓸지 결정

    3. create_react_agent: ReAct 패턴의 에이전트 그래프를 생성
       - Reasoning: "이 질문에 답하려면 어떤 도구가 필요한가?" 판단
       - Acting: 도구를 호출하여 실제 데이터 조회
       - 결과를 보고 추가 도구가 필요하면 반복, 아니면 최종 답변 생성
    """

    # API 키 설정
    api_key = (OPENAI_API_KEY or os.environ.get("OPENAI_API_KEY", "")).strip()

    # LLM 인스턴스 생성
    # temperature=0: 결정론적 답변 (데이터 분석에서는 일관성이 중요)
    llm = ChatOpenAI(
        model=DEFAULT_MODEL,
        api_key=api_key,
        temperature=1, # 모델 정책 준수
        timeout=60,
    )

    # 에이전트에 제공할 도구 목록
    tools = [
        navigate_dashboard,      # 대시보드 날짜/화면 전환
        open_dashboard,          # 메인 대시보드 강제 열기
        get_urgent_signals,      # 긴급 조정 대상 조회
        get_region_detail,       # 지역별 상세 현황
        get_auction_stats,       # 지역/용도별 낙찰가율 통계
        get_ltv_standards,       # LTV 기준표 조회
        get_available_regions,   # 조회 가능 지역 목록
        get_available_usages,    # 조회 가능 담보유형 목록
        compare_regions,         # 두 지역 비교
        search_market_news,      # 시장 뉴스 검색
    ]

    # ReAct 에이전트 생성
    # prompt 파라미터에 템플릿 객체를 직접 전달하거나 문자열을 전달할 수 있습니다.
    # 여기서는 좀 더 표준적인 프롬프트 구성을 위해 SYSTEM_PROMPT 문자열과 결합하여 사용됩니다.
    agent = create_react_agent(
        model=llm,
        tools=tools,
        prompt=SYSTEM_PROMPT,
    )

    return agent


# ==========================================
# 3단계: 에이전트 실행 함수
# ==========================================

# 에이전트를 모듈 수준에서 1회만 생성 (매 요청마다 새로 만들지 않음)
_agent = None


def get_agent():
    """싱글턴 패턴으로 에이전트 인스턴스를 관리합니다."""
    global _agent
    if _agent is None:
        _agent = create_chat_agent()
    return _agent


def chat(user_message: str, bank: str, base_date: str = None) -> dict:
    """
    사용자 메시지를 받아 에이전트를 실행하고 답변 + UI 액션을 반환합니다.

    Returns:
        dict: {"answer": str, "actions": list}
        - answer: 에이전트의 최종 답변 문자열
        - actions: 프론트엔드가 실행해야 할 UI 액션 목록
          예: [{"action": "set_date", "value": "2025-09"}]
    """
    agent = get_agent()

    # 사용자 메시지에 컨텍스트 정보를 자연스럽게 추가
    context_msg = f"[현재 접속 은행: {bank}"
    if base_date:
        context_msg += f", 분석 기준일: {base_date}"
    context_msg += f"]\n\n{user_message}"

    try:
        result = agent.invoke({
            "messages": [{"role": "user", "content": context_msg}]
        })

        messages = result.get("messages", [])

        # 1) 도구 결과에서 UI 액션 추출
        #    navigate_dashboard 등의 도구가 __UI_ACTION__ 마커를 포함한 JSON을 반환하면
        #    이를 감지하여 actions 배열에 담는다.
        actions = []
        for msg in messages:
            # ToolMessage인 경우 content에서 __UI_ACTION__ 확인
            if hasattr(msg, "content") and isinstance(msg.content, str):
                try:
                    parsed = json.loads(msg.content)
                    if isinstance(parsed, dict) and parsed.get("__UI_ACTION__"):
                        actions.append({
                            "action": parsed["action"],
                            "value": parsed.get("value")
                        })
                except (json.JSONDecodeError, KeyError):
                    pass

        # 2) 마지막 AI 메시지 추출
        answer = "답변을 생성하지 못했습니다. 질문을 다시 시도해 주세요."
        for msg in reversed(messages):
            # AIMessage: content가 있고, tool_calls 속성이 없거나 빈 리스트
            if hasattr(msg, "content") and msg.content:
                tool_calls = getattr(msg, "tool_calls", None)
                if not tool_calls:  # None이거나 빈 리스트
                    answer = msg.content
                    break
            if isinstance(msg, dict) and msg.get("role") == "assistant" and msg.get("content"):
                answer = msg["content"]
                break

        return {"answer": answer, "actions": actions}

    except Exception as e:
        import traceback
        error_msg = f"에이전트 실행 중 오류가 발생했습니다: {str(e)}\n\n상세 정보:\n{traceback.format_exc()}"
        return {"answer": error_msg, "actions": []}


def stream_chat(user_message: str, bank: str, base_date: str = None):
    """
    에이전트 조작 과정을 실시간으로 스트리밍하여 프론트엔드에 현재 동작 상태를 전달합니다.
    (FastAPI StreamingResponse용 제너레이터)
    """
    agent = get_agent()
    context_msg = f"[현재 접속 은행: {bank}"
    if base_date:
        context_msg += f", 분석 기준일: {base_date}"
    context_msg += f"]\n\n{user_message}"

    try:
        # LangGraph 스트리밍 모드로 실행
        for event in agent.stream(
            {"messages": [{"role": "user", "content": context_msg}]},
            stream_mode="values"
        ):
            # 1. 현재 어떤 도구가 호출되었는지 확인하여 상태 전달
            # LangGraph는 실행 단계마다 메시지 리스트의 변화를 내보냅니다.
            if "messages" in event:
                last_msg = event["messages"][-1]
                
                # AI가 도구를 호출하려고 하는 경우 (tool_calls가 있는 경우)
                if hasattr(last_msg, "tool_calls") and last_msg.tool_calls:
                    for tc in last_msg.tool_calls:
                        tool_name = tc["name"]
                        status_text = TOOL_STATUS_MAP.get(tool_name, f"작업({tool_name}) 수행 중...")
                        yield json.dumps({"status": status_text}, ensure_ascii=False) + "\n"

        # 2. 최종 결과 반환 (기존 chat 로직과 동일하되 결과만 yield)
        # 최종 상태(Final State)에서 결과를 다시 한 번 정리해서 보냅니다.
        final_result = chat(user_message, bank, base_date)
        yield json.dumps(final_result, ensure_ascii=False) + "\n"

    except Exception as e:
        import traceback
        yield json.dumps({"error": str(e), "detail": traceback.format_exc()}, ensure_ascii=False) + "\n"
