import hashlib
import json
import logging
import os
import re
import time
import random
from datetime import datetime, timezone
from typing import Any, Dict

from openai import OpenAI

logger = logging.getLogger(__name__)

MAX_ADVICE_RETRIES = 5

# =========================================================
# 1. 설정 (환경 변수 우선)
# =========================================================
DEFAULT_MODEL = os.getenv("LTV_ADVISOR_MODEL", "gpt-5-nano")
DEFAULT_SEARCH_CONTEXT = os.getenv("LTV_WEB_SEARCH_CONTEXT", "high")
DEFAULT_USE_WEB_SEARCH = os.getenv("LTV_USE_WEB_SEARCH", "false").strip().lower() in ("1", "true", "yes", "on")
DEFAULT_MAX_OUTPUT_TOKENS = int(os.getenv("LTV_ADVISOR_MAX_OUTPUT_TOKENS", "2000"))
DEFAULT_REASONING_EFFORT = os.getenv("LTV_ADVISOR_REASONING_EFFORT", "minimal").strip().lower()

LTV_ADVICE_JSON_SCHEMA = {
    "type": "object",
    "properties": {
        "region": {"type": "string"},
        "usage_type": {"type": "string"},
        "conservative_ltv": {"type": "number"},
        "relaxed_ltv": {"type": "number"},
        "reason": {"type": "string"},
    },
    "required": ["region", "usage_type", "conservative_ltv", "relaxed_ltv", "reason"],
    "additionalProperties": False,
}
# API 키는 보안상 소스에 직접 노출하지 않고 환경변수를 사용하거나, 
# 사용자 계정의 sk-... 키가 설정된 경우 이를 참조합니다.
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "sk-gMIR9DYnckJUG1qBnii6VUbHHHR9_WefdSI5LliNnJT3BlbkFJzXnESdeQS2zF358vyariY6qxz-BIn7Bqee4OzyaoYA")

def _get_client() -> OpenAI:
    if not OPENAI_API_KEY:
        raise RuntimeError("OPENAI_API_KEY 환경변수가 설정되지 않았습니다.")
    
    client = OpenAI(api_key=OPENAI_API_KEY)
    # LangSmith 추적을 위한 OpenAI 래핑 적용
    try:
        from langsmith import wrappers
        client = wrappers.wrap_openai(client)
    except ImportError:
        logger.warning("langsmith 패키지를 찾을 수 없어 LangSmith 추적이 비활성화됩니다.")
        
    return client

def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None:
            return default
        return float(value)
    except (TypeError, ValueError):
        return default

def _extract_json(text: str) -> Dict[str, Any]:
    """텍스트에서 JSON 객체를 추출하여 파싱합니다 (마크다운 코드 블록 대응)."""
    text = text.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # ```json ... ``` 또는 ``` ... ``` 블록 찾기
    match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(1))
        except json.JSONDecodeError:
            pass

    # 가장 바깥쪽 { } 찾기
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end != -1:
        try:
            return json.loads(text[start:end+1])
        except json.JSONDecodeError:
            pass

    raise ValueError("응답에서 유효한 JSON을 찾을 수 없거나 형식이 올바르지 않습니다.")


def _extract_output_text(response) -> str:
    txt = getattr(response, "output_text", None)
    if txt:
        return str(txt)

    outputs = getattr(response, "output", None) or []
    for item in outputs:
        if getattr(item, "type", "") != "message":
            continue
        content = getattr(item, "content", None) or []
        for c in content:
            if getattr(c, "type", "") == "output_text":
                value = getattr(c, "text", "")
                if value:
                    return str(value)

    if isinstance(response, dict):
        response_dict = response
    elif hasattr(response, "model_dump"):
        try:
            response_dict = response.model_dump()
        except Exception:
            response_dict = {}
    else:
        response_dict = {}

    for item in response_dict.get("output", []) or []:
        if not isinstance(item, dict) or item.get("type") != "message":
            continue
        for content in item.get("content", []) or []:
            if not isinstance(content, dict):
                continue
            parsed = content.get("parsed")
            if parsed:
                return json.dumps(parsed, ensure_ascii=False)
            text = content.get("text")
            if text:
                return str(text)
            output_text = content.get("output_text")
            if output_text:
                return str(output_text)
    return ""


def _response_debug_summary(response) -> str:
    if isinstance(response, dict):
        response_dict = response
    elif hasattr(response, "model_dump"):
        try:
            response_dict = response.model_dump()
        except Exception:
            response_dict = {}
    else:
        response_dict = {}

    status = response_dict.get("status") or getattr(response, "status", "")
    incomplete = response_dict.get("incomplete_details") or getattr(response, "incomplete_details", "")
    output_types = []
    for item in response_dict.get("output", []) or []:
        if isinstance(item, dict):
            output_types.append(str(item.get("type", "")))
        else:
            output_types.append(str(getattr(item, "type", "")))
    return f"status={status}, incomplete_details={incomplete}, output_types={output_types}"


def _retry_wait_seconds(exc: Exception, attempt: int) -> float:
    err_msg = str(exc)
    match = re.search(r"try again in\s*(\d+)ms", err_msg)
    if match:
        try:
            return max(0.15, int(match.group(1)) / 1000.0)
        except Exception:
            pass
    return min(30.0, (2 ** (attempt - 1)) + random.uniform(0.0, 1.0))

# =========================================================
# 2. 캐시 관리 로직 (Revised 방식)
# =========================================================
def build_cache_key(item_info: Dict[str, Any]) -> str:
    """권고안 캐시 키 생성: 시점/통계/시그널이 바뀌면 새 권고안을 만들도록 구성."""
    region = str(item_info.get("region", "unknown"))
    usage = str(item_info.get("usage", "unknown"))
    payload = {
        "bank": item_info.get("bank_name", ""),
        "base_date": str(item_info.get("base_date", "")),
        "region": region,
        "usage": usage,
        "tone": str(item_info.get("tone", "")),
        "current_ltv": round(_safe_float(item_info.get("current_ltv")), 2),
        "avg3": round(_safe_float(item_info.get("avg3")), 2),
        "avg6": round(_safe_float(item_info.get("avg6")), 2),
        "avg12": round(_safe_float(item_info.get("avg12")), 2),
        "avg36": round(_safe_float(item_info.get("avg36")), 2)
    }
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    digest = hashlib.sha1(raw.encode("utf-8")).hexdigest()[:8]
    # 읽기 쉽도록 지역_담보_해시 형태의 키 생성
    clean_region = re.sub(r'[^\w\s]', '', region).replace(' ', '')
    clean_usage = re.sub(r'[^\w\s]', '', usage).replace(' ', '')
    return f"advice_{clean_region}_{clean_usage}_{digest}"

def cache_ttl_hours(tone: str) -> int:
    """시그널별 유효기간 설정 (Red: 24h, Yellow: 72h, Normal: 168h)"""
    tone = (tone or "").lower().strip()
    if tone == "red":
        return 24
    if tone == "yellow":
        return 72
    return 168

def is_cache_fresh(advice: Dict[str, Any], tone: str) -> bool:
    """캐시 데이터가 최신 포맷이고 유효기간(TTL) 내에 있는지 확인합니다."""
    if not advice or not isinstance(advice, dict):
        return False

    generated_at = advice.get("generated_at")
    if not generated_at:
        return False

    try:
        # ISO 포맷 파싱
        ts = datetime.fromisoformat(str(generated_at).replace("Z", "+00:00"))
    except Exception:
        return False

    now = datetime.now(timezone.utc)
    age_hours = (now - ts.astimezone(timezone.utc)).total_seconds() / 3600.0
    if age_hours > cache_ttl_hours(tone):
        return False

    # 필수 내용 포함 여부 검사
    reason = str(advice.get("reason", ""))
    if len(reason) < 50:
        return False

    return True

# =========================================================
# 3. 보정 및 Fallback 로직
# =========================================================
def _clamp_ltv(value: Any, current_ltv: float, tone: str) -> float:
    """급진적인 LTV 추천을 방지하기 위한 보정 함수"""
    v = _safe_float(value, current_ltv)
    tone = (tone or "").lower().strip()
    max_gap = 15.0 if tone == "red" else 10.0
    lower = max(0.0, current_ltv - max_gap)
    upper = min(100.0, current_ltv + max_gap)
    return round(min(max(v, lower), upper), 1)

def _fallback_advice(item_info: Dict[str, Any], error_message: str) -> Dict[str, Any]:
    """검색 실패 또는 오류 시 내부 통계를 활용한 대체 답변 생성"""
    current_ltv = _safe_float(item_info.get("current_ltv"), 80.0)
    tone = (item_info.get("tone") or "").lower().strip()
    avg3 = _safe_float(item_info.get("avg3"))
    avg6 = _safe_float(item_info.get("avg6"))
    avg12 = _safe_float(item_info.get("avg12"))

    if avg3 and avg6 and avg12:
        avg_mean = round((avg3 * 0.5 + avg6 * 0.3 + avg12 * 0.2), 1)
    else:
        avg_mean = current_ltv

    gap = round(avg_mean - current_ltv, 1)
    if gap >= 5:
        conservative = current_ltv + min(5.0, gap * 0.5)
        relaxed = current_ltv + min(10.0, gap)
    elif gap <= -5:
        conservative = current_ltv + max(-10.0, gap)
        relaxed = current_ltv + max(-5.0, gap * 0.5)
    else:
        conservative = current_ltv
        relaxed = current_ltv

    conservative = _clamp_ltv(conservative, current_ltv, tone)
    relaxed = _clamp_ltv(relaxed, current_ltv, tone)

    return {
        "region": item_info.get("region", ""),
        "usage_type": item_info.get("usage", ""),
        "conservative_ltv": conservative,
        "relaxed_ltv": relaxed,
        "reason": (
            f"실시간 AI 모니터링 모듈 호출 지연(오류: {error_message})으로 인해 내부 통계 지표(현재 LTV {current_ltv:.1f}%, 낙찰가율 가중평균 {avg_mean:.1f}%)를 기반으로 긴급 산출된 권고안입니다. 실시간 시장 웹 검색 결과는 반영되지 않았으므로 보수적인 관점의 가이드라인을 준수하십시오."
        ),
        "sources": [],
        "search_used": False,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "model": DEFAULT_MODEL,
        "error": error_message,
        "timestamp": time.time()
    }

# =========================================================
# 4. 메인 분석 함수 (OpenAI Responses API + Web Search)
# =========================================================
def get_ltv_advice(item_info: Dict[str, Any]) -> Dict[str, Any]:
    """OpenAI built-in Web Search를 활용하여 전문가 수준의 정밀 리포트를 생성합니다."""
    current_ltv = _safe_float(item_info.get("current_ltv"), 80.0)
    tone = (item_info.get("tone") or "normal").lower().strip()

    prompt = f"""
    당신은 국내 은행의 부동산 담보 리스크 관리 담당자다.

    목표는 '보수적안(conservative_ltv)'과 '완화적안(relaxed_ltv)'에 대해
    왜 그 수치를 제안하는지 간결하고 구체적으로 설명하는 것이다.

    반드시 최신 웹 검색을 사용하되,
    전국 거시 기사 나열이 아니라 아래 순서로 관련성이 높은 근거만 반영하라.

    우선순위:
    1. 해당 지역의 최근 부동산 시장 흐름
    2. 해당 지역의 경매/낙찰가율/유찰 흐름
    3. 해당 담보유형(아파트, 단독, 임야, 근린상가 등)의 거래 및 회수 리스크 특성
    4. 금리/정책/규제는 필요한 경우에만 보조 근거로 짧게 반영

    중요:
    - 출력은 '시장 보고서'가 아니라 '제시안의 이유 설명'이어야 한다.
    - 기사나 뉴스 내용의 원문 출처(언론사명, 기사 제목, URL)를 본문에 직접 언급하거나 표기하지 마라.
    - 특정 지역 및 담보유형에 대한 기사가 부족하다면, 해당 광역 지역(예: 도 전체, 광역시 전체) 또는 담보 시장 전체의 거시적 흐름으로 범위를 넓혀서 유의미한 시장 근거를 반드시 찾아 반영하라.
    - '기사 부족', '특화 자료 제한', '데이터 부족' 같은 변명을 문장에 절대 포함하지 말고, 검색된 가장 관련성 높은 광역적 데이터를 바탕으로 전문가답게 확신에 찬 어조로 사유를 작성하라.
    - 반드시 사유 시작 부분에 해당 지역과 담보유형을 명시하며 분석을 시작하라. (예: '서울 지역 아파트 시장의 경우...')
    - reason은 5~8문장 이내로 작성하라.
    - 각 문장은 실제 제안 수치와 연결되어야 한다.
    - 모든 문장은 "-습니다", "-입니다"와 같은 정중한 경어체로 끝내야 한다.
    - 한국어 문장 중간에 불필요한 영단어(예: current LTV, conservative_ltv 등)를 섞어 쓰지 말고 자연스러운 우리말로 순화하여 작성하라.
    - **중요**: JSON 파싱 오류를 방지하기 위해 "reason" 값 내부의 문자열을 작성할 때 쌍따옴표(")를 절대 사용하지 말고, 대신 홑따옴표(')만을 사용하라.

    [분석 대상]
    - 은행: {item_info.get('bank_name', '미지정')}
    - 지역: {item_info.get('region', '')}
    - 담보유형: {item_info.get('usage', '')}
    - 현재 적용 LTV: {current_ltv:.1f}%
    - 최근 3개월 평균: {_safe_float(item_info.get('avg3')):.1f}% (건수 {int(item_info.get('cnt3', 0) or 0)})
    - 최근 6개월 평균: {_safe_float(item_info.get('avg6')):.1f}% (건수 {int(item_info.get('cnt6', 0) or 0)})
    - 최근 12개월 평균: {_safe_float(item_info.get('avg12')):.1f}% (건수 {int(item_info.get('cnt12', 0) or 0)})

    [판정 원칙 및 수치 가이드라인]
    1. 조정 방향 판정:
    - 하향 조정: 3, 6, 12개월 평균이 모두 현재 LTV보다 낮을 때
    - 상향 조정: 3, 6, 12개월 평균이 모두 현재 LTV보다 높을 때
    - 혼조세: 그 외의 경우 (과도한 조정을 피함)

    2. 수치 산출 제약:
    - 하향 조정 시: 보수적안(가장 안전) <= 완화적안 <= 현재 LTV
    - 상향 조정 시: 현재 LTV <= 보수적안 <= 완화적안(가장 공격적)
    - 보수적안(conservative_ltv)은 항상 리스크 관리에 우선순위를 둔 가장 안전한 수치여야 한다.

    [출력 스키마]
    반드시 다음 형태의 JSON 객체로 답변하라. (추가적인 텍스트 없이 JSON 객체 하나만 출력할 것)

{{"region": "...", "usage_type": "...", "conservative_ltv": float, "relaxed_ltv": float, "reason": "..."}}
""".strip()

    max_retries = MAX_ADVICE_RETRIES
    data = None
    use_web_search = DEFAULT_USE_WEB_SEARCH

    for attempt in range(1, max_retries + 1):
        try:
            client = _get_client()
            request_kwargs = {
                "model": DEFAULT_MODEL,
                "input": prompt,
                "temperature": 1,  # o1 계열 및 gpt-5-nano 모델 호환성을 위해 1로 고정
                "max_output_tokens": DEFAULT_MAX_OUTPUT_TOKENS,
                "text": {
                    "format": {
                        "type": "json_schema",
                        "name": "ltv_advice",
                        "strict": True,
                        "schema": LTV_ADVICE_JSON_SCHEMA,
                    }
                },
            }
            if DEFAULT_MODEL.startswith("gpt-5"):
                request_kwargs["reasoning"] = {"effort": DEFAULT_REASONING_EFFORT}
            if use_web_search:
                request_kwargs["tools"] = [{
                    "type": "web_search",
                    "search_context_size": DEFAULT_SEARCH_CONTEXT,
                }]

            response = client.responses.create(**request_kwargs)
            output_text = _extract_output_text(response)
            if not output_text:
                raise RuntimeError(f"응답 텍스트가 비어 있습니다. {_response_debug_summary(response)}")
            data = _extract_json(output_text)
            break
        except Exception as exc:
            err_msg = str(exc).lower()
            if any(k in err_msg for k in ["429", "rate limit", "overloaded", "service_unavailable", "503"]):
                wait_sec = _retry_wait_seconds(exc, attempt)
                logger.warning(
                    "AI API 일시적 오류 발생 (시도 %s/%s): %s. %.2f초 후 재시도합니다.",
                    attempt,
                    max_retries,
                    exc,
                    wait_sec,
                )
                # 토큰 소모를 줄이기 위해 1회 실패 후 웹검색 없이 재시도
                use_web_search = False
                if attempt < max_retries:
                    time.sleep(wait_sec)
                    continue
            else:
                # 일시적 오류가 아닌 경우에는 즉시 fallback
                logger.exception("AI 분석 리포트 생성 중 예외 발생")
                return _fallback_advice(item_info, str(exc))

    if data is None:
        return _fallback_advice(item_info, "AI 응답 파싱 또는 재시도 실패")

    # -------------------------------------------------------------
    # [검증] 웹 검색 성공 여부 및 리포트 품질 강제 검증
    # -------------------------------------------------------------
    # sources = data.get("sources", [])
    reason = str(data.get("reason", ""))
    if len(reason) < 50:
        return _fallback_advice(item_info, "생성된 답변(reason)의 내용이 너무 짧거나 형식이 규격에 맞지 않습니다.")

    # LTV 보정
    data["conservative_ltv"] = _clamp_ltv(data.get("conservative_ltv"), current_ltv, tone)
    data["relaxed_ltv"] = _clamp_ltv(data.get("relaxed_ltv"), current_ltv, tone)

    # 보수적안이 완화적안보다 크지 않도록 보정
    if data["conservative_ltv"] > data["relaxed_ltv"]:
        data["conservative_ltv"], data["relaxed_ltv"] = data["relaxed_ltv"], data["conservative_ltv"]

    data.setdefault("region", item_info.get("region", ""))
    data.setdefault("usage_type", item_info.get("usage", ""))
    data.setdefault("sources", [])
    data["search_used"] = use_web_search
    data["generated_at"] = datetime.now(timezone.utc).isoformat()
    data["model"] = DEFAULT_MODEL
    data["timestamp"] = time.time()
    return data
