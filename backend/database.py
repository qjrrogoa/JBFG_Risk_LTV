import os
from sqlalchemy import Column, Integer, String, Float, DateTime, create_engine
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
from dotenv import load_dotenv
from datetime import datetime

# .env 파일 로드
load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"))

DATABASE_URL = os.getenv("DATABASE_URL")
if not DATABASE_URL:
    raise ValueError("DATABASE_URL not found in .env file")

# SQLAlchemy 설정
engine = create_engine(
    DATABASE_URL,
    pool_recycle=3600,
    pool_size=10,
    max_overflow=20,
    pool_use_lifo=True
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
    username = Column(String, unique=True, index=True)
    hashed_password = Column(String)
    created_at = Column(DateTime, default=datetime.now)

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
