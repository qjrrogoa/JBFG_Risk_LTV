import os
import json
import threading
import pandas as pd
from datetime import datetime
from dateutil.relativedelta import relativedelta
from concurrent.futures import ThreadPoolExecutor
from functools import lru_cache

import sys
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
import llm_advisor

# ==========================================
# 상수 및 전역 설정
# ==========================================
FIXED_MONTHS = [(3, "3개월"), (6, "6개월"), (12, "12개월"), (36, "3년"), (60, "5년")]
DATA_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "data"))
CACHE_DIR = os.path.join(DATA_DIR, "llm_cache")
os.makedirs(CACHE_DIR, exist_ok=True)
CACHE_LOCK = threading.Lock()

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
# 인증
# ==========================================
def verify_login(bank_name: str, password: str) -> bool:
    cfg = BANK_CONFIG.get(bank_name)
    if cfg is None:
        return False
    return cfg["password"] == password


# ==========================================
# 데이터 로딩 계층
# ==========================================
def load_ltv_standards(bank_name: str):
    cfg = BANK_CONFIG.get(bank_name)
    if cfg is None:
        return None
    path = cfg["ltv_file"]
    if os.path.exists(path):
        try:
            return pd.read_csv(path, encoding="utf-8-sig")
        except Exception:
            pass
    return None


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


def get_global_winning_df(bank_name: str) -> pd.DataFrame:
    """은행별 전체 매각 데이터를 로드하고 LTV 기준을 통합합니다."""
    with _winning_df_lock:
        if bank_name in _winning_df_cache:
            return _winning_df_cache[bank_name]

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
        id_vars = cfg["id_vars"]
        usage_col = cfg["usage_col"]
        exclude = cfg.get("exclude_regions", [])

        std_melted = ltv_std.melt(id_vars=id_vars, var_name="_LTV지역구분", value_name="적용LTV")
        valid_regions = [c for c in ltv_std.columns if c not in id_vars and c not in exclude]
        
        # 유효 지역 필터링
        df = df[df["_LTV지역구분"].isin(valid_regions)].copy()

        # 대규모 MERGE
        df = df.merge(
            std_melted[[usage_col, "_LTV지역구분", "적용LTV"]],
            left_on=["분석용도", "_LTV지역구분"],
            right_on=[usage_col, "_LTV지역구분"],
            how="left"
        )
        df["적용LTV"] = df["적용LTV"].fillna(80.0)
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
    is_golden = avg3 > avg6 > avg12
    is_dead = avg3 < avg6 < avg12

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

def get_aggregated_data(bank_name: str, base_date: str | None = None,
                        outlier_thresh: float = 0.3, min_cnt: int = 1):
    """은행별/날짜별 집계 결과를 캐시하여 극강의 속도를 보장합니다."""
    ym_key = base_date if base_date else "LATEST"
    cache_key = f"{bank_name}_{ym_key}"
    
    with _agg_lock:
        if cache_key in _aggregated_cache:
            return _aggregated_cache[cache_key]

    winning_df = get_global_winning_df(bank_name)
    ltv_std = load_ltv_standards(bank_name)
    
    selected_dt = pd.to_datetime(base_date) if base_date else None
    matrix_rows, urgent_cards = [], []

    # 전체 데이터에서 해당 날짜까지만 미리 필터링 (반복 필터링 방지)
    all_filtered = winning_df.copy()
    if selected_dt is not None:
        all_filtered = all_filtered[all_filtered["매각일"] <= selected_dt]
    
    if all_filtered.empty:
        return pd.DataFrame(), []

    # 지역별로 그룹화하여 처리
    for reg, reg_winning in all_filtered.groupby("_LTV지역구분"):
        reg_last_date = selected_dt if selected_dt is not None else reg_winning["매각일"].max()
        reg_group = reg_winning.groupby("분석용도")

        if ltv_std is not None:
            std_info = ltv_std[["구분", "담보종류"]].drop_duplicates()
            for _, row_std in std_info.iterrows():
                category, usage_type = row_std["구분"], row_std["담보종류"]
                if usage_type not in reg_group.groups:
                    continue

                target_sample = reg_group.get_group(usage_type)
                ltv_val = float(target_sample["적용LTV"].iloc[0]) if "적용LTV" in target_sample.columns else 80.0
                
                # 통계 계산
                met = calculate_metrics(reg_winning, usage_type, ltv_val, reg_last_date, outlier_thresh)
                signal = check_signal_logic(met, ltv_val, min_cnt)

                if signal:
                    urgent_cards.append({
                        "reg": reg, "category": category, "usage_type": usage_type,
                        "ltv_val": ltv_val, "met": met, "signal": signal,
                    })

                row = {
                    "지역": reg, "대분류": category, "용도": usage_type, "LTV": ltv_val,
                    "signal_tone": signal["tone"] if signal else None,
                }
                for m_num, m_lbl in FIXED_MONTHS:
                    row[m_lbl] = classify_period(met["avg"].get(m_num), ltv_val, met["count"].get(m_num, 0), min_cnt)
                    row[f"{m_lbl}_count"] = met["count"].get(m_num, 0)
                matrix_rows.append(row)

    res_df = pd.DataFrame(matrix_rows)
    
    with _agg_lock:
        _aggregated_cache[cache_key] = (res_df, urgent_cards)
        
    return res_df, urgent_cards


# ==========================================
# 차트 데이터
# ==========================================
def get_chart_data(bank_name: str, region: str, usage_type: str, base_date: str | None = None):
    winning_df = get_global_winning_df(bank_name)
    ltv_std = load_ltv_standards(bank_name)

    reg_df = winning_df[winning_df["_LTV지역구분"] == region].copy()
    selected_dt = pd.to_datetime(base_date) if base_date else reg_df["매각일"].max()
    reg_df = reg_df[reg_df["매각일"] <= selected_dt]

    if reg_df.empty:
        return {"ltv": 80.0, "points": []}

    # LTV 값 찾기
    target_df = reg_df[reg_df["분석용도"] == usage_type]
    ltv_val = float(target_df["적용LTV"].iloc[0]) if not target_df.empty and "적용LTV" in target_df.columns else 80.0

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
# LTV 저장
# ==========================================
def save_ltv(bank_name: str, region: str, usage: str, new_ltv: float) -> dict:
    cfg = BANK_CONFIG.get(bank_name)
    if cfg is None:
        return {"ok": False, "message": "알 수 없는 은행입니다."}

    ltv_std = load_ltv_standards(bank_name)
    if ltv_std is None:
        return {"ok": False, "message": "LTV 기준 파일을 찾을 수 없습니다."}

    usage_col = cfg["usage_col"]
    mask = ltv_std[usage_col] == usage
    if not mask.any():
        return {"ok": False, "message": f"'{usage}' 용도를 기준 테이블에서 찾을 수 없습니다."}

    if region not in ltv_std.columns:
        return {"ok": False, "message": f"'{region}' 지역 컬럼이 기준표에 없습니다."}

    ltv_std.loc[mask, region] = new_ltv
    ltv_std.to_csv(cfg["ltv_file"], index=False, encoding="utf-8-sig")

    # 캐시 무효화
    with _winning_df_lock:
        _winning_df_cache.pop(bank_name, None)

    return {"ok": True, "message": f"[{region}] {usage}: LTV {new_ltv}%로 적용 완료!"}


# ==========================================
# LLM 통신 계층
# ==========================================
def get_monthly_cache_file(bank_name: str, ym_str: str):
    bank_id = "jbb" if bank_name == "전북은행" else "kjb"
    return os.path.join(CACHE_DIR, f"llm_{bank_id}_{ym_str}.json")


def load_monthly_cache(bank_name: str, ym_str: str):
    path = get_monthly_cache_file(bank_name, ym_str)
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            try:
                return json.load(f)
            except Exception:
                pass
    return {}


def save_to_monthly_cache(bank_name: str, ym_str: str, key: str, advice_data):
    path = get_monthly_cache_file(bank_name, ym_str)
    with CACHE_LOCK:
        cache = load_monthly_cache(bank_name, ym_str)
        cache[key] = advice_data
        with open(path, "w", encoding="utf-8") as f:
            json.dump(cache, f, ensure_ascii=False, indent=2)


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
            "avg3": met["avg"][3] or 0.0,
            "cnt3": met["count"][3],
            "avg6": met["avg"][6] or 0.0,
            "cnt6": met["count"][6],
            "avg12": met["avg"][12] or 0.0,
            "cnt12": met["count"][12],
            "avg36": met["avg"][36] or 0.0,
            "cnt36": met["count"][36],
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
                save_to_monthly_cache(bank_name, ym_str, cache_key, advice)

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

    with ThreadPoolExecutor(max_workers=10) as executor:
        results = list(executor.map(process_item, urgent_list))
    return results
