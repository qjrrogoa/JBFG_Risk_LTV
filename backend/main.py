from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from dotenv import load_dotenv
from pydantic import BaseModel
import uvicorn

import services
import chat_agent
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
    password: str


@app.post("/api/auth/login")
def login(req: LoginRequest):
    if req.bank not in services.BANK_CONFIG:
        raise HTTPException(status_code=400, detail="알 수 없는 은행입니다.")
    if not services.verify_login(req.bank, req.password):
        raise HTTPException(status_code=401, detail="비밀번호가 올바르지 않습니다.")
    return {"ok": True, "bank": req.bank}


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
        matrix_df, _ = services.get_aggregated_data(bank, base_date)
        return matrix_df.fillna("").to_dict(orient="records")
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
        _, raw_urgent_list = services.get_aggregated_data(bank, base_date)
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
):
    try:
        _, raw_urgent_list = services.get_aggregated_data(bank, base_date)
        results = services.fetch_all_advice(raw_urgent_list, bank, base_date)
        return results
    except FileNotFoundError as e:
        raise HTTPException(status_code=503, detail=str(e))


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
    new_ltv: float


@app.post("/api/save-ltv")
def save_ltv(req: SaveLtvRequest):
    result = services.save_ltv(req.bank, req.region, req.usage, req.new_ltv)
    if not result["ok"]:
        raise HTTPException(status_code=400, detail=result["message"])
    return result


# ──────────────────────────────────────────
# 챗봇 (LangChain Agent)
# ──────────────────────────────────────────
class ChatRequest(BaseModel):
    message: str
    bank: str
    base_date: str | None = None


@app.post("/api/chat")
def chat_endpoint(req: ChatRequest):
    try:
        result = chat_agent.chat(req.message, req.bank, req.base_date)
        # result = {"answer": "...", "actions": [...]}
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
