import os
from sqlalchemy import (
    Column, Integer, String, Float, DateTime, Text, Boolean, create_engine,
    UniqueConstraint, text, inspect
)
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
from dotenv import load_dotenv
from datetime import datetime

# .env 파일 로드
load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"))

DATABASE_URL = os.getenv("DATABASE_URL")
if not DATABASE_URL:
    raise ValueError("DATABASE_URL not found in .env file")

# SQLAlchemy 2.0+ requires postgresql:// instead of postgres://
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

# SQLAlchemy 설정
engine = create_engine(
    DATABASE_URL,
    pool_recycle=3600,
    pool_size=10,
    max_overflow=20,
    pool_use_lifo=True,
    pool_pre_ping=True,
)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

# ------------------------------------------
# 모델 정의
# ------------------------------------------

class LtvStandard(Base):
    __tablename__ = "ltv_standards"

    id = Column(Integer, primary_key=True, index=True)
    bank_name = Column(String, index=True)      # 광주은행, 전북은행
    category = Column(String)                   # 주택, 토지 등
    usage_type = Column(String, index=True)     # 아파트, 단독 등
    region = Column(String, index=True)         # 서울, 경기 등
    ltv_value = Column(Float)                   # 80.0
    effective_date = Column(DateTime, index=True) # 적용시작일

class LtvLog(Base):
    __tablename__ = "ltv_logs"

    id = Column(Integer, primary_key=True, index=True)
    created_at = Column(DateTime, default=datetime.now)
    bank_name = Column(String)
    region = Column(String)
    usage_type = Column(String)
    old_value = Column(Float)
    new_value = Column(Float)
    effective_date = Column(String) # "2026-04-01" 형식
    log_suffix = Column(String)     # "(되돌리기)" 등

class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    bank_name = Column(String, index=True)      # 광주은행, 전북은행
    username = Column(String, index=True)
    hashed_password = Column(String)
    created_at = Column(DateTime, default=datetime.now)

    __table_args__ = (
        UniqueConstraint('bank_name', 'username', name='_bank_user_uc'),
    )

class SignalCache(Base):
    __tablename__ = "signal_cache"

    id = Column(Integer, primary_key=True, index=True)
    bank_name = Column(String, index=True)
    base_ym = Column(String(6), index=True)  # YYYYMM
    region = Column(String, index=True)
    category = Column(String)
    usage_type = Column(String, index=True)
    ltv_value = Column(Float)
    signal_tone = Column(String(16), nullable=True)
    signal_direction = Column(String(8), nullable=True)
    suggested_ltv = Column(Float, nullable=True)
    adjust_delta = Column(Float, nullable=True)
    gap3 = Column(Float, nullable=True)
    avg_3 = Column(Float)
    avg_6 = Column(Float)
    avg_12 = Column(Float)
    avg_36 = Column(Float)
    avg_60 = Column(Float)
    cnt_3 = Column(Integer, default=0)
    cnt_6 = Column(Integer, default=0)
    cnt_12 = Column(Integer, default=0)
    cnt_36 = Column(Integer, default=0)
    cnt_60 = Column(Integer, default=0)
    metric_blob = Column(Text, nullable=True)   # 메타데이터(평균/건수 JSON)
    reason = Column(Text, nullable=True)
    advice_payload = Column(Text, nullable=True)         # AI 권고 캐시(JSON 문자열)
    advice_cache_key = Column(String(64), nullable=True)  # 마지막 저장 시 사용한 AI 캐시 키
    advice_updated_at = Column(DateTime, nullable=True)
    advice_model = Column(String(64), nullable=True)
    is_modified = Column(Boolean, default=False)
    created_at = Column(DateTime, default=datetime.now)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)

    __table_args__ = (
        UniqueConstraint(
            "bank_name",
            "base_ym",
            "region",
            "category",
            "usage_type",
            "ltv_value",
            name="uq_signal_cache_key"
        ),
    )


class RegionAuctionRecord(Base):
    __tablename__ = "region_auction_records"

    id = Column(Integer, primary_key=True, index=True)
    region_file = Column(String(10), index=True)         # 서울.csv, 부산.csv 등 파일명
    case_number = Column(String(64), index=True)         # 사건번호
    usage = Column(String, index=True)                   # 용도
    ltv_gwangju = Column(String(120))                    # LTV_광주
    ltv_jeonbuk = Column(String(120))                    # LTV_전북
    province = Column(String(30), index=True)             # 시도
    district = Column(String(60), index=True)             # 시군구
    address = Column(Text)                               # 소재지
    appraised_value = Column(Float)                       # 감정가
    min_price = Column(Float)                            # 최저가
    result = Column(String, index=True)                  # 결과
    winning_price = Column(Float)                         # 낙찰가
    winning_rate = Column(Float)                          # 낙찰율
    auction_date = Column(DateTime, index=True)           # 매각일
    quarter = Column(String(20))                         # 분기
    period_type = Column(String(20))                     # 기간구분
    row_in_source = Column(Integer)                      # CSV 원본 행번호(1-based)
    created_at = Column(DateTime, default=datetime.now)

    __table_args__ = (
        UniqueConstraint("case_number", name="uq_auction_case_number"),
    )

# 테이블 생성 함수
def init_db():
    Base.metadata.create_all(bind=engine)

# Dependency
def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def ensure_signal_cache_columns():
    """기존 DB에 AI 컬럼이 없을 때 안전하게 추가합니다."""
    try:
        cols = {c["name"] for c in inspect(engine).get_columns("signal_cache")}
        with engine.begin() as conn:
            if "advice_payload" not in cols:
                conn.execute(text("ALTER TABLE signal_cache ADD COLUMN advice_payload TEXT"))
            if "advice_cache_key" not in cols:
                conn.execute(text("ALTER TABLE signal_cache ADD COLUMN advice_cache_key VARCHAR(64)"))
            if "advice_updated_at" not in cols:
                conn.execute(text("ALTER TABLE signal_cache ADD COLUMN advice_updated_at TIMESTAMP"))
            if "advice_model" not in cols:
                conn.execute(text("ALTER TABLE signal_cache ADD COLUMN advice_model VARCHAR(64)"))
    except Exception:
        # 스키마 보정은 선택적 동작이므로 실행 실패해도 앱 시작을 막지 않음.
        # 실제 쿼리에서 컬럼 부재 오류가 나면 다시 시도할 수 있음.
        pass


def drop_legacy_llm_cache():
    """과거 전용 llm_cache 테이블이 남아 있으면 삭제합니다."""
    with engine.begin() as conn:
        conn.execute(text("DROP TABLE IF EXISTS llm_cache CASCADE"))