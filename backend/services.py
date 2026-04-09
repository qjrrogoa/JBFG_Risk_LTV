import os
import gc
import json
import threading
import pandas as pd
from datetime import datetime
from dateutil.relativedelta import relativedelta
from concurrent.futures import ThreadPoolExecutor
from functools import lru_cache
import bcrypt
from sqlalchemy.orm import Session
from database import SessionLocal, LtvStandard, LtvLog, User, LlmCache

import sys
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
import llm_advisor

# ==========================================
# 상수 및 전역 설정
# ==========================================
FIXED_MONTHS = [(3, "3개월"), (6, "6개월"), (12, "12개월"), (36, "3년"), (60, "5년")]
DATA_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "data"))
CACHE_DIR = os.path.join(DATA_DIR, "llm_cache")
LOG_DIR = os.path.join(DATA_DIR, "logs")
os.makedirs(CACHE_DIR, exist_ok=True)
os.makedirs(LOG_DIR, exist_ok=True)
CACHE_LOCK = threading.Lock()

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


def load_regional_data_raw(file_path: str):
    """CSV 파일 자체의 전처리만 수행 (Merge 제외)"""
    df = pd.read_csv(file_path)

    def parse_currency(x):
        if isinstance(x, str):
            try: return int(x.replace(",", ""))
            except: return 0
        return x

    def parse_percentage(x):
        if isinstance(x, str):
            try: return float(x.replace("%", ""))
            except: return 0.0
        return x

    df["낙찰가"] = df["낙찰가"].apply(parse_currency)
    df["감정가"] = df["감정가"].apply(parse_currency)
    df["낙찰율"] = df["낙찰율"].apply(parse_percentage)
    df["매각일"] = pd.to_datetime(df["매각일"])

    return df


_winning_df_cache: dict = {}
_winning_df_lock = threading.Lock()

# 메모리 관리를 위해 전체 데이터 캐싱을 비활성화하거나 극히 제한합니다.
def get_processed_region_df(bank_name: str, region_fname: str) -> pd.DataFrame:
    """특정 지역의 데이터를 로드하고 해당 은행의 LTV 기준에 맞춰 전처리를 수행합니다."""
    cfg = BANK_CONFIG[bank_name]
    path = os.path.join(DATA_DIR, f"{region_fname}.csv")
    if not os.path.exists(path):
        return pd.DataFrame()

    try:
        df = load_regional_data_raw(path)
    except Exception as e:
        print(f"[Warn] {region_fname} 로드 실패: {e}")
        return pd.DataFrame()

    # 전처리 (용도 매핑 등)
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
        # pandas warning 방지를 위해 .loc 사용
        df.loc[mask_jb & df["시군구"].str.contains("전주", na=False), "_LTV지역구분"] = "전주"
        df.loc[mask_jb & df["시군구"].str.contains("군산", na=False), "_LTV지역구분"] = "군산"
        df.loc[mask_jb & df["시군구"].str.contains("익산", na=False), "_LTV지역구분"] = "익산"

    # LTV 기준 통합 (필요한 경우)
    ltv_std = load_ltv_standards(bank_name)
    if ltv_std is not None:
        if "적용시작일" not in ltv_std.columns:
            ltv_std["적용시작일"] = "2000-01-01"
        ltv_std["적용시작일"] = pd.to_datetime(ltv_std["적용시작일"])

        id_vars = cfg["id_vars"]
        melt_ids = id_vars + ["적용시작일"]
        usage_col = cfg["usage_col"]
        exclude = cfg.get("exclude_regions", [])

        # Wide -> Long 변환
        std_melted = ltv_std.melt(id_vars=melt_ids, var_name="_LTV지역구분", value_name="적용LTV")
        valid_regions = [c for c in ltv_std.columns if c not in melt_ids and c not in exclude]
        
        # 유효 지역 필터링
        df = df[df["_LTV지역구분"].isin(valid_regions)].copy()
        if df.empty: return df
        
        df = df.rename(columns={"분석용도": usage_col})
        df = df.sort_values("매각일")
        std_melted = std_melted.sort_values("적용시작일")

        df = pd.merge_asof(
            df,
            std_melted[[usage_col, "_LTV지역구분", "적용시작일", "적용LTV"]],
            left_on="매각일",
            right_on="적용시작일",
            by=[usage_col, "_LTV지역구분"],
            direction="backward"
        )
        df["적용LTV"] = df["적용LTV"].fillna(80.0)
        df = df.rename(columns={usage_col: "분석용도"})
    else:
        df["적용LTV"] = 80.0

    # 낙찰/매각 건만 필터링
    if "결과" in df.columns:
        df = df[df["결과"].astype(str).str.contains("낙찰|매각", na=False)].copy()

    return df

def get_global_winning_df(bank_name: str) -> pd.DataFrame:
    """은행별 전체 매각 데이터를 로드합니다 (메모리 주의!). 차트 등 소량 사용 시에만 권장."""

    cfg = BANK_CONFIG[bank_name]
    dfs = []
    
    # 1. 모든 지역 파일 단순 결합 (가장 빠름)
    for fname in REGIONS_ALL:
        path = os.path.join(DATA_DIR, f"{fname}.csv")
        if os.path.exists(path):
            try:
                dfs.append(load_regional_data_raw(path))
            except Exception as e:
                print(f"[Warn] {fname} 로드 실패: {e}")

    if not dfs:
        raise FileNotFoundError("데이터 파일을 찾을 수 없습니다.")

    df = pd.concat(dfs, ignore_index=True)
    
    # 2. 전처리 (용도 매핑 등)
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

    # 3. LTV 기준표 한 번만 MERGE (핵심 성능 포인트)
    ltv_std = load_ltv_standards(bank_name)
    if ltv_std is not None:
        if "적용시작일" not in ltv_std.columns:
            ltv_std["적용시작일"] = "2000-01-01"
        ltv_std["적용시작일"] = pd.to_datetime(ltv_std["적용시작일"])

        id_vars = cfg["id_vars"]
        # 적용시작일도 ID 변수처럼 행동함 (Melt 시 유지)
        melt_ids = id_vars + ["적용시작일"]
        usage_col = cfg["usage_col"]
        exclude = cfg.get("exclude_regions", [])

        # Wide -> Long 변환
        std_melted = ltv_std.melt(id_vars=melt_ids, var_name="_LTV지역구분", value_name="적용LTV")
        valid_regions = [c for c in ltv_std.columns if c not in melt_ids and c not in exclude]
        
        # 유효 지역 필터링
        df = df[df["_LTV지역구분"].isin(valid_regions)].copy()
        
        # rename for merge_asof matching
        df = df.rename(columns={"분석용도": usage_col})
        
        # merge_asof 를 위해 정렬 필수
        df = df.sort_values("매각일")
        std_melted = std_melted.sort_values("적용시작일")

        # merge_asof: 매각일 >= 적용시작일 인 가장 최신 LTV 매칭
        df = pd.merge_asof(
            df,
            std_melted[[usage_col, "_LTV지역구분", "적용시작일", "적용LTV"]],
            left_on="매각일",
            right_on="적용시작일",
            by=[usage_col, "_LTV지역구분"],
            direction="backward"
        )
        df["적용LTV"] = df["적용LTV"].fillna(80.0)
        # 분석용도 원래 이름으로 (다른 곳에서 쓰일 수 있음)
        df = df.rename(columns={usage_col: "분석용도"})
    else:
        df["적용LTV"] = 80.0

    # 낙찰/매각 건만 필터링
    if "결과" in df.columns:
        result_df = df[df["결과"].astype(str).str.contains("낙찰|매각", na=False)].copy()
    else:
        result_df = df.copy()

    with _winning_df_lock:
        _winning_df_cache[bank_name] = result_df

    return result_df


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


_aggregated_cache: dict = {}
_agg_lock = threading.Lock()
MAX_CACHE_SIZE = 3 # 메모리 보존을 위해 최근 3개 요청만 캐시

def get_aggregated_data(bank_name: str, base_date: str | None = None,
                        outlier_thresh: float = 0.3, min_cnt: int = 1):
    """지역별 순차 처리를 통해 메모리 효율적으로 집계를 수행합니다."""
    ym_key = base_date if base_date else "LATEST"
    cache_key = f"{bank_name}_{ym_key}"
    
    with _agg_lock:
        if cache_key in _aggregated_cache:
            return _aggregated_cache[cache_key]

    ltv_std = load_ltv_standards(bank_name)
    selected_dt = pd.to_datetime(base_date) if base_date else None
    matrix_rows, urgent_cards = [], []

    # 한 번에 처리할 지역 개수 (테스트용: 4개)
    CHUNK_SIZE = 4
    for i in range(0, len(REGIONS_ALL), CHUNK_SIZE):
        chunk = REGIONS_ALL[i:i + CHUNK_SIZE]
        chunk_dfs = []
        
        # 1. 청크 내의 지역 데이터 로드
        for region_fname in chunk:
            df = get_processed_region_df(bank_name, region_fname)
            if not df.empty:
                chunk_dfs.append(df)

        if not chunk_dfs:
            continue

        # 2. 청크 데이터 통합
        combined_winning = pd.concat(chunk_dfs, ignore_index=True)
        if selected_dt is not None:
            combined_winning = combined_winning[combined_winning["매각일"] <= selected_dt]
        
        if combined_winning.empty:
            del combined_winning, chunk_dfs; clear_memory()
            continue

        # 3. 통합된 데이터 처리
        for reg, sub_winning in combined_winning.groupby("_LTV지역구분"):
            reg_last_date = selected_dt if selected_dt is not None else sub_winning["매각일"].max()
            reg_group = sub_winning.groupby("분석용도")

            if ltv_std is not None:
                std_info = ltv_std[["구분", "담보종류"]].drop_duplicates()
                for _, row_std in std_info.iterrows():
                    category, usage_type = row_std["구분"], row_std["담보종류"]
                    if usage_type not in reg_group.groups:
                        continue

                    target_sample = reg_group.get_group(usage_type)
                    ltv_val = float(target_sample["적용LTV"].iloc[-1]) if "적용LTV" in target_sample.columns else 80.0
                    
                    met = calculate_metrics(sub_winning, usage_type, ltv_val, reg_last_date, outlier_thresh)
                    met_str = {
                        "avg": {str(k): v for k, v in met["avg"].items()},
                        "count": {str(k): v for k, v in met["count"].items()},
                    }

                    signal = check_signal_logic(met, ltv_val, min_cnt)
                    if signal:
                        urgent_cards.append({
                            "reg": reg, "category": category, "usage_type": usage_type,
                            "ltv_val": ltv_val, "met": met_str, "signal": signal,
                        })

                    row = {
                        "지역": reg, "대분류": category, "용도": usage_type, "LTV": ltv_val,
                        "signal_tone": signal["tone"] if signal else None,
                        "met": met_str,
                    }
                    for m_num, m_lbl in FIXED_MONTHS:
                        row[m_lbl] = classify_period(met["avg"].get(m_num), ltv_val, met["count"].get(m_num, 0), min_cnt)
                        row[f"{m_lbl}_count"] = met["count"].get(m_num, 0)
                    matrix_rows.append(row)

        # 4. 청크 처리 완료 후 메모리 즉시 해제
        del combined_winning, chunk_dfs
        clear_memory()

    if not matrix_rows:
        return pd.DataFrame(), []

    res_df = pd.DataFrame(matrix_rows)
    with _agg_lock:
        if len(_aggregated_cache) >= MAX_CACHE_SIZE:
            # 가장 오래된 캐시 하나 삭제
            oldest_key = next(iter(_aggregated_cache))
            del _aggregated_cache[oldest_key]
        _aggregated_cache[cache_key] = (res_df, urgent_cards)
        
    return res_df, urgent_cards


# ==========================================
# 차트 데이터
# ==========================================
def get_chart_data(bank_name: str, region: str, usage_type: str, base_date: str | None = None):
    # region을 포함하는 원본 파일 찾기 (REGION_COL_MAP 역참조 또는 시도명 매칭)
    source_fname = None
    for fname in REGIONS_ALL:
        if fname in region or region in fname:
            source_fname = fname
            break
    
    # 못 찾으면 검색 (느릴 수 있음)
    if not source_fname:
        source_fname = region # Fallback

    reg_df = get_processed_region_df(bank_name, source_fname)
    if reg_df.empty: 
        return {"ltv": 80.0, "points": []}

    # 세부 지역 필터링 (여러 지역이 한 파일에 있을 수 있음)
    reg_df = reg_df[reg_df["_LTV지역구분"] == region].copy()
    
    ltv_std = load_ltv_standards(bank_name)
    selected_dt = pd.to_datetime(base_date) if base_date else reg_df["매각일"].max()
    reg_df = reg_df[reg_df["매각일"] <= selected_dt]

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

    # 캐시 무효화
    with _winning_df_lock:
        _winning_df_cache.pop(bank_name, None)
    
    # 집계 캐시도 전체 삭제 (LTV 기준이 바뀌었으므로)
    with _agg_lock:
        _aggregated_cache.clear()

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
            # 캐시 무효화
            with _winning_df_lock:
                _winning_df_cache.pop(bank_name, None)
            with _agg_lock:
                _aggregated_cache.clear()
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

    # 캐시 무효화
    with _winning_df_lock:
        _winning_df_cache.pop(bank_name, None)
    with _agg_lock:
        _aggregated_cache.clear()

    return {"ok": True, "message": f"[{region}] {usage}: LTV {old_ltv}% → {prev_val}%로 되돌렸습니다."}

# ==========================================
# LLM 통신 계층
# ==========================================
def load_monthly_cache(bank_name: str, ym_str: str):
    """DB에서 해당 월의 모든 LLM 캐시를 딕셔너리로 반환"""
    db: Session = SessionLocal()
    try:
        rows = db.query(LlmCache).filter(
            LlmCache.bank_name == bank_name,
            LlmCache.ym_str == ym_str
        ).all()
        cache = {}
        for row in rows:
            try:
                cache[row.cache_key] = json.loads(row.advice_data)
            except Exception:
                pass
        return cache
    except Exception:
        return {}
    finally:
        db.close()


def save_to_monthly_cache(bank_name: str, ym_str: str, key: str, advice_data, region: str = "", usage_type: str = ""):
    """DB에 LLM 캐시 저장 (upsert)"""
    db: Session = SessionLocal()
    try:
        existing = db.query(LlmCache).filter(
            LlmCache.bank_name == bank_name,
            LlmCache.ym_str == ym_str,
            LlmCache.cache_key == key
        ).first()
        
        serialized = json.dumps(advice_data, ensure_ascii=False)
        
        if existing:
            existing.advice_data = serialized
            existing.updated_at = datetime.now()
            if region:
                existing.region = region
            if usage_type:
                existing.usage_type = usage_type
        else:
            db.add(LlmCache(
                bank_name=bank_name,
                ym_str=ym_str,
                cache_key=key,
                region=region,
                usage_type=usage_type,
                advice_data=serialized
            ))
        db.commit()
    except Exception as e:
        print(f"Error saving LLM cache to DB: {e}")
        db.rollback()
    finally:
        db.close()


def _round_to_5(val) -> float:
    """LTV 값을 5% 단위로 반올림"""
    try:
        val = float(val)
    except (TypeError, ValueError):
        return 0.0
    r = val % 5
    return val - r if r <= 3 else val + (5 - r)


def fetch_all_advice(urgent_list: list, bank_name: str, base_date: str | None = None):
    if not urgent_list:
        return []

    ym_str = (pd.to_datetime(base_date).strftime("%Y_%m") if base_date
              else datetime.now().strftime("%Y_%m"))
    monthly_cache = load_monthly_cache(bank_name, ym_str)

    def process_item(item):
        met = item["met"]
        info = {
            "bank_name": bank_name,
            "base_date": base_date,
            "region": item["reg"],
            "usage": item["usage_type"],
            "tone": item.get("signal", {}).get("tone", ""),
            "current_ltv": float(item["ltv_val"]),
            "avg3": met["avg"].get("3") or 0.0,
            "cnt3": met["count"].get("3", 0),
            "avg6": met["avg"].get("6") or 0.0,
            "cnt6": met["count"].get("6", 0),
            "avg12": met["avg"].get("12") or 0.0,
            "cnt12": met["count"].get("12", 0),
            "avg36": met["avg"].get("36") or 0.0,
            "cnt36": met["count"].get("36", 0),
        }
        # -------------------------------------------------------------
        # 1. 정밀한 캐시 키 생성 (llm_advisor의 SHA1 기반 키 추천 사용)
        # -------------------------------------------------------------
        cache_key = llm_advisor.build_cache_key(info)

        # -------------------------------------------------------------
        # 2. 캐시 로딩 및 신선도(Freshness) 체크
        # -------------------------------------------------------------
        advice = monthly_cache.get(cache_key, {})
        
        # llm_advisor가 제공하는 TTL(Red: 24h 등) 및 섹션 검증 로직 통합 적용
        is_fresh = llm_advisor.is_cache_fresh(advice, info["tone"])
        is_bad_data = not advice or "오류" in str(advice.get("reason", "")) or not is_fresh
        
        # -------------------------------------------------------------
        # 3. 필요시 AI 분석 수행 (OpenAI web_search 포함)
        # -------------------------------------------------------------
        if is_bad_data:
            # 실시간 웹 검색 및 전문가 분석 수행
            advice = llm_advisor.get_ltv_advice(info)
            # 웹 검색이 성공하고 에러가 없는 경우에만 캐시에 저장 (Fallback 저장 방지)
            if advice.get("search_used") and not advice.get("error"):
                save_to_monthly_cache(bank_name, ym_str, cache_key, advice,
                                      region=info["region"], usage_type=info["usage"])

        current_ltv = info["current_ltv"]
        # AI 결과가 없거나 부족할 때 현재 LTV를 기본값으로 사용
        con_val = advice.get("conservative_ltv")
        rel_val = advice.get("relaxed_ltv")
        
        conservative_ltv = _round_to_5(con_val if con_val is not None else current_ltv)
        relaxed_ltv      = _round_to_5(rel_val if rel_val is not None else current_ltv)
        
        conservative_delta = round(conservative_ltv - current_ltv, 1)
        relaxed_delta      = round(relaxed_ltv - current_ltv, 1)

        reason_raw = advice.get("reason", "")
        reason = reason_raw.replace("\n", "<br>") if isinstance(reason_raw, str) else ""

        return {
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
            "met": {
                "avg": {str(k): v for k, v in item["met"]["avg"].items()},
                "count": {str(k): v for k, v in item["met"]["count"].items()},
            },
        }

    results = []
    for item in urgent_list:
        results.append(process_item(item))
        gc.collect()  # 각 항목 처리 후 메모리 강제 해제
    return results
