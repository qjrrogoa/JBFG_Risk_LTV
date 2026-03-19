import google.generativeai as genai
import os
import json
import streamlit as st
try:
    from openai import OpenAI
except Exception:
    OpenAI = None
    import openai as openai_legacy

# =========================================================
# LLM 설정 (이 부분을 수정하여 모델을 엔진을 변경하세요)
# =========================================================
DEFAULT_PROVIDER = "OpenAI"           # "Gemini" 또는 "OpenAI"
DEFAULT_MODEL = "gpt-5-nano"        # 사용하고자 하는 모델명 입력

# DEFAULT_PROVIDER = "Gemini"           # "Gemini" 또는 "OpenAI"
# DEFAULT_MODEL = "gemini-2.5-flash-lite"        

# API 키 설정
GEMINI_API_KEY = " AIzaSyB0vR0tkEfmu0QNcw8xManSG9gu81RErKY "
OPENAI_API_KEY = "sk-gMIR9DYnckJUG1qBnii6VUbHHHR9_WefdSI5LliNnJT3BlbkFJzXnESdeQS2zF358vyariY6qxz-BIn7Bqee4OzyaoYA"

def get_ltv_advice(item_info):
    """
    LLM을 사용하여 LTV 조정 권고를 가져옵니다.
    설정된 DEFAULT_PROVIDER와 DEFAULT_MODEL을 기반으로 작동합니다.
    """
    prompt = f"""
부동산 리스크 관리 전문가로서 다음 건물 유형 및 지역에 대한 LTV(담보인정비율) 조정 권고를 '보수적 안'과 '완화적 안' 두 가지로 제안해주세요.

[데이터]
- 지역: {item_info['region']}
- 용도: {item_info['usage']}
- 현재 적용 LTV: {item_info['current_ltv']}%
- 최근 시장 낙찰가율 현황:
  * 최근 3개월 평균: {item_info['avg3']:.1f}% ({item_info['cnt3']}건)
  * 최근 6개월 평균: {item_info['avg6']:.1f}% ({item_info['cnt6']}건)
  * 최근 12개월 평균: {item_info['avg12']:.1f}% ({item_info['cnt12']}건)
  * 최근 3년 평균: {item_info.get('avg36', 0):.1f}% ({item_info.get('cnt36', 0)}건)

[지침]
1. 보수적 권고안: 리스크 관리를 우선시하여 12개월 또는 3년 장기 평균을 중점적으로 반영한 안정적인 LTV.
2. 완화적 권고안: 최근 3~6개월의 시장 상승 흐름이나 회복세를 더 적극적으로 반영한 LTV.
3. 조정 사유: 보수적 안과 완화적 안이 각각 어떤 지표를 중점적으로 반영했는지 비교하여 문어체로 '매우 간결하게' 설명하십시오. (공백 포함 최대 80자 제한)

[응답 형식]
JSON 형식으로만 답변하세요.
{{
  "conservative_ltv": float,
  "conservative_delta": float, 
  "relaxed_ltv": float,
  "relaxed_delta": float, 
  "combined_reason": "text"
}}
"""

    try:
        if DEFAULT_PROVIDER == "Gemini":
            api_key = GEMINI_API_KEY if GEMINI_API_KEY and "입력하세요" not in GEMINI_API_KEY else os.environ.get("GEMINI_API_KEY", "")
            if not api_key: raise Exception("Gemini API 키가 설정되지 않았습니다.")
            genai.configure(api_key=api_key)
            model = genai.GenerativeModel(DEFAULT_MODEL)
            response = model.generate_content(prompt)
            text = response.text.strip()
        else:
            # OpenAI / GPT 5.4 Nano
            api_key = OPENAI_API_KEY if OPENAI_API_KEY else os.environ.get("OPENAI_API_KEY", "")
            if not api_key: raise Exception("OpenAI API 키가 설정되지 않았습니다.")
            if OpenAI:
                client = OpenAI(api_key=api_key)
                try:
                    response = client.responses.create(
                        model=DEFAULT_MODEL,
                        input=prompt,
                        # response_format={ "type": "json_object" }
                    )
                    text = response.output_text.strip()
                except Exception as responses_error:
                    completion = client.chat.completions.create(
                        model=DEFAULT_MODEL,
                        messages=[{"role": "user", "content": prompt}],
                        response_format={"type": "json_object"}
                    )
                    text = completion.choices[0].message.content.strip()
            else:
                openai_legacy.api_key = api_key
                completion = openai_legacy.ChatCompletion.create(
                    model=DEFAULT_MODEL,
                    messages=[{"role": "user", "content": prompt}]
                )
                text = completion["choices"][0]["message"]["content"].strip()

        # JSON 파싱 및 데이터 추출
        if "```json" in text:
            text = text.split("```json")[1].split("```")[0].strip()
        elif "```" in text:
            text = text.split("```")[1].split("```")[0].strip()
        
        result = json.loads(text)
        reason = result.get("combined_reason", "분석 완료")
        
        if len(reason) > 80:
            reason = reason[:77] + "..."

        return {
            "conservative_ltv": result.get("conservative_ltv", item_info['current_ltv']),
            "conservative_delta": result.get("conservative_delta", 0.0),
            "relaxed_ltv": result.get("relaxed_ltv", item_info['current_ltv']),
            "relaxed_delta": result.get("relaxed_delta", 0.0),
            "reason": reason
        }
    except Exception as e:
        return {
            "conservative_ltv": item_info['current_ltv'],
            "conservative_delta": 0.0,
            "relaxed_ltv": item_info['current_ltv'],
            "relaxed_delta": 0.0,
            "reason": f"LLM({DEFAULT_PROVIDER}) 오류: {str(e)}"
        }
