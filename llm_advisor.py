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
    prompt = f"""
        As a real estate risk management expert, please propose two LTV (Loan-to-Value) adjustment recommendations for the following property type and region:
        a "conservative option" and a "relaxed option."

        [Data]
        - Region: {item_info['region']}
        - Usage: {item_info['usage']}
        - Current applied LTV: {item_info['current_ltv']}%
        - Recent market auction price ratio trends:
        * Recent 3-month average: {item_info['avg3']:.1f}% ({item_info['cnt3']} cases)
        * Recent 6-month average: {item_info['avg6']:.1f}% ({item_info['cnt6']} cases)
        * Recent 12-month average: {item_info['avg12']:.1f}% ({item_info['cnt12']} cases)
        * Recent 3-year average: {item_info.get('avg36', 0):.1f}% ({item_info.get('cnt36', 0)} cases)

        [Important Rules]
        1. First, determine the adjustment direction.
        - If the recent 3-month, 6-month, and 12-month averages are all lower than the current LTV: this is a downward adjustment case.
        - If the recent 3-month, 6-month, and 12-month averages are all higher than the current LTV: this is an upward adjustment case.
        - Otherwise: treat it as a mixed zone, and avoid excessive adjustments from the current LTV.

        2. In a downward adjustment case:
        - Conservative option = the more significantly lowered option
        - Relaxed option = the less significantly lowered option
        - It must satisfy: conservative option <= relaxed option <= current LTV
        - The relaxed option must not be higher than the current LTV

        3. In an upward adjustment case:
        - Conservative option = the less significantly raised option
        - Relaxed option = the more significantly raised option
        - It must satisfy: current LTV <= conservative option <= relaxed option
        - The conservative option must not be lower than the current LTV

        4. In a mixed zone:
        - Consider recent data consistency to be low and avoid excessive adjustments
        - Both options should remain close to the current LTV

        5. Principles for Data Application & Reasoning (CRITICAL)
        - Conservative Option: Must reflect the worst-case or most severe recent trend (primarily 3-month or 6-month average). It assumes maximum risk.
        - Relaxed Option: Must reflect the longer-term structural average (12-month or 3-year average) to avoid overreacting to short-term market crashes.
        - LOGIC CHECK: When writing the reason, you MUST cite the short-term data (3M/6M) to justify the Conservative Option, and cite the long-term data (12M/3Y) to justify the Relaxed Option. Do NOT mix them up. Never claim a long-term 65% average justifies a 55% conservative option.

        6. Reason for adjustment:
        Write a detailed, data-driven rationale for BOTH the "conservative option" and the "relaxed option" in this format:
        "보수적안: [reason]\n완화적안: [reason]"
        
        Guidelines for the reason:
        - Must be highly analytical, citing the exact trends provided (e.g., "최근 3개월 67%, 6개월 69%로 뚜렷한 하락세 전환").
        - Must justify WHY this specific LTV was chosen compared to the current LTV.
        - Tone & Style: Strictly use formal corporate reporting endings, always ending with noun-forms like "~임", "~함", "~판단됨". DO NOT use "~입니다", "~해요" or "~다".
        - Must be concise and exactly ONE sentence per option (보수적안 1줄, 완화적안 1줄). Each line MUST NOT exceed 100 Korean characters (각 줄당 100자 이내 제한).

        [Output Constraints]
        - Present numbers as integers.
        - ALL proposed LTV values MUST be multiples of 5 (e.g., 40, 45, 50, 55, 60, 65, 70, 75). 
          * Rule map: values ending in 1,2,3 should round DOWN (e.g. 53 -> 50).
          * Rule map: values ending in 4,6,7,8,9 should round UP/DOWN to nearest 5 appropriately (e.g. 54 -> 55, 56 -> 55, 59 -> 60).
        - Only provide values that strictly satisfy the rules above.
        - Output JSON only.
        - All textual content in the response must be written in Korean.

        [Response Format]
        {{
        "direction": "up" | "down" | "mixed",
        "conservative_ltv": float,
        "relaxed_ltv": float,
        "reason": "보수적안: ...\n완화적안: ..."
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
        reason = result.get("reason", result.get("combined_reason", "분석 완료")).strip()
        
        if len(reason) > 200:
            reason = reason[:197] + "..."

        # 5단위 라운딩 (n % 5가 3 이하면 내림, 4면 올림)
        def round_to_5(val):
            r = val % 5
            return val - r if r <= 3 else val + (5 - r)

        conservative_ltv = round_to_5(float(result.get("conservative_ltv", item_info['current_ltv'])))
        relaxed_ltv = round_to_5(float(result.get("relaxed_ltv", item_info['current_ltv'])))
        current_ltv = float(item_info['current_ltv'])

        return {
            "conservative_ltv": conservative_ltv,
            "conservative_delta": conservative_ltv - current_ltv,
            "relaxed_ltv": relaxed_ltv,
            "relaxed_delta": relaxed_ltv - current_ltv,
            "reason": reason
        }
    except Exception as e:
        safe_ltv = float(item_info.get('current_ltv', 0.0))
        return {
            "conservative_ltv": safe_ltv,
            "conservative_delta": 0.0,
            "relaxed_ltv": safe_ltv,
            "relaxed_delta": 0.0,
            "reason": f"LLM({DEFAULT_PROVIDER}) 오류: {str(e)}"
        }
