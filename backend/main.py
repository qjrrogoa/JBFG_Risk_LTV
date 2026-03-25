from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
import uvicorn

app = FastAPI(title="LTV Risk Assessment API", description="Provides calculated LTV matrix data and LLM advice")

# 프론트엔드(React)에서 백엔드 데이터를 가져오기 위해 CORS 허용
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.get("/")
def read_root():
    return {"message": "LTV 백엔드 API 서버가 정상 작동중입니다."}

@app.get("/api/health")
def health_check():
    return {"status": "ok"}

from pydantic import BaseModel
import services

@app.get("/api/summary")
def get_dashboard_summary():
    """
    대시보드 상단 요약 정보 (총 건수 및 LTV 방향성 문구)
    """
    matrix_df, raw_urgent_list = services.get_aggregated_data()
    # LLM이 캐싱되어 있거나 즉시 답변할 수 있게 호출
    urgent_df = services.fetch_all_advice(raw_urgent_list)
    
    total_cnt = len(urgent_df) if not urgent_df.empty else 0
    return {
        "status": "success",
        "total_urgent_items": total_cnt,
        "message": "데이터 로딩 및 분석 완료"
    }

@app.get("/api/matrix")
def get_ltv_matrix():
    """
    메인 테이블 데이터를 제공하는 API
    """
    matrix_df, _ = services.get_aggregated_data()
    # JSON 변환 강제로 처리
    return matrix_df.fillna("").to_dict(orient="records")

@app.get("/api/urgent-list")
def get_urgent_items():
    """
    바로 조치가 필요한(Red/Yellow) 긴급 항목 리스트와 LLM AI 권고안
    """
    _, raw_urgent_list = services.get_aggregated_data()
    urgent_df = services.fetch_all_advice(raw_urgent_list)
    
    if urgent_df.empty:
        return []
    
    return urgent_df.fillna("").to_dict(orient="records")

if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
