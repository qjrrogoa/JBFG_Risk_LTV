import os
from openai import OpenAI

# 1. 클라이언트 생성 (키가 설정되어 있다고 가정)
client = OpenAI(api_key=os.getenv("OPENAI_API_KEY", "sk-gMIR9DYnckJUG1qBnii6VUbHHHR9_WefdSI5LliNnJT3BlbkFJzXnESdeQS2zF358vyariY6qxz-BIn7Bqee4OzyaoYA"))

# 2. 아주 간단한 실시간 질문 및 웹 검색 도구 호출
res = client.responses.create(
    model="gpt-5-nano", # gpt-5-nano가 아니더라도 검색 테스트 가능
    input="오늘 한국 서울 날씨는 어때? 구체적으로 알려줘.",
    tools=[{"type": "web_search"}]
)

# 3. 검색 결과 출력
print(res.output_text)
