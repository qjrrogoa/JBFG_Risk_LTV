import os
import gc
import json
import threading
import pandas as pd
from datetime import datetime
from dateutil.relativedelta import relativedelta
import bcrypt
from sqlalchemy import text, inspect, func
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session
from database import (
    SessionLocal,
    LtvStandard,
    LtvLog,
    User,
    RegionAuctionRecord,
    SignalCache,
    engine,
    ensure_signal_cache_columns,
)

import sys
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
import llm_advisor

# ==========================================
# 상수 및 전역 설정
# ==========================================
FIXED_MONTHS = [(3, "3개월"), (6, "6개월"), (12, "12개월"), (36, "3년"), (60, "5년")]
DATA_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "data"))
LOG_DIR = os.path.join(DATA_DIR, "logs")
os.makedirs(LOG_DIR, exist_ok=True)

def clear_memory():
    """불필요한 메모리를 명시적으로 정리합니다."""
    gc.collect()

BANK_CONFIG = {
    "광주은행": {
        "ltv_file": os.path.join(DATA_DIR, "LTV_기준(광주은행).csv"),
        "id_vars": ["구분", "담보종류"],
        "usage_col": "담보종류",
        "password": "1234",
        "ltv_col_key": "LTV_광주",
        "region_remap": {},
        "exclude_regions": [],
    },
    "전북은행": {
        "ltv_file": os.path.join(DATA_DIR, "LTV_기준(전북은행).csv"),
        "id_vars": ["구분", "담보종류"],
        "usage_col": "담보종류",
        "password": "1234",
        "ltv_col_key": "LTV_전북",
        "region_remap": {
            "광주": "광역시", "대구": "광역시", "울산": "광역시", "부산": "광역시",
        },
        "exclude_regions": ["시지역", "군이하"],
    },
}

REGIONS_ALL = [
    "서울", "인천", "경기", "부산", "대구", "대전", "광주", "울산",
    "전북", "전남", "경북", "경남", "제주", "충남", "충북", "강원", "세종",
]

REGION_COL_MAP = {
    "서울특별시": "서울", "인천광역시": "인천", "경기도": "경기",
    "광주광역시": "광주", "전라남도": "전남", "전라북도": "전북",
    "부산광역시": "부산", "대전광역시": "대전", "대구광역시": "대구",
    "울산광역시": "울산", "세종특별자치시": "세종", "충청북도": "충북",
    "충청남도": "충남", "경상북도": "경북", "경상남도": "경남",
    "제주특별자치도": "제주", "강원도": "강원",
    **{v: v for v in ["서울", "인천", "경기", "광주", "전남", "전북", "부산",
                       "대전", "대구", "울산", "세종", "충북", "충남", "경북",
                       "경남", "제주", "강원"]},
}


# ==========================================
# 인증 및 사용자 관리
# ==========================================
def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode('utf-8'), bcrypt.gensalt()).decode('utf-8')

def verify_password(password: str, hashed_password: str) -> bool:
    return bcrypt.checkpw(password.encode('utf-8'), hashed_password.encode('utf-8'))

def create_user(db: Session, bank_name: str, username: str, password: str) -> dict:
    try:
        # 중복 체크
        if check_username_exists(db, bank_name, username):
            return {"ok": False, "message": f"{bank_name}에 이미 존재하는 사용자 이름입니다."}
        
        new_user = User(
            bank_name=bank_name,
            username=username,
            hashed_password=hash_password(password)
        )
        db.add(new_user)
        db.commit()
        return {"ok": True, "message": "회원가입이 완료되었습니다."}
    except Exception as e:
        db.rollback()
        return {"ok": False, "message": f"오류 발생: {e}"}

def check_username_exists(db: Session, bank_name: str, username: str) -> bool:
    # 해당 은행 내에서 아이디 중복 확인
    exists = db.query(User.id).filter(
        User.bank_name == bank_name,
        User.username == username
    ).first() is not None
    return exists

def verify_login(db: Session, bank_name: str, username: str, password: str) -> dict:
    user = db.query(User).filter(User.username == username, User.bank_name == bank_name).first()
    if not user:
        return {"ok": False, "message": "사용자를 찾을 수 없거나 은행이 일치하지 않습니다."}
    
    if verify_password(password, user.hashed_password):
        return {"ok": True, "bank": user.bank_name, "username": user.username}
    return {"ok": False, "message": "비밀번호가 올바르지 않습니다."}


# ==========================================
# 데이터 로딩 계층
# ==========================================
def load_ltv_standards(bank_name: str):
    """DB에서 LTV 기준 정보를 읽어와 기존 CSV와 같은 Wide Format DataFrame으로 반환합니다."""
    db: Session = SessionLocal()
    try:
        query = db.query(LtvStandard).filter(LtvStandard.bank_name == bank_name)
        records = query.all()
        if not records:
            return None
        
        # 리스트를 딕셔너리 리스트로 변환
        data = []
        for r in records:
            data.append({
                "적용시작일": r.effective_date.strftime("%Y-%m-%d"),
                "구분": r.category,
                "담보종류": r.usage_type,
                "region": r.region,
                "ltv_value": r.ltv_value
            })
        
        df_long = pd.DataFrame(data)
        
        # Long -> Wide 변환 (구분, 담보종류, 적용시작일 기준)
        df_wide = df_long.pivot_table(
            index=["적용시작일", "구분", "담보종류"],
            columns="region",
            values="ltv_value"
        )
        
        # 적용시작일 순으로 정렬 후, 구분/담보종류별로 직전 값들을 채워넣음 (Forward Fill)
        # 이렇게 하면 특정 날짜에 특정 지역만 바뀌어도 다른 지역 값들이 유지됨
        df_wide = df_wide.groupby(["구분", "담보종류"]).ffill().reset_index()
        
        # 컬럼 순서 정렬 (적용시작일 우선)
        df_wide = df_wide.sort_values(["적용시작일", "구분", "담보종류"])
        
        return df_wide
    except Exception as e:
        print(f"Error loading LTV from DB: {e}")
        return None
    finally:
        db.close()


def map_usage_to_config(usage):
    if not isinstance(usage, str):
        return str(usage)
    if usage in ["연립주택", "연립"]:
        return "연립"
    if usage in ["병원", "의료시설"]:
        return "의료시설"
    if "오피스텔" in usage:
        return "오피스텔"
    if "나대지" in usage or usage == "대지":
        return "대지"
    return usage


_REGION_RECORD_COLUMNS = [
    "사건번호",
    "용도",
    "LTV_광주",
    "LTV_전북",
    "시도",
    "시군구",
    "소재지",
    "감정가",
    "최저가",
    "결과",
    "낙찰가",
    "낙찰율",
    "매각일",
    "분기",
    "기간구분",
    "region_file",
]


def _load_region_records_from_db(region_file: str | None = None) -> pd.DataFrame:
    query = [
        RegionAuctionRecord.case_number.label("사건번호"),
        RegionAuctionRecord.usage.label("용도"),
        RegionAuctionRecord.ltv_gwangju.label("LTV_광주"),
        RegionAuctionRecord.ltv_jeonbuk.label("LTV_전북"),
        RegionAuctionRecord.province.label("시도"),
        RegionAuctionRecord.district.label("시군구"),
        RegionAuctionRecord.address.label("소재지"),
        RegionAuctionRecord.appraised_value.label("감정가"),
        RegionAuctionRecord.min_price.label("최저가"),
        RegionAuctionRecord.result.label("결과"),
        RegionAuctionRecord.winning_price.label("낙찰가"),
        RegionAuctionRecord.winning_rate.label("낙찰율"),
        RegionAuctionRecord.auction_date.label("매각일"),
        RegionAuctionRecord.quarter.label("분기"),
        RegionAuctionRecord.period_type.label("기간구분"),
        RegionAuctionRecord.region_file.label("region_file"),
    ]

    db: Session = SessionLocal()
    try:
        db_query = db.query(*query).order_by(RegionAuctionRecord.auction_date.asc(), RegionAuctionRecord.id.asc())
        if region_file:
            db_query = db_query.filter(RegionAuctionRecord.region_file == region_file)

        rows = db_query.all()
        if not rows:
            return pd.DataFrame(columns=_REGION_RECORD_COLUMNS)

        df = pd.DataFrame(rows, columns=_REGION_RECORD_COLUMNS)
        df["매각일"] = pd.to_datetime(df["매각일"])
        return df
    except Exception as e:
        print(f"[Warn] load region records from DB failed: {e}")
        return pd.DataFrame(columns=_REGION_RECORD_COLUMNS)
    finally:
        db.close()


def _prepare_region_df(bank_name: str, df: pd.DataFrame, std_melted: pd.DataFrame = None, selected_dt: datetime | None = None) -> pd.DataFrame:
    if df.empty:
        return df

    cfg = BANK_CONFIG[bank_name]
    df = df.copy()

    ltv_col_key = cfg["ltv_col_key"]
    if ltv_col_key in df.columns:
        df["분석용도"] = df[ltv_col_key]
    else:
        df["분석용도"] = df["용도"].apply(map_usage_to_config)

    df["_LTV지역구분"] = df["시도"].map(REGION_COL_MAP).fillna("경기")

    region_remap = cfg.get("region_remap", {})
    if region_remap:
        df["_LTV지역구분"] = df["_LTV지역구분"].replace(region_remap)

    if bank_name == "전북은행":
        mask_jb = df["시도"].isin(["전북", "전라북도"])
        df.loc[mask_jb & df["시군구"].str.contains("전주", na=False), "_LTV지역구분"] = "전주"
        df.loc[mask_jb & df["시군구"].str.contains("군산", na=False), "_LTV지역구분"] = "군산"
        df.loc[mask_jb & df["시군구"].str.contains("익산", na=False), "_LTV지역구분"] = "익산"

    if std_melted is not None and not std_melted.empty:
        usage_col = cfg["usage_col"]
        df = df.rename(columns={"분석용도": usage_col})
        df = df.sort_values("매각일")

        valid_regions = std_melted["_LTV지역구분"].unique().tolist()
        df = df[df["_LTV지역구분"].isin(valid_regions)].copy()

        if not df.empty:
            df = pd.merge_asof(
                df,
                std_melted,
                left_on="매각일",
                right_on="적용시작일",
                by=[usage_col, "_LTV지역구분"],
                direction="backward"
            )
            df["적용LTV"] = df["적용LTV"].fillna(80.0)

        df = df.rename(columns={usage_col: "분석용도"})
    else:
        df["적용LTV"] = 80.0

    if selected_dt is not None:
        df = df[df["매각일"] <= selected_dt].copy()

    if "결과" in df.columns:
        df = df[df["결과"].astype(str).str.contains("낙찰|매각", na=False)].copy()

    return df


def get_processed_region_df(bank_name: str, region_fname: str, std_melted: pd.DataFrame = None, selected_dt: datetime | None = None) -> pd.DataFrame:
    """특정 지역의 데이터를 로드하고 전처리합니다."""
    region_key = os.path.splitext(region_fname)[0] if region_fname else ""
    if region_key in REGIONS_ALL:
        raw_df = _load_region_records_from_db(region_key)
    else:
        raw_df = _load_region_records_from_db()

    if raw_df.empty:
        return pd.DataFrame()

    df = _prepare_region_df(bank_name, raw_df, std_melted=std_melted, selected_dt=selected_dt)

    # 파일 기반 요청이 아닌 임의 지역명(예: 전주) 전달 시 세부 지역 필터링
    if region_key and region_key not in REGIONS_ALL:
        df = df[df["_LTV지역구분"] == region_key]

    return df


def get_global_winning_df(bank_name: str, std_melted: pd.DataFrame = None, selected_dt: datetime | None = None) -> pd.DataFrame:
    """은행별 전체 매각 데이터를 로드합니다 (메모리 주의!)."""
    raw_df = _load_region_records_from_db()
    if raw_df.empty:
        raise FileNotFoundError("데이터 파일을 찾을 수 없습니다.")

    df = _prepare_region_df(bank_name, raw_df, std_melted=std_melted, selected_dt=selected_dt)
    return df


def _get_region_expr(bank_name: str) -> str:
    base_case = """
        CASE
            WHEN r.province = '서울특별시' THEN '서울'
            WHEN r.province = '인천광역시' THEN '인천'
            WHEN r.province = '경기도' THEN '경기'
            WHEN r.province = '광주광역시' THEN '광주'
            WHEN r.province = '전라남도' THEN '전남'
            WHEN r.province = '전라북도' THEN '전북'
            WHEN r.province = '부산광역시' THEN '부산'
            WHEN r.province = '대전광역시' THEN '대전'
            WHEN r.province = '대구광역시' THEN '대구'
            WHEN r.province = '울산광역시' THEN '울산'
            WHEN r.province = '세종특별자치시' THEN '세종'
            WHEN r.province = '충청북도' THEN '충북'
            WHEN r.province = '충청남도' THEN '충남'
            WHEN r.province = '경상북도' THEN '경북'
            WHEN r.province = '경상남도' THEN '경남'
            WHEN r.province = '제주특별자치도' THEN '제주'
            WHEN r.province = '강원도' THEN '강원'
            ELSE COALESCE(NULLIF(BTRIM(r.province), ''), '경기')
        END
    """

    if bank_name == "전북은행":
        return f"""
            CASE
                WHEN ({base_case}) IN ('광주', '대구', '울산', '부산') THEN '광역시'
                WHEN r.province IN ('전북', '전라북도') AND COALESCE(r.district, '') LIKE '%%전주%%' THEN '전주'
                WHEN r.province IN ('전북', '전라북도') AND COALESCE(r.district, '') LIKE '%%군산%%' THEN '군산'
                WHEN r.province IN ('전북', '전라북도') AND COALESCE(r.district, '') LIKE '%%익산%%' THEN '익산'
                ELSE ({base_case})
            END
        """

    return base_case


_REGION_AUCTION_COLUMNS = None


def _get_region_auction_columns() -> set[str]:
    global _REGION_AUCTION_COLUMNS
    if _REGION_AUCTION_COLUMNS is None:
        insp = inspect(engine)
        _REGION_AUCTION_COLUMNS = {c["name"] for c in insp.get_columns("region_auction_records")}
    return _REGION_AUCTION_COLUMNS


def _has_region_col(name: str) -> bool:
    return name in _get_region_auction_columns()


def _get_usage_expr(bank_name: str) -> str:
    usage_only_expr = """
        CASE
            WHEN r.usage IN ('연립주택', '연립') THEN '연립'
            WHEN r.usage IN ('병원', '의료시설') THEN '의료시설'
            WHEN r.usage LIKE '%%오피스텔%%' THEN '오피스텔'
            WHEN r.usage = '대지' OR r.usage LIKE '%%나대지%%' THEN '대지'
            ELSE NULLIF(BTRIM(r.usage), '')
        END
    """

    if bank_name == "전북은행" and _has_region_col("ltv_전북"):
        return f"""
            COALESCE(
                NULLIF(BTRIM(CAST(r.ltv_전북 AS TEXT)), ''),
                {usage_only_expr}
            )
        """
    if bank_name == "전북은행" and _has_region_col("ltv_jeonbuk"):
        return f"""
            COALESCE(
                NULLIF(BTRIM(CAST(r.ltv_jeonbuk AS TEXT)), ''),
                {usage_only_expr}
            )
        """

    if _has_region_col("ltv_gwangju"):
        return f"""
            COALESCE(
                NULLIF(BTRIM(CAST(r.ltv_gwangju AS TEXT)), ''),
                {usage_only_expr}
            )
        """

    return usage_only_expr


def _run_aggregated_sql(bank_name: str, selected_dt: datetime | None, outlier_thresh: float):
    cfg = BANK_CONFIG[bank_name]
    region_expr = _get_region_expr(bank_name)
    usage_expr = _get_usage_expr(bank_name)
    selected_filter = "AND r.auction_date <= :selected_dt" if selected_dt is not None else ""

    exclude_clause = ""
    exclude_regions = cfg.get("exclude_regions", [])
    if exclude_regions:
        excluded = ", ".join([f"'{x}'" for x in exclude_regions])
        exclude_clause = f"AND mapped_region NOT IN ({excluded})"

    sql = f"""
    WITH mapped AS (
        SELECT
            {region_expr} AS mapped_region,
            {usage_expr} AS usage_mapped,
            r.auction_date AS auction_date,
            r.winning_rate AS winning_rate
        FROM region_auction_records r
        WHERE (r.result ILIKE '%%낙찰%%' OR r.result ILIKE '%%매각%%')
        {selected_filter}
    ),
    with_last AS (
        SELECT
            m.*,
            CASE
                WHEN :selected_dt IS NULL THEN MAX(m.auction_date) OVER (PARTITION BY m.mapped_region)
                ELSE :selected_dt
            END AS region_last_date,
            MAX(m.auction_date) OVER (PARTITION BY m.mapped_region, m.usage_mapped) AS usage_last_date
        FROM mapped m
    ),
    valid_usage AS (
        SELECT DISTINCT
            s.usage_type,
            s.region,
            MAX(s.category) AS category
        FROM ltv_standards s
        WHERE s.bank_name = :bank_name
        GROUP BY s.usage_type, s.region
    ),
    with_std AS (
        SELECT
            w.mapped_region,
            w.usage_mapped,
            w.auction_date,
            w.winning_rate,
            w.region_last_date,
            s_cat.category,
            COALESCE(s_std.ltv_value, 80.0) AS ltv_value
        FROM with_last w
        JOIN valid_usage s_cat
            ON s_cat.usage_type = w.usage_mapped
            AND s_cat.region = w.mapped_region
        LEFT JOIN LATERAL (
            SELECT s.ltv_value
            FROM ltv_standards s
            WHERE s.bank_name = :bank_name
                AND s.usage_type = w.usage_mapped
                AND s.region = w.mapped_region
                AND s.effective_date <= w.usage_last_date
            ORDER BY s.effective_date DESC
            LIMIT 1
        ) s_std ON TRUE
        WHERE w.usage_mapped IS NOT NULL
          AND w.region_last_date IS NOT NULL
          {exclude_clause}
    )
    SELECT
        mapped_region AS region,
        usage_mapped AS usage,
        category,
        COALESCE(ltv_value, 80.0) AS ltv_val,
        AVG(CASE
            WHEN auction_date > region_last_date - INTERVAL '3 months'
                 AND auction_date <= region_last_date
                 AND winning_rate IS NOT NULL
                 AND ABS(winning_rate - COALESCE(ltv_value, 80.0))
                     <= COALESCE(ltv_value, 80.0) * :outlier_thresh
            THEN winning_rate END
        ) AS avg_3,
        COUNT(CASE
            WHEN auction_date > region_last_date - INTERVAL '3 months'
                 AND auction_date <= region_last_date
                 AND winning_rate IS NOT NULL
                 AND ABS(winning_rate - COALESCE(ltv_value, 80.0))
                     <= COALESCE(ltv_value, 80.0) * :outlier_thresh
            THEN 1 END) AS cnt_3,
        AVG(CASE
            WHEN auction_date > region_last_date - INTERVAL '6 months'
                 AND auction_date <= region_last_date
                 AND winning_rate IS NOT NULL
                 AND ABS(winning_rate - COALESCE(ltv_value, 80.0))
                     <= COALESCE(ltv_value, 80.0) * :outlier_thresh
            THEN winning_rate END
        ) AS avg_6,
        COUNT(CASE
            WHEN auction_date > region_last_date - INTERVAL '6 months'
                 AND auction_date <= region_last_date
                 AND winning_rate IS NOT NULL
                 AND ABS(winning_rate - COALESCE(ltv_value, 80.0))
                     <= COALESCE(ltv_value, 80.0) * :outlier_thresh
            THEN 1 END) AS cnt_6,
        AVG(CASE
            WHEN auction_date > region_last_date - INTERVAL '12 months'
                 AND auction_date <= region_last_date
                 AND winning_rate IS NOT NULL
                 AND ABS(winning_rate - COALESCE(ltv_value, 80.0))
                     <= COALESCE(ltv_value, 80.0) * :outlier_thresh
            THEN winning_rate END
        ) AS avg_12,
        COUNT(CASE
            WHEN auction_date > region_last_date - INTERVAL '12 months'
                 AND auction_date <= region_last_date
                 AND winning_rate IS NOT NULL
                 AND ABS(winning_rate - COALESCE(ltv_value, 80.0))
                     <= COALESCE(ltv_value, 80.0) * :outlier_thresh
            THEN 1 END) AS cnt_12,
        AVG(CASE
            WHEN auction_date > region_last_date - INTERVAL '36 months'
                 AND auction_date <= region_last_date
                 AND winning_rate IS NOT NULL
                 AND ABS(winning_rate - COALESCE(ltv_value, 80.0))
                     <= COALESCE(ltv_value, 80.0) * :outlier_thresh
            THEN winning_rate END
        ) AS avg_36,
        COUNT(CASE
            WHEN auction_date > region_last_date - INTERVAL '36 months'
                 AND auction_date <= region_last_date
                 AND winning_rate IS NOT NULL
                 AND ABS(winning_rate - COALESCE(ltv_value, 80.0))
                     <= COALESCE(ltv_value, 80.0) * :outlier_thresh
            THEN 1 END) AS cnt_36,
        AVG(CASE
            WHEN auction_date > region_last_date - INTERVAL '60 months'
                 AND auction_date <= region_last_date
                 AND winning_rate IS NOT NULL
                 AND ABS(winning_rate - COALESCE(ltv_value, 80.0))
                     <= COALESCE(ltv_value, 80.0) * :outlier_thresh
            THEN winning_rate END
        ) AS avg_60,
        COUNT(CASE
            WHEN auction_date > region_last_date - INTERVAL '60 months'
                 AND auction_date <= region_last_date
                 AND winning_rate IS NOT NULL
                 AND ABS(winning_rate - COALESCE(ltv_value, 80.0))
                     <= COALESCE(ltv_value, 80.0) * :outlier_thresh
            THEN 1 END) AS cnt_60
    FROM with_std
    GROUP BY mapped_region, usage_mapped, category, ltv_value
    ORDER BY mapped_region, category, usage_mapped;
    """

    db: Session = SessionLocal()
    try:
        params = {
            "bank_name": bank_name,
            "outlier_thresh": float(outlier_thresh),
            "selected_dt": None,
        }
        if selected_dt is not None:
            params["selected_dt"] = pd.to_datetime(selected_dt)

        rows = db.execute(text(sql), params).fetchall()
        return rows
    finally:
        db.close()


# ==========================================
# 통계 집계 로직
# ==========================================
def calculate_metrics(source_df, target_usage, ltv, current_date, outlier_thresh=0.3):
    sub_df = source_df[source_df["분석용도"] == target_usage].copy()
    limit = ltv * outlier_thresh
    sub_df = sub_df[abs(sub_df["낙찰율"] - ltv) <= limit]

    results = {"avg": {}, "count": {}}
    for m in [3, 6, 12, 36, 60]:
        start_date = current_date - relativedelta(months=m)
        m_filtered = sub_df[(sub_df["매각일"] > start_date) & (sub_df["매각일"] <= current_date)]
        results["avg"][m] = float(m_filtered["낙찰율"].mean()) if not m_filtered.empty else None
        results["count"][m] = int(len(m_filtered))
    return results


def classify_period(avg_value, ltv, count_value, min_required=1):
    if avg_value is None or count_value < min_required:
        return "gray"
    abs_gap = abs(avg_value - ltv)
    if abs_gap > 10:
        return "red"
    if abs_gap >= 5:
        return "yellow"
    return "green"


def check_signal_logic(metrics, ltv, min_val=1):
    if metrics is None:
        return None
    avg12, avg6, avg3 = metrics["avg"][12], metrics["avg"][6], metrics["avg"][3]
    cnt3 = metrics["count"][3]
    cnt6 = metrics["count"][6]
    cnt12 = metrics["count"][12]
    cnt36 = metrics["count"][36]

    if not all(v is not None for v in [avg12, avg6, avg3]):
        return None

    d12, d6, d3 = avg12 - ltv, avg6 - ltv, avg3 - ltv
    g12, g6, g3 = round(abs(d12), 1), round(abs(d6), 1), round(abs(d3), 1)

    if cnt3 < 10:
        return None

    weighted_gap = (g3 * 5 + g6 * 3 + g12 * 2) / 10.0
    is_red = weighted_gap >= 10
    is_yellow = (weighted_gap >= 5) and not is_red
    if not (is_red or is_yellow):
        return None

    is_pos = all(d > 0 for d in [d12, d6, d3])
    is_neg = all(d < 0 for d in [d12, d6, d3])
    is_golden = avg3 > avg6
    is_dead = avg3 < avg6

    direction = "▲" if (is_pos and is_golden) else ("▼" if (is_neg and is_dead) else None)
    if not direction:
        return None

    suggested_ltv = round(avg12 if direction == "▲" else avg3, 1)
    adjust_delta = round(suggested_ltv - ltv, 1)

    return {
        "direction": direction,
        "tone": "red" if is_red else "yellow",
        "gap3": round(avg3 - ltv, 2),
        "suggested_ltv": suggested_ltv,
        "adjust_delta": adjust_delta,
        "reason": f"3/6/12개월 가중평균 낙찰가율이 기존 LTV와 {'10%p 이상' if is_red else '5%p 이상'} 차이, 건수 충족, {'상향' if direction == '▲' else '하향'} 추세 확인",
        "counts": {"3": cnt3, "6": cnt6, "12": cnt12, "36": cnt36},
    }


_ai_task_lock = threading.Lock()
_ai_pending_tasks: set[str] = set()
_ai_task_events: dict[str, threading.Event] = {}
_AI_ADVICE_CONCURRENCY = max(1, int(os.getenv("LTV_AI_CONCURRENCY", "2")))
_ai_advice_semaphore = threading.Semaphore(_AI_ADVICE_CONCURRENCY)


def _resolve_base_ym(base_date: str | None) -> str:
    if base_date:
        try:
            return pd.to_datetime(base_date).to_period("M").strftime("%Y%m")
        except (TypeError, ValueError):
            return datetime.now().strftime("%Y%m")
    return datetime.now().strftime("%Y%m")


def _resolve_signal_base_ym(bank_name: str, base_date: str | None = None) -> str:
    """요청 기준월이 없으면 signal_cache 최신월 또는 경매 데이터 최신월을 사용."""
    if base_date:
        return _resolve_base_ym(base_date)

    db = SessionLocal()
    try:
        row = (
            db.query(SignalCache.base_ym)
            .filter(SignalCache.bank_name == bank_name)
                .order_by(SignalCache.base_ym.desc())
                .first()
        )
        if row and row[0]:
            return row[0]

        latest_auction = db.query(func.max(RegionAuctionRecord.auction_date)).scalar()
        if latest_auction:
            return pd.to_datetime(latest_auction).to_period("M").strftime("%Y%m")
    except Exception:
        return datetime.now().strftime("%Y%m")
    finally:
        db.close()

    return datetime.now().strftime("%Y%m")


def _is_historical_base_month(base_date: str | None) -> bool:
    """기준월이 현재 달보다 이전이면 과거 조회로 간주."""
    if not base_date:
        return False
    try:
        target = pd.to_datetime(base_date).to_period("M")
        return target < pd.Timestamp.now().to_period("M")
    except Exception:
        return False


def _to_num(v):
    try:
        value = float(v)
    except (TypeError, ValueError):
        return None
    return None if pd.isna(value) else value


def _month_end_str(base_ym: str | None) -> str:
    try:
        period = pd.to_datetime(base_ym).to_period("M")
        return period.to_timestamp("M").strftime("%Y-%m-%d")
    except Exception:
        return datetime.now().strftime("%Y-%m-%d")


def _query_signal_cache_rows(bank_name: str, base_ym: str):
    db = SessionLocal()
    try:
        ensure_signal_cache_columns()
        return (
            db.query(SignalCache)
            .filter(SignalCache.bank_name == bank_name, SignalCache.base_ym == base_ym)
            .order_by(SignalCache.region.asc(), SignalCache.category.asc(), SignalCache.usage_type.asc())
            .all()
        )
    finally:
        db.close()


def _row_to_matrix_item(row):
    ltv_val = _to_num(row.ltv_value) or 80.0
    metrics = {
        3: _to_num(row.avg_3),
        6: _to_num(row.avg_6),
        12: _to_num(row.avg_12),
        36: _to_num(row.avg_36),
        60: _to_num(row.avg_60),
    }
    counts = {
        3: int(row.cnt_3 or 0),
        6: int(row.cnt_6 or 0),
        12: int(row.cnt_12 or 0),
        36: int(row.cnt_36 or 0),
        60: int(row.cnt_60 or 0),
    }
    status = {
        "3개월": classify_period(metrics[3], ltv_val, counts[3]),
        "6개월": classify_period(metrics[6], ltv_val, counts[6]),
        "12개월": classify_period(metrics[12], ltv_val, counts[12]),
        "3년": classify_period(metrics[36], ltv_val, counts[36]),
        "5년": classify_period(metrics[60], ltv_val, counts[60]),
    }

    return {
        "row_id": row.id,
        "지역": row.region,
        "대분류": row.category,
        "용도": row.usage_type,
        "LTV": ltv_val,
        "signal_tone": row.signal_tone,
        "signal_direction": row.signal_direction,
        "signal_reason": row.reason,
        "met": {
            "avg": {str(k): v for k, v in metrics.items()},
            "count": {str(k): v for k, v in counts.items()},
        },
        "3개월": status["3개월"],
        "6개월": status["6개월"],
        "12개월": status["12개월"],
        "3년": status["3년"],
        "5년": status["5년"],
        "3개월_count": counts[3],
        "6개월_count": counts[6],
        "12개월_count": counts[12],
        "3년_count": counts[36],
        "5년_count": counts[60],
        "base_ym": row.base_ym,
    }


def _extract_metric_maps_from_matrix_row(row: dict):
    met = row.get("met") if isinstance(row, dict) else None
    avg_src = met.get("avg", {}) if isinstance(met, dict) else {}
    cnt_src = met.get("count", {}) if isinstance(met, dict) else {}

    def avg_value(month_key: str, label: str):
        if isinstance(avg_src, dict):
            value = avg_src.get(month_key)
            if value is None:
                value = avg_src.get(int(month_key))
            numeric = _to_num(value)
            if numeric is not None:
                return numeric
        return _to_num(row.get(label))

    def cnt_value(month_key: str, label: str):
        if isinstance(cnt_src, dict):
            value = cnt_src.get(month_key)
            if value is None:
                value = cnt_src.get(int(month_key))
            try:
                return int(value or 0)
            except (TypeError, ValueError):
                pass
        try:
            return int(row.get(f"{label}_count") or 0)
        except (TypeError, ValueError):
            return 0

    avg_map = {
        "3": avg_value("3", "3개월"),
        "6": avg_value("6", "6개월"),
        "12": avg_value("12", "12개월"),
        "36": avg_value("36", "3년"),
        "60": avg_value("60", "5년"),
    }
    cnt_map = {
        "3": cnt_value("3", "3개월"),
        "6": cnt_value("6", "6개월"),
        "12": cnt_value("12", "12개월"),
        "36": cnt_value("36", "3년"),
        "60": cnt_value("60", "5년"),
    }
    return avg_map, cnt_map


def _cache_matrix_rows_to_signal_cache(bank_name: str, base_ym: str, matrix_df, raw_urgent_list: list[dict]):
    if matrix_df is None or matrix_df.empty:
        return 0

    raw_signal_lookup = {}
    for item in raw_urgent_list:
        signal = item.get("signal") or {}
        key = (
            item.get("reg"),
            item.get("category"),
            item.get("usage_type"),
            round(_to_num(item.get("ltv_val")) or 80.0, 4),
        )
        raw_signal_lookup[key] = signal

    now = datetime.now()
    payloads = []
    for row in matrix_df.to_dict(orient="records"):
        region = row.get("지역")
        category = row.get("대분류")
        usage_type = row.get("용도")
        ltv_value = _to_num(row.get("LTV")) or 80.0
        key = (region, category, usage_type, round(float(ltv_value), 4))
        signal = raw_signal_lookup.get(key, {})

        avg_map, cnt_map = _extract_metric_maps_from_matrix_row(row)

        payloads.append({
            "bank_name": bank_name,
            "base_ym": base_ym,
            "region": region,
            "category": category,
            "usage_type": usage_type,
            "ltv_value": ltv_value,
            "signal_tone": signal.get("tone"),
            "signal_direction": signal.get("direction"),
            "suggested_ltv": _to_num(signal.get("suggested_ltv")),
            "adjust_delta": _to_num(signal.get("adjust_delta")),
            "gap3": _to_num(signal.get("gap3")),
            "avg_3": _to_num(avg_map.get("3")),
            "avg_6": _to_num(avg_map.get("6")),
            "avg_12": _to_num(avg_map.get("12")),
            "avg_36": _to_num(avg_map.get("36")),
            "avg_60": _to_num(avg_map.get("60")),
            "cnt_3": int(cnt_map.get("3") or 0),
            "cnt_6": int(cnt_map.get("6") or 0),
            "cnt_12": int(cnt_map.get("12") or 0),
            "cnt_36": int(cnt_map.get("36") or 0),
            "cnt_60": int(cnt_map.get("60") or 0),
            "metric_blob": json.dumps({"avg": avg_map, "count": cnt_map}, ensure_ascii=False),
            "reason": signal.get("reason"),
            "is_modified": False,
            "updated_at": now,
        })

    if not payloads:
        return 0

    for payload in payloads:
        payload.setdefault("created_at", now)

    db = SessionLocal()
    try:
        stmt = insert(SignalCache).values(payloads)
        excluded = stmt.excluded
        update_fields = {
            "ltv_value": excluded.ltv_value,
            "signal_tone": excluded.signal_tone,
            "signal_direction": excluded.signal_direction,
            "suggested_ltv": excluded.suggested_ltv,
            "adjust_delta": excluded.adjust_delta,
            "gap3": excluded.gap3,
            "avg_3": excluded.avg_3,
            "avg_6": excluded.avg_6,
            "avg_12": excluded.avg_12,
            "avg_36": excluded.avg_36,
            "avg_60": excluded.avg_60,
            "cnt_3": excluded.cnt_3,
            "cnt_6": excluded.cnt_6,
            "cnt_12": excluded.cnt_12,
            "cnt_36": excluded.cnt_36,
            "cnt_60": excluded.cnt_60,
            "metric_blob": excluded.metric_blob,
            "reason": excluded.reason,
            "is_modified": excluded.is_modified,
            "updated_at": excluded.updated_at,
        }
        upsert_stmt = stmt.on_conflict_do_update(
            index_elements=[
                "bank_name",
                "base_ym",
                "region",
                "category",
                "usage_type",
                "ltv_value",
            ],
            set_=update_fields,
        )
        db.execute(upsert_stmt)
        db.commit()
        return len(payloads)
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def _build_signal_cache_from_aggregated(bank_name: str, base_ym: str, base_date: str | None = None):
    cache_base_dt = _month_end_str(base_ym if base_date is None else base_date)
    matrix_df, urgent_cards = get_aggregated_data(
        bank_name,
        cache_base_dt,
        outlier_thresh=0.3,
        min_cnt=1,
    )
    _cache_matrix_rows_to_signal_cache(bank_name, base_ym, matrix_df, urgent_cards)
    return _query_signal_cache_rows(bank_name, base_ym), base_ym


def _signal_cache_needs_metric_refresh(rows) -> bool:
    if not rows:
        return False

    checks = (
        ("avg_3", "cnt_3"),
        ("avg_6", "cnt_6"),
        ("avg_12", "cnt_12"),
        ("avg_36", "cnt_36"),
        ("avg_60", "cnt_60"),
    )
    for row in rows:
        for avg_attr, cnt_attr in checks:
            if int(getattr(row, cnt_attr, 0) or 0) > 0 and _to_num(getattr(row, avg_attr, None)) is None:
                return True
    return False


def get_matrix_cache_rows(bank_name: str, base_date: str | None = None):
    base_ym = _resolve_signal_base_ym(bank_name, base_date)

    rows = _query_signal_cache_rows(bank_name, base_ym)
    if not rows or _signal_cache_needs_metric_refresh(rows):
        rows, base_ym = _build_signal_cache_from_aggregated(bank_name, base_ym, base_date)
    if not rows:
        return []

    return [_row_to_matrix_item(row) for row in rows]

def get_aggregated_data(bank_name: str, base_date: str | None = None,
                        outlier_thresh: float = 0.3, min_cnt: int = 1):
    """전체 지역을 한 번에 로드해 집계합니다."""
    selected_dt = pd.to_datetime(base_date) if base_date else None
    matrix_rows, urgent_cards = [], []

    rows = _run_aggregated_sql(bank_name, selected_dt, outlier_thresh)
    if not rows:
        return pd.DataFrame(), []

    for row in rows:
        m = row._mapping
        ltv_val = float(m.get("ltv_val", 80.0) or 80.0)
        metrics = {
            "avg": {
                3: m.get("avg_3"),
                6: m.get("avg_6"),
                12: m.get("avg_12"),
                36: m.get("avg_36"),
                60: m.get("avg_60"),
            },
            "count": {
                3: int(m.get("cnt_3") or 0),
                6: int(m.get("cnt_6") or 0),
                12: int(m.get("cnt_12") or 0),
                36: int(m.get("cnt_36") or 0),
                60: int(m.get("cnt_60") or 0),
            },
        }

        met_str = {
            "avg": {str(k): v for k, v in metrics["avg"].items()},
            "count": {str(k): v for k, v in metrics["count"].items()},
        }

        signal = check_signal_logic(metrics, ltv_val, min_cnt)
        if signal:
            urgent_cards.append({
                "reg": m["region"], "category": m["category"], "usage_type": m["usage"],
                "ltv_val": ltv_val, "met": met_str, "signal": signal,
            })

        matrix_rows.append({
            "지역": m["region"], "대분류": m["category"], "용도": m["usage"], "LTV": ltv_val,
            "signal_tone": signal["tone"] if signal else None,
            "met": met_str,
            **{m_lbl: classify_period(metrics["avg"].get(m_num), ltv_val, metrics["count"].get(m_num, 0), min_cnt)
               for m_num, m_lbl in FIXED_MONTHS},
            **{f"{m_lbl}_count": metrics["count"].get(m_num, 0)
               for m_num, m_lbl in FIXED_MONTHS}
        })

    clear_memory()

    if not matrix_rows:
        return pd.DataFrame(), []

    res_df = pd.DataFrame(matrix_rows)
    return res_df, urgent_cards


# ==========================================
# 차트 데이터
# ==========================================
def get_chart_data(bank_name: str, region: str, usage_type: str, base_date: str | None = None):
    cfg = BANK_CONFIG[bank_name]
    selected_dt = pd.to_datetime(base_date) if base_date else None

    ltv_std = load_ltv_standards(bank_name)

    std_melted = None
    if ltv_std is not None:
        if "적용시작일" not in ltv_std.columns:
            ltv_std["적용시작일"] = "2000-01-01"
        ltv_std["적용시작일"] = pd.to_datetime(ltv_std["적용시작일"])
        if selected_dt is not None:
            ltv_std = ltv_std[ltv_std["적용시작일"] <= selected_dt].copy()

        melt_ids = cfg["id_vars"] + ["적용시작일"]
        exclude = cfg.get("exclude_regions", [])
        std_melted = ltv_std.melt(id_vars=melt_ids, var_name="_LTV지역구분", value_name="적용LTV")
        valid_regions = [c for c in ltv_std.columns if c not in melt_ids and c not in exclude]
        std_melted = std_melted[std_melted["_LTV지역구분"].isin(valid_regions)].sort_values("적용시작일")
        std_melted = std_melted[[cfg["usage_col"], "_LTV지역구분", "적용시작일", "적용LTV"]]

    reg_df = get_processed_region_df(bank_name, region, std_melted=std_melted, selected_dt=selected_dt)
    if reg_df.empty: 
        return {"ltv": 80.0, "points": []}

    selected_dt = selected_dt if selected_dt is not None else reg_df["매각일"].max()

    if reg_df.empty:
        return {"ltv": 80.0, "points": []}

    # LTV 값 찾기 (데이터가 없더라도 기준표에서 가져옴)
    target_df = reg_df[reg_df["분석용도"] == usage_type]
    
    if not target_df.empty:
        # 매각일 기준 가장 최근 데이터의 적용LTV 사용
        ltv_val = float(target_df["적용LTV"].iloc[-1])
    else:
        # 데이터가 아예 없는 경우 기준표 직접 조회
        if ltv_std is not None:
            if "적용시작일" not in ltv_std.columns: ltv_std["적용시작일"] = "2000-01-01"
            ltv_std["적용시작일"] = pd.to_datetime(ltv_std["적용시작일"])
            usage_col = cfg["usage_col"]
            # 해당 용도/지역 필터
            relevant = ltv_std[ltv_std[usage_col] == usage_type].copy()
            relevant = relevant[relevant["적용시작일"] <= selected_dt].sort_values("적용시작일")
            if not relevant.empty:
                ltv_val = float(relevant[region].iloc[-1])
            else:
                ltv_val = 80.0
        else:
            ltv_val = 80.0

    # 차트용 서브셋
    limit = ltv_val * 0.3
    sub_df = target_df[abs(target_df["낙찰율"] - ltv_val) <= limit].copy()

    if sub_df.empty:
        return {"ltv": ltv_val, "points": []}

    start_date = selected_dt - relativedelta(years=3)
    chart_df = sub_df[(sub_df["매각일"] >= start_date) & (sub_df["매각일"] <= selected_dt)].copy()
    if chart_df.empty:
        return {"ltv": ltv_val, "points": []}

    chart_df = chart_df.set_index("매각일").sort_index()
    monthly = chart_df.resample("ME")["낙찰율"].mean()
    rolling_3m = monthly.rolling(window=3, min_periods=1).mean()
    rolling_6m = monthly.rolling(window=6, min_periods=1).mean()
    rolling_12m = monthly.rolling(window=12, min_periods=1).mean()

    # 최근 2년만 뷰
    view_start = selected_dt - relativedelta(years=2)
    mask = monthly.index >= view_start
    monthly = monthly.loc[mask]
    rolling_3m = rolling_3m.loc[mask]
    rolling_6m = rolling_6m.loc[mask]
    rolling_12m = rolling_12m.loc[mask]

    points = []
    for dt in monthly.index:
        dt_str = dt.strftime("%Y-%m")
        points.append({
            "month": dt_str,
            "monthly": round(float(monthly.get(dt)), 2) if pd.notna(monthly.get(dt)) else None,
            "ma3": round(float(rolling_3m.get(dt)), 2) if pd.notna(rolling_3m.get(dt)) else None,
            "ma6": round(float(rolling_6m.get(dt)), 2) if pd.notna(rolling_6m.get(dt)) else None,
            "ma12": round(float(rolling_12m.get(dt)), 2) if pd.notna(rolling_12m.get(dt)) else None,
        })

    return {"ltv": ltv_val, "points": points}


# ==========================================
# LTV 기준표 조회 (전체 테이블)
# ==========================================
def get_current_ltv_table(bank_name: str, base_date: str | None = None):
    cfg = BANK_CONFIG.get(bank_name)
    ltv_std = load_ltv_standards(bank_name)
    if ltv_std is None:
        return []
    
    # Versioning column check
    if "적용시작일" not in ltv_std.columns:
        ltv_std["적용시작일"] = "2000-01-01"
    ltv_std["적용시작일"] = pd.to_datetime(ltv_std["적용시작일"])
    
    # base_date가 YYYY-MM 형식이면 해당 월의 말일로 설정하여,
    # 해당 월에 변경된 모든 LTV가 표에 보이도록 함
    if base_date:
        dt = pd.to_datetime(base_date)
        # 해당 월의 마지막 날로 설정
        selected_dt = dt + pd.offsets.MonthEnd(0)
    else:
        selected_dt = datetime.now()
    
    usage_col = cfg["usage_col"]
    id_vars = cfg["id_vars"]
    
    results = []
    # 구분, 담보종류 그룹별로 기준일 이전의 가장 최신 데이터 추출
    for _, group in ltv_std.groupby(id_vars, sort=False):
        # 1. 기준일 이전 기록 찾기
        relevant = group[group["적용시작일"] <= selected_dt].sort_values("적용시작일", ascending=False)
        if not relevant.empty:
            results.append(relevant.iloc[0])
        else:
            # 2. 기준일 이전 기록이 없다면, 가장 과거(혹은 전체 중 가장 최신) 기록이라도 하나 보여줌
            # (데이터 자체가 아예 안 보이는 상황 방지)
            fallback = group.sort_values("적용시작일", ascending=True)
            if not fallback.empty:
                results.append(fallback.iloc[0])
            
    if not results:
        return []
        
    final_df = pd.DataFrame(results).sort_values(id_vars)
    
    # 변경된 항목 감지 (이전 버전과 비교)
    modified_info = []
    for _, row in final_df.iterrows():
        usage = row[usage_col]
        current_date = row["적용시작일"]
        
        # 이전 버전 찾기
        prev = ltv_std[(ltv_std[usage_col] == usage) & (ltv_std["적용시작일"] < current_date)].sort_values("적용시작일", ascending=False)
        
        modified_regions = []
        if not prev.empty:
            p_row = prev.iloc[0]
            for col in ltv_std.columns:
                if col not in id_vars and col != "적용시작일":
                    curr_val = row[col]
                    prev_val = p_row[col]
                    
                    is_curr_na = pd.isna(curr_val)
                    is_prev_na = pd.isna(prev_val)

                    if not is_curr_na and not is_prev_na:
                        if float(curr_val) != float(prev_val):
                            modified_regions.append(col)
                    elif is_curr_na != is_prev_na:
                        # 한쪽만 NaN인 경우 변경으로 간주
                        modified_regions.append(col)
        
        row_dict = row.to_dict()
        row_dict["modified_regions"] = modified_regions
        row_dict["적용시작일"] = row["적용시작일"].strftime("%Y-%m-%d")

        # JSON 호환을 위해 NaN을 None으로 최종 변환 (numpy/pandas scalar 대응)
        cleaned_row = {}
        for k, v in row_dict.items():
            if k == "modified_regions":
                cleaned_row[k] = v
                continue
                
            if pd.isna(v):
                cleaned_row[k] = None
            elif isinstance(v, (float, int)):
                cleaned_row[k] = float(v)
            else:
                cleaned_row[k] = v
        modified_info.append(cleaned_row)


    return modified_info


# ==========================================
# 로그 기록
# ==========================================
def write_ltv_log(bank, region, usage, old_ltv, new_ltv, effective_date, log_suffix=""):
    """LTV 변경 로그를 DB에 기록합니다."""
    db: Session = SessionLocal()
    try:
        new_log = LtvLog(
            bank_name=bank,
            region=region,
            usage_type=usage,
            old_value=float(old_ltv),
            new_value=float(new_ltv),
            effective_date=str(effective_date),
            log_suffix=log_suffix
        )
        db.add(new_log)
        db.commit()
    except Exception as e:
        print(f"Error writing DB log: {e}")
        db.rollback()
    finally:
        db.close()


def get_ltv_logs(bank_name: str, limit: int = 100):
    """DB에서 최근 LTV 변경 로그를 가져옵니다."""
    db: Session = SessionLocal()
    try:
        logs = db.query(LtvLog).filter(LtvLog.bank_name == bank_name).order_by(LtvLog.created_at.desc()).limit(limit).all()
        return [
            {
                "id": l.id,
                "created_at": l.created_at.strftime("%Y-%m-%d %H:%M:%S"),
                "bank": l.bank_name,
                "region": l.region,
                "usage": l.usage_type,
                "old_ltv": l.old_value,
                "new_ltv": l.new_value,
                "effective_date": l.effective_date,
                "suffix": l.log_suffix
            }
            for l in logs
        ]
    finally:
        db.close()


# ==========================================
# LTV 저장
# ==========================================
def save_ltv(bank_name: str, region: str, usage: str, new_ltv: float, base_date: str | None = None) -> dict:
    cfg = BANK_CONFIG.get(bank_name)
    if cfg is None:
        return {"ok": False, "message": "알 수 없는 은행입니다."}

    ltv_std = load_ltv_standards(bank_name)
    if ltv_std is None:
        return {"ok": False, "message": "LTV 기준 파일을 찾을 수 없습니다."}

    # "적용시작일" 컬럼 처리
    if "적용시작일" not in ltv_std.columns:
        # 모든 기존 컬럼을 1900-01-01로 초기화 (가장 먼 과거)
        ltv_std.insert(0, "적용시작일", "1900-01-01")

    # 적용시작일은 항상 오늘 날짜 기준
    new_effective_date = datetime.now().strftime("%Y-%m-%d")

    usage_col = cfg["usage_col"]
    id_vars = cfg["id_vars"]

    # 1. 같은 구분/담보종류/지역에 대해 "현재 기준" 정보를 찾음
    # (적용시작일과 상관없이 'usage'가 같은 걸 필터링)
    mask_usage = ltv_std[usage_col] == usage
    if not mask_usage.any():
        return {"ok": False, "message": f"'{usage}' 용도를 기준 테이블에서 찾을 수 없습니다."}
    
    if region not in ltv_std.columns:
        return {"ok": False, "message": f"'{region}' 지역 컬럼이 기준표에 없습니다."}

    # 2. 이미 해당 '적용시작일'로 엔트리가 있는지 확인
    mask_exact = (ltv_std[usage_col] == usage) & (ltv_std["적용시작일"] == new_effective_date)
    
    old_ltv = None
    if mask_exact.any():
        # 해당 월에 이미 업데이트 내역이 있으면 그 행만 수정
        old_ltv = float(ltv_std.loc[mask_exact, region].iloc[0])
        ltv_std.loc[mask_exact, region] = new_ltv
    else:
        # 없으면, 가장 최근(latest) 행을 찾아서 복사한 뒤 수정
        # (usage가 같은 것들 중 적용시작일이 가장 큰 것)
        relevant_rows = ltv_std[mask_usage].sort_values("적용시작일", ascending=False)
        latest_config = relevant_rows.iloc[0].copy()
        
        old_ltv = float(latest_config[region])
        # 새로운 행 생성
        new_row = latest_config.to_dict()
        new_row["적용시작일"] = new_effective_date
        new_row[region] = new_ltv
        
        # DataFrame에 추가
        ltv_std = pd.concat([ltv_std, pd.DataFrame([new_row])], ignore_index=True)

        # 4. DB 저장
        db: Session = SessionLocal()
        try:
            # 해당 월/은행/용도/지역에 대한 기존 레코드 확인 (같은 달이면 덮어쓰기)
            month_start = pd.to_datetime(datetime.now().strftime("%Y-%m-01"))
            month_end = month_start + pd.offsets.MonthEnd(0)
            existing = db.query(LtvStandard).filter(
                LtvStandard.bank_name == bank_name,
                LtvStandard.usage_type == usage,
                LtvStandard.region == region,
                LtvStandard.effective_date >= month_start,
                LtvStandard.effective_date <= month_end
            ).first()
            
            if existing:
                old_ltv = existing.ltv_value
                existing.ltv_value = new_ltv
                existing.effective_date = pd.to_datetime(new_effective_date)  # 날짜도 오늘로 갱신
            else:
                # 없으면 상속받을 가장 최신 값 찾기
                latest = db.query(LtvStandard).filter(
                    LtvStandard.bank_name == bank_name,
                    LtvStandard.usage_type == usage,
                    LtvStandard.region == region,
                    LtvStandard.effective_date < pd.to_datetime(new_effective_date)
                ).order_by(LtvStandard.effective_date.desc()).first()
                
                old_ltv = latest.ltv_value if latest else 80.0
                current_category = latest.category if latest else "기타"
                
                # 새로운 날짜의 레코드 1개만 생성 (다른 지역은 load 단계에서 ffill로 상속됨)
                new_record = LtvStandard(
                    bank_name=bank_name,
                    category=current_category,
                    usage_type=usage,
                    region=region,
                    ltv_value=new_ltv,
                    effective_date=pd.to_datetime(new_effective_date)
                )
                db.add(new_record)
            
            db.commit()
            # 저장 후 즉시 반영 완료 및 로그 기록
            if float(old_ltv) != float(new_ltv):
                write_ltv_log(bank_name, region, usage, old_ltv, new_ltv, new_effective_date)
                
        except Exception as e:
            print(f"Error saving to DB: {e}")
            db.rollback()
            return {"ok": False, "message": f"DB 저장 중 오류: {e}"}
        finally:
            db.close()

    return {"ok": True, "message": f"[{region}] {usage}: {new_effective_date}부터 LTV {new_ltv}%로 적용되었습니다."}

def revert_ltv(bank_name: str, region: str, usage: str, base_date: str | None = None) -> dict:
    cfg = BANK_CONFIG.get(bank_name)
    if cfg is None:
        return {"ok": False, "message": "알 수 없는 은행입니다."}

    try:
        dt = pd.to_datetime(base_date)
        month_start = pd.to_datetime(dt.strftime("%Y-%m-01"))
        month_end = month_start + pd.offsets.MonthEnd(0)
    except:
        return {"ok": False, "message": "유효하지 않은 날짜입니다."}

    # DB에서 직접 처리 (CSV 기반 검증 제거)
    db: Session = SessionLocal()
    try:
        # 해당 월의 레코드 찾기
        current = db.query(LtvStandard).filter(
            LtvStandard.bank_name == bank_name,
            LtvStandard.usage_type == usage,
            LtvStandard.region == region,
            LtvStandard.effective_date >= month_start,
            LtvStandard.effective_date <= month_end
        ).first()

        if not current:
            return {"ok": False, "message": "이 달에 수정된 내역이 없어 되돌릴 수 없습니다."}

        # 이전 버전 찾기 (해당 월 이전의 가장 최신 레코드)
        prev = db.query(LtvStandard).filter(
            LtvStandard.bank_name == bank_name,
            LtvStandard.usage_type == usage,
            LtvStandard.region == region,
            LtvStandard.effective_date < month_start
        ).order_by(LtvStandard.effective_date.desc()).first()

        if not prev:
            # 이전 이력이 없으면 현재 레코드 자체를 삭제 (원본 baseline으로 복귀)
            old_ltv = current.ltv_value
            db.delete(current)
            db.commit()
            write_ltv_log(bank_name, region, usage, old_ltv, None, month_start.strftime("%Y-%m"), "(삭제-원본복귀)")
            return {"ok": True, "message": f"[{region}] {usage}: 수정 이력을 삭제하고 원본으로 복구했습니다."}

        old_ltv = current.ltv_value
        prev_val = prev.ltv_value

        if old_ltv == prev_val:
            return {"ok": False, "message": "이미 이전 기준과 동일한 값입니다."}

        # 해당 날짜의 레코드를 삭제하여 이전 값으로 자동 복귀
        db.delete(current)
        db.commit()
        
        # 로그 기록
        write_ltv_log(bank_name, region, usage, old_ltv, prev_val, month_start.strftime("%Y-%m"), "(되돌리기)")

    except Exception as e:
        db.rollback()
        return {"ok": False, "message": f"DB 되돌리기 중 오류: {e}"}
    finally:
        db.close()

    return {"ok": True, "message": f"[{region}] {usage}: LTV {old_ltv}% → {prev_val}%로 되돌렸습니다."}

# ==========================================
# AI 캐시(권고안) 통합 계층
# ==========================================
def _normalize_cached_advice(raw_payload) -> dict:
    if not raw_payload:
        return {}
    try:
        if isinstance(raw_payload, dict):
            return raw_payload
        parsed = json.loads(raw_payload)
        return parsed if isinstance(parsed, dict) else {}
    except Exception:
        return {}


def _is_retryable_advice(advice: dict) -> bool:
    """Fallback으로 저장된 AI 권고는 완료 캐시로 보지 않고 재호출 대상으로 처리합니다."""
    if not advice or not isinstance(advice, dict):
        return False

    if advice.get("error"):
        return True

    reason = str(advice.get("reason") or "")
    retry_markers = (
        "실시간 AI 모니터링 모듈 호출 지연",
        "AI 응답 파싱 또는 재시도 실패",
        "생성된 답변(reason)의 내용이 너무 짧거나 형식이 규격에 맞지 않습니다.",
    )
    return any(marker in reason for marker in retry_markers)


def _save_advice_to_signal_cache(row_id: int, bank_name: str, key: str, advice_data):
    """signal_cache 행 단위로 AI 권고안을 저장합니다."""
    if _is_retryable_advice(advice_data):
        print(f"[AI_ADVICE_NOT_SAVE] row_id={row_id} bank={bank_name} key={key} reason=retryable_or_fallback")
        return

    db: Session = SessionLocal()
    try:
        row = db.query(SignalCache).filter(
            SignalCache.id == row_id,
            SignalCache.bank_name == bank_name
        ).first()
        if not row:
            return

        row.advice_payload = json.dumps(advice_data, ensure_ascii=False) if advice_data is not None else None
        row.advice_cache_key = key
        row.advice_updated_at = datetime.now()
        row.advice_model = str(advice_data.get("model", "")) if isinstance(advice_data, dict) else None
        db.commit()
        print(f"[AI_ADVICE_SAVE] row_id={row_id} bank={bank_name} key={key} model={row.advice_model}")
    except Exception as e:
        print(f"Error saving AI advice to signal_cache: {e}")
        db.rollback()
    finally:
        db.close()


def _load_advice_from_signal_cache(row_id: int, bank_name: str) -> dict:
    db: Session = SessionLocal()
    try:
        row = db.query(SignalCache).filter(
            SignalCache.id == row_id,
            SignalCache.bank_name == bank_name
        ).first()
        if not row:
            return {}
        return _normalize_cached_advice(row.advice_payload)
    finally:
        db.close()


def _begin_ai_task(task_key: str):
    with _ai_task_lock:
        event = _ai_task_events.get(task_key)
        if event:
            return False, event

        event = threading.Event()
        _ai_task_events[task_key] = event
        _ai_pending_tasks.add(task_key)
        return True, event


def _finish_ai_task(task_key: str):
    with _ai_task_lock:
        event = _ai_task_events.pop(task_key, None)
        _ai_pending_tasks.discard(task_key)
        if event:
            event.set()


def _round_to_5(val) -> float:
    """LTV 값을 5% 단위로 반올림"""
    try:
        val = float(val)
    except (TypeError, ValueError):
        return 0.0
    r = val % 5
    return val - r if r <= 3 else val + (5 - r)

def get_signal_cache_rows(bank_name: str, base_date: str | None = None):
    base_ym = _resolve_signal_base_ym(bank_name, base_date)

    rows = _query_signal_cache_rows(bank_name, base_ym)
    if not rows or _signal_cache_needs_metric_refresh(rows):
        rows, base_ym = _build_signal_cache_from_aggregated(bank_name, base_ym, base_date)
    rows = [row for row in rows if row.signal_tone in ("red", "yellow")]
    if not rows:
        return [], base_ym

    results = []
    for row in rows:
        met = {
            "avg": {
                "3": row.avg_3,
                "6": row.avg_6,
                "12": row.avg_12,
                "36": row.avg_36,
                "60": row.avg_60,
            },
            "count": {
                "3": row.cnt_3 or 0,
                "6": row.cnt_6 or 0,
                "12": row.cnt_12 or 0,
                "36": row.cnt_36 or 0,
                "60": row.cnt_60 or 0,
            },
        }

        signal = {
            "tone": row.signal_tone or "",
            "direction": row.signal_direction or "",
            "suggested_ltv": row.suggested_ltv,
            "adjust_delta": row.adjust_delta,
            "gap3": row.gap3,
            "reason": row.reason,
        }
        advice_payload = _normalize_cached_advice(row.advice_payload)

        item = {
            "row_id": row.id,
            "region": row.region,
            "usage_type": row.usage_type,
            "category": row.category,
            "ltv_val": float(row.ltv_value) if row.ltv_value is not None else 80.0,
            "met": met,
            "signal": signal,
            "advice_payload": advice_payload,
            "advice_cache_key": row.advice_cache_key or "",
            "advice_model": row.advice_model or "",
            "advice_updated_at": row.advice_updated_at.isoformat() if row.advice_updated_at else None,
        }
        item["reg"] = item["region"]
        item["signal_tone"] = row.signal_tone
        item["signal_direction"] = row.signal_direction
        results.append(item)

    return results, base_ym


def _build_advice_info(item: dict, bank_name: str, base_date: str | None):
    met = item["met"]
    region = item.get("region") or item.get("reg") or ""
    usage = item.get("usage_type") or item.get("usage") or ""
    try:
        base_month = pd.to_datetime(base_date).to_period("M").strftime("%Y-%m-01") if base_date else ""
    except Exception:
        base_month = str(base_date or "")

    return {
        "bank_name": bank_name,
        "base_date": base_month,
        "region": region,
        "usage": usage,
        "tone": item.get("signal", {}).get("tone", ""),
        "current_ltv": float(item.get("ltv_val", 0.0) or 0.0),
        "avg3": met["avg"].get("3") or 0.0,
        "cnt3": met["count"].get("3", 0),
        "avg6": met["avg"].get("6") or 0.0,
        "cnt6": met["count"].get("6", 0),
        "avg12": met["avg"].get("12") or 0.0,
        "cnt12": met["count"].get("12", 0),
        "avg36": met["avg"].get("36") or 0.0,
        "cnt36": met["count"].get("36", 0),
    }


def _advice_cache_key(item: dict, bank_name: str, base_date: str | None):
    info = _build_advice_info(item, bank_name, base_date)
    return llm_advisor.build_cache_key(info)


def _execute_async_advice_update(task_key: str, row_id: int, bank_name: str, base_date: str | None, cache_key: str, item: dict):
    try:
        info = _build_advice_info(item, bank_name, base_date)
        with _ai_advice_semaphore:
            advice = llm_advisor.get_ltv_advice(info)
        _save_advice_to_signal_cache(row_id, bank_name, cache_key, advice)
    finally:
        _finish_ai_task(task_key)


def _queue_async_advice_update(row_id: int, bank_name: str, base_date: str | None, cache_key: str, item: dict) -> bool:
    task_key = f"{bank_name}:{row_id}:{cache_key}"

    should_run, _ = _begin_ai_task(task_key)
    if not should_run:
        return False

    threading.Thread(
        target=_execute_async_advice_update,
        args=(task_key, row_id, bank_name, base_date, cache_key, item),
        daemon=True,
    ).start()
    return True


def get_advice_generation_status(bank_name: str, base_date: str | None = None):
    rows, base_ym = get_signal_cache_rows(bank_name, base_date)

    total_count = 0
    ready_count = 0
    pending_count = 0

    for item in rows:
        total_count += 1
        advice = _normalize_cached_advice(item.get("advice_payload"))
        has_real_advice = bool(advice) and not _is_retryable_advice(advice)

        if has_real_advice:
            ready_count += 1
            continue

        pending_count += 1

    return {
        "base_ym": base_ym,
        "total_count": total_count,
        "ready_count": ready_count,
        "pending_count": pending_count,
        "queued_count": 0,
        "pending": pending_count > 0,
    }


def fetch_all_advice(
    urgent_list: list,
    bank_name: str,
    base_date: str | None = None,
    async_only: bool = False,
):
    if not urgent_list:
        return []
    async_only = False

    def process_item(item):
        met = item["met"]
        info = _build_advice_info(item, bank_name, base_date)
        cache_key = _advice_cache_key(item, bank_name, base_date)
        row_id = item.get("row_id")
        advice = _normalize_cached_advice(item.get("advice_payload"))
        has_real_advice = bool(advice) and not _is_retryable_advice(advice)
        if not has_real_advice:
            advice = {}

        if async_only:
            if not advice:
                if row_id:
                    _queue_async_advice_update(row_id, bank_name, base_date, cache_key, item)
                    advice_status = "pending"
                else:
                    with _ai_advice_semaphore:
                        advice = llm_advisor.get_ltv_advice(info)
                    advice_status = "computed"
            else:
                advice_status = "ready"
        else:
            if not advice:
                task_key = f"{bank_name}:{row_id}:{cache_key}" if row_id else ""
                should_run = True
                task_event = None
                if task_key:
                    should_run, task_event = _begin_ai_task(task_key)

                if should_run:
                    try:
                        with _ai_advice_semaphore:
                            advice = llm_advisor.get_ltv_advice(info)
                        if row_id:
                            _save_advice_to_signal_cache(row_id, bank_name, cache_key, advice)
                        advice_status = "computed"
                    finally:
                        if task_key:
                            _finish_ai_task(task_key)
                else:
                    task_event.wait()
                    advice = _load_advice_from_signal_cache(row_id, bank_name)
                    if _is_retryable_advice(advice):
                        advice = {}
                    advice_status = "ready" if advice else "pending"
            else:
                advice_status = "ready"

        current_ltv = info["current_ltv"]
        # AI 결과가 없거나 부족할 때 현재 LTV를 기본값으로 사용
        con_val = advice.get("conservative_ltv")
        rel_val = advice.get("relaxed_ltv")
        
        # AI가 0.50 같은 소수점 형태로 보냈을 경우 (0~1 사이) % 단위로 변환 (기존 캐시 대응)
        if con_val is not None and 0.0 < con_val <= 1.0 and current_ltv > 1.0:
            con_val *= 100.0
        if rel_val is not None and 0.0 < rel_val <= 1.0 and current_ltv > 1.0:
            rel_val *= 100.0
        
        conservative_ltv = _round_to_5(con_val if con_val is not None else current_ltv)
        relaxed_ltv      = _round_to_5(rel_val if rel_val is not None else current_ltv)
        
        conservative_delta = round(conservative_ltv - current_ltv, 1)
        relaxed_delta      = round(relaxed_ltv - current_ltv, 1)

        reason_raw = advice.get("reason", "")
        reason = reason_raw.replace("\n", "<br>") if isinstance(reason_raw, str) else ""

        return {
            "row_id": row_id,
            "reg": item["reg"],
            "region": item["reg"],
            "category": item["category"],
            "usage_type": item["usage_type"],
            "usage": item["usage_type"],
            "ltv_val": current_ltv,
            "current_ltv": current_ltv,
            "signal": item.get("signal", {}),
            "tone": item.get("signal", {}).get("tone", ""),
            "direction": item.get("signal", {}).get("direction", ""),
            "conservative_ltv": conservative_ltv,
            "conservative_delta": conservative_delta,
            "relaxed_ltv": relaxed_ltv,
            "relaxed_delta": relaxed_delta,
            "reason": reason,
            "advice_status": advice_status,
            "met": {
                "avg": {str(k): v for k, v in item["met"]["avg"].items()},
                "count": {str(k): v for k, v in item["met"]["count"].items()},
            },
        }

    results = []
    if len(urgent_list) > 1:
        import concurrent.futures

        with concurrent.futures.ThreadPoolExecutor(max_workers=_AI_ADVICE_CONCURRENCY) as executor:
            for result in executor.map(process_item, urgent_list):
                results.append(result)
                gc.collect()  # 각 항목 처리 후 메모리 강제 해제
    else:
        for item in urgent_list:
            results.append(process_item(item))
            gc.collect()  # 각 항목 처리 후 메모리 강제 해제
    return results


def recompute_row_advice(row_id: int, bank_name: str, base_date: str):
    """특정 행의 AI 권고안을 강제로 다시 계산하고 DB에 저장합니다."""
    from database import SessionLocal, SignalCache
    with SessionLocal() as db:
        row = db.query(SignalCache).filter(SignalCache.id == row_id).first()
        if not row:
            return None
        
        # item 데이터 구성 (기존 get_signal_cache_rows 로직 일부 차용)
        met = {
            "avg": {"3": row.avg_3, "6": row.avg_6, "12": row.avg_12, "36": row.avg_36},
            "count": {"3": row.cnt_3, "6": row.cnt_6, "12": row.cnt_12, "36": row.cnt_36}
        }
        item = {
            "region": row.region,
            "usage_type": row.usage_type,
            "ltv_val": float(row.ltv_value) if row.ltv_value is not None else 80.0,
            "met": met,
            "signal": {"tone": row.signal_tone or "", "direction": row.signal_direction or ""}
        }
        
        info = _build_advice_info(item, bank_name, base_date)
        cache_key = llm_advisor.build_cache_key(info)
        
        # 강제 재산출
        advice = llm_advisor.get_ltv_advice(info)
        
        # DB 업데이트
        row.advice_payload = json.dumps(advice, ensure_ascii=False)
        row.advice_cache_key = cache_key
        row.advice_updated_at = datetime.now()
        db.commit()
        
        # 최종 반환 데이터 가공 (fetch_all_advice와 동일하게 5단위 반올림 적용)
        con_val = advice.get("conservative_ltv")
        rel_val = advice.get("relaxed_ltv")
        
        current_ltv = info["current_ltv"]
        if con_val is not None and 0.0 < con_val <= 1.5 and current_ltv > 1.0:
            con_val *= 100.0
        if rel_val is not None and 0.0 < rel_val <= 1.5 and current_ltv > 1.0:
            rel_val *= 100.0
            
        final_conservative = _round_to_5(con_val if con_val is not None else current_ltv)
        final_relaxed = _round_to_5(rel_val if rel_val is not None else current_ltv)
        
        return {
            "conservative_ltv": final_conservative,
            "relaxed_ltv": final_relaxed,
            "reason": advice.get("reason", "").replace("\n", "<br>")
        }
