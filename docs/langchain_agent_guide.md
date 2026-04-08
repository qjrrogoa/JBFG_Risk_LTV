# LangChain LTV 챗봇 에이전트 구현 가이드

## 아키텍처 개요

```mermaid
flowchart LR
    A[사용자 질문] --> B[FastAPI /api/chat]
    B --> C[LangChain ReAct Agent]
    C --> D{LLM 추론}
    D -->|도구 필요| E[@tool 함수 호출]
    E --> F[pandas 데이터 조회]
    F --> G[JSON 결과 반환]
    G --> D
    D -->|답변 완성| H[한국어 답변]
    H --> B
    B --> A
```

> [!IMPORTANT]
> **핵심 원칙**: LLM은 **질문 해석 + 결과 설명**만 담당하고, **실제 데이터 조회/계산은 pandas**가 수행합니다.

---

## 파일 구조

| 파일 | 역할 |
|------|------|
| [chat_agent.py](file:///c:/Users/jemaj/Documents/JBFG_Risk_LTV/backend/chat_agent.py) | LangChain 에이전트 핵심 모듈 (도구 정의 + 에이전트 생성) |
| [main.py](file:///c:/Users/jemaj/Documents/JBFG_Risk_LTV/backend/main.py) | FastAPI 엔드포인트 (`/api/chat`) |
| [App_v2.jsx](file:///c:/Users/jemaj/Documents/JBFG_Risk_LTV/frontend/src/App_v2.jsx) | 프론트엔드 채팅 UI |
| [services.py](file:///c:/Users/jemaj/Documents/JBFG_Risk_LTV/backend/services.py) | 기존 데이터 로딩/집계 (chat_agent가 재사용) |
| [llm_advisor.py](file:///c:/Users/jemaj/Documents/JBFG_Risk_LTV/llm_advisor.py) | API 키/모델 설정 (chat_agent가 참조) |

---

## 핵심 코드 설명

### 1. `@tool` 데코레이터란?

```python
from langchain_core.tools import tool

@tool
def get_urgent_signals(bank: str, base_date: Optional[str] = None) -> str:
    """현재 LTV 조정이 필요한 긴급 대상 목록을 조회합니다."""
    # ... pandas로 실제 데이터 조회 ...
    return json.dumps(result, ensure_ascii=False)
```

- `@tool`을 붙이면 일반 Python 함수가 **LLM이 호출할 수 있는 도구**가 됩니다.
- LLM은 함수의 **이름**, **docstring**, **파라미터 타입**을 보고 어떤 도구를 쓸지 자동 결정합니다.
- 따라서 **docstring을 명확하게 한국어로 작성하는 것이 매우 중요합니다**.

> [!TIP]
> 반환값은 반드시 **문자열(str)**이어야 합니다. JSON으로 직렬화하여 반환하세요.

### 2. ReAct 에이전트란?

```python
from langgraph.prebuilt import create_react_agent

agent = create_react_agent(
    model=llm,        # 추론을 담당할 LLM
    tools=tools,      # LLM이 호출 가능한 도구 목록
    prompt=SYSTEM_PROMPT,  # 에이전트의 역할/규칙 정의
)
```

**ReAct = Reasoning + Acting** 패턴입니다:

```
사용자: "서울 아파트 현황 보여줘"
    ↓
[Reasoning] "서울 지역의 아파트 데이터를 조회해야 함 → get_region_detail 또는 get_auction_stats 사용"
    ↓
[Acting] get_auction_stats(bank="광주은행", region="서울", usage="아파트") 호출
    ↓
[Observation] {"지역": "서울", "담보유형": "아파트", "현재LTV": 80, "통계": {...}}
    ↓
[Reasoning] "결과가 충분함 → 한국어로 설명 작성"
    ↓
[Final Answer] "서울 아파트의 현재 LTV는 80%이며, 최근 3개월 평균 낙찰가율은 ..."
```

### 3. 시스템 프롬프트의 역할

```python
SYSTEM_PROMPT = """
너는 LTV 적정성 분석 전문 AI 도우미다.

[핵심 규칙]
1. 숫자는 반드시 tool 호출 결과만 사용한다. 절대 추측하지 않는다.
2. 데이터가 필요한 질문이면 반드시 적절한 tool을 먼저 호출한다.
...
"""
```

- 에이전트의 **행동 규칙**을 정의합니다.
- "숫자 추측 금지"는 LLM 환각(hallucination) 방지를 위해 매우 중요합니다.
- 용어 설명을 포함하면 LLM이 도메인 맥락을 더 잘 이해합니다.

### 4. 에이전트 실행 흐름

```python
def chat(user_message, bank, base_date):
    agent = get_agent()  # 싱글턴으로 관리 (매번 새로 만들지 않음)

    # 사용자 메시지에 컨텍스트 추가
    context_msg = f"[현재 접속 은행: {bank}]\n\n{user_message}"

    result = agent.invoke({
        "messages": [{"role": "user", "content": context_msg}]
    })

    # 결과에서 마지막 AI 메시지 추출
    messages = result["messages"]
    # messages = [user_msg, tool_call, tool_result, ..., ai_final_answer]
```

> [!NOTE]
> `agent.invoke()` 내부에서 LLM ↔ tool 호출이 자동으로 반복됩니다. 복잡한 질문이면 여러 도구를 순차 호출할 수도 있습니다.

---

## 등록된 도구 목록

| 도구명 | 설명 | 예시 질문 |
|--------|------|-----------|
| `get_urgent_signals` | 긴급 조정/검토 대상 목록 | "조정대상 보여줘", "검토 필요한 항목은?" |
| `get_region_detail` | 특정 지역 상세 현황 | "서울 현황", "경기 담보유형별 상태는?" |
| `get_auction_stats` | 지역/용도별 낙찰가율 통계 | "광주 아파트 낙찰가율은?", "전남 다세대 통계" |
| `get_ltv_standards` | LTV 기준표 조회 | "현재 LTV 기준표 보여줘" |
| `get_available_regions` | 조회 가능 지역 목록 | "어떤 지역을 볼 수 있어?" |
| `get_available_usages` | 조회 가능 담보유형 목록 | "서울에 어떤 담보유형이 있어?" |
| `compare_regions` | 두 지역 비교 | "서울이랑 경기 아파트 비교해줘" |

---

## 확장 가이드

### 새로운 도구 추가하기

```python
@tool
def get_monthly_trend(bank: str, region: str, usage: str, months: int = 6) -> str:
    """특정 지역/담보유형의 월별 낙찰가율 추이를 조회합니다.
    
    Args:
        bank: 은행명
        region: 지역명
        usage: 담보유형
        months: 조회할 개월 수 (기본 6개월)
    """
    # pandas로 데이터 조회 ...
    return json.dumps(result, ensure_ascii=False)
```

그 다음 `create_chat_agent()` 함수의 `tools` 리스트에 추가하면 됩니다.

> [!WARNING]
> 도구를 너무 많이 추가하면 LLM이 혼란스러워할 수 있습니다. 10개 이내로 유지하는 것을 권장합니다.

### RAG 확장 (업무 규정 문서 Q&A)

현재 구조는 숫자 데이터 질의에 최적화되어 있습니다. 만약 "조정대상 판정 기준이 뭐야?" 같은 업무 규정 질문도 받고 싶다면:

1. 규정 문서를 마크다운/텍스트로 준비
2. `langchain_community.vectorstores`로 벡터 DB 구성
3. retrieval tool을 하나 더 추가

이렇게 하면 **숫자 질의 = pandas 도구**, **규정 질의 = RAG 도구**로 자연스럽게 분리됩니다.
