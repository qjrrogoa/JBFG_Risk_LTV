import os
import sys

# 현재 파일(main.py)이 있는 backend 폴더를 임포트 경로에 추가
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from fastapi import FastAPI, HTTPException, Query, Depends
from fastapi.middleware.cors import CORSMiddleware
from dotenv import load_dotenv
from pydantic import BaseModel
import uvicorn
from sqlalchemy.orm import Session

import services
import chat_agent
from database import get_db
load_dotenv()
app = FastAPI(title="LTV Risk Assessment API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ──────────────────────────────────────────
# 헬스체크
# ──────────────────────────────────────────
@app.get("/")
def read_root():
    return {"message": "LTV 백엔드 API 서버가 정상 작동중입니다."}


@app.get("/api/health")
def health_check():
    return {"status": "ok"}


# ──────────────────────────────────────────
# 인증
# ──────────────────────────────────────────
class LoginRequest(BaseModel):
    bank: str
    username: str
    password: str

class SignupRequest(BaseModel):
    bank: str
    username: str
    password: str


@app.post("/api/auth/login")
def login(req: LoginRequest, db: Session = Depends(get_db)):
    result = services.verify_login(db, req.bank, req.username, req.password)
    if not result["ok"]:
        raise HTTPException(status_code=401, detail=result["message"])
    return result

@app.post("/api/auth/signup")
def signup(req: SignupRequest, db: Session = Depends(get_db)):
    if req.bank not in services.BANK_CONFIG:
        raise HTTPException(status_code=400, detail="알 수 없는 은행입니다.")
    result = services.create_user(db, req.bank, req.username, req.password)
    if not result["ok"]:
        raise HTTPException(status_code=400, detail=result["message"])
    return result

@app.get("/api/auth/check-username")
def check_username(bank: str = Query(...), username: str = Query(...), db: Session = Depends(get_db)):
    exists = services.check_username_exists(db, bank, username)
    return {"exists": exists}


@app.get("/api/banks")
def get_banks():
    return list(services.BANK_CONFIG.keys())


# ──────────────────────────────────────────
# 매트릭스 (기간별 적정성 요약)
# ──────────────────────────────────────────
@app.get("/api/matrix")
def get_ltv_matrix(
    bank: str = Query("광주은행"),
    base_date: str | None = Query(None),
):
    try:
        return services.get_matrix_cache_rows(bank, base_date)
    except FileNotFoundError as e:
        raise HTTPException(status_code=503, detail=str(e))


# ──────────────────────────────────────────
# 긴급 신호 (빠름 — LLM 없음)
# ──────────────────────────────────────────
@app.get("/api/urgent-signals")
def get_urgent_signals(
    bank: str = Query("광주은행"),
    base_date: str | None = Query(None),
):
    try:
        raw_urgent_list, base_ym = services.get_signal_cache_rows(bank, base_date)
        results = []
        for card in raw_urgent_list:
            signal = card.get("signal", {})
            met = card["met"]
            results.append({
                "region": card["reg"],
                "usage": card["usage_type"],
                "category": card["category"],
                "current_ltv": card["ltv_val"],
                "ltv_val": card["ltv_val"],
                "tone": signal.get("tone", ""),
                "direction": signal.get("direction", ""),
                "conservative_ltv": None,
                "conservative_delta": None,
                "relaxed_ltv": None,
                "relaxed_delta": None,
                "reason": None,
                "base_ym": base_ym,
                "met": {
                    "avg": {str(k): v for k, v in met["avg"].items()},
                    "count": {str(k): v for k, v in met["count"].items()},
                },
            })
        return results
    except FileNotFoundError as e:
        raise HTTPException(status_code=503, detail=str(e))


# ──────────────────────────────────────────
# 긴급 대상 리스트 (LLM 권고 포함)
# ──────────────────────────────────────────
@app.get("/api/urgent-list")
def get_urgent_items(
    bank: str = Query("광주은행"),
    base_date: str | None = Query(None),
    sync_ai: bool = Query(True),
):
    try:
        raw_urgent_list, base_ym = services.get_signal_cache_rows(bank, base_date)
        resolved_base_date = f"{base_ym[:4]}-{base_ym[4:]}-01" if base_ym else base_date
        results = services.fetch_all_advice(
            raw_urgent_list,
            bank,
            resolved_base_date,
            async_only=False
        )
        for item in results:
            item["base_ym"] = base_ym
        return results
    except FileNotFoundError as e:
        raise HTTPException(status_code=503, detail=str(e))


@app.get("/api/advice-status")
def get_advice_status(
    bank: str = Query("광주은행"),
    base_date: str | None = Query(None),
):
    try:
        return services.get_advice_generation_status(bank, base_date)
    except FileNotFoundError as e:
        raise HTTPException(status_code=503, detail=str(e))


# ──────────────────────────────────────────
# LTV 기준표 전체 조회
# ──────────────────────────────────────────
@app.get("/api/ltv-table")
def get_ltv_table(
    bank: str = Query("광주은행"),
    base_date: str | None = Query(None),
):
    try:
        return services.get_current_ltv_table(bank, base_date)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ──────────────────────────────────────────
# 차트 데이터 (상세 모달)
# ──────────────────────────────────────────
@app.get("/api/chart-data")
def get_chart_data(
    bank: str = Query("광주은행"),
    region: str = Query(...),
    usage: str = Query(...),
    base_date: str | None = Query(None),
):
    try:
        data = services.get_chart_data(bank, region, usage, base_date)
        return data
    except FileNotFoundError as e:
        raise HTTPException(status_code=503, detail=str(e))


# ──────────────────────────────────────────
# LTV 저장
# ──────────────────────────────────────────
class SaveLtvRequest(BaseModel):
    bank: str
    region: str
    usage: str
    new_ltv: float | None = None # 롤백 시에는 필요 없으므로 Optional 처리
    base_date: str | None = None


@app.post("/api/save-ltv")
def save_ltv(req: SaveLtvRequest):
    result = services.save_ltv(req.bank, req.region, req.usage, req.new_ltv, req.base_date)
    if not result["ok"]:
        raise HTTPException(status_code=400, detail=result["message"])
    return result


@app.post("/api/revert-ltv")
def revert_ltv(req: SaveLtvRequest):
    result = services.revert_ltv(req.bank, req.region, req.usage, req.base_date)
    if not result["ok"]:
        raise HTTPException(status_code=400, detail=result["message"])
    return result


@app.get("/api/ltv-logs")
def get_ltv_logs(bank: str = Query("광주은행")):
    try:
        return services.get_ltv_logs(bank)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ──────────────────────────────────────────
# 챗봇 (LangChain Agent)
# ──────────────────────────────────────────
class ChatRequest(BaseModel):
    message: str
    bank: str
    base_date: str | None = None


from fastapi.responses import StreamingResponse

@app.post("/api/chat")
def chat_endpoint(req: ChatRequest):
    """
    챗봇 스트리밍 엔드포인트: AI의 사고 과정(도구 호출 상태)과 최종 답변을 실시간으로 반환합니다.
    """
    try:
        return StreamingResponse(
            chat_agent.stream_chat(req.message, req.bank, req.base_date),
            media_type="application/x-ndjson"
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
