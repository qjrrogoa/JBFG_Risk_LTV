import os
import json
import threading
import pandas as pd
from datetime import datetime
from dateutil.relativedelta import relativedelta
from concurrent.futures import ThreadPoolExecutor
from functools import lru_cache

# 상위 폴더에 있는 llm_advisor.py 함수를 재사용하기 위해 path 추가
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

# ==========================================
# 데이터 로딩 계층 (기존 st.cache_data 대체)
# ==========================================
@lru_cache(maxsize=1)
def load_ltv_standards():
    try:
        return pd.read_csv(os.path.join(DATA_DIR, "LTV_기준(광주은행).csv"), encoding="utf-8-sig")
    except Exception:
        return None

def get_ltv_col_name_vec(s_si_do):
    mapping = {
        "서울특별시": "서울", "인천광역시": "인천", "경기도": "경기",
        "광주광역시": "광주", "전라남도": "전남", "전라북도": "전북",
        "부산광역시": "부산", "대전광역시": "대전", "대구광역시": "대구",
        "울산광역시": "울산", "세종특별자치시": "세종", "충청북도": "충북",
        "충청남도": "충남", "경상북도": "경북", "경상남도": "경남",
        "제주특별자치도": "제주", "강원도": "강원",
        "서울": "서울", "인천": "인천", "경기": "경기", "광주": "광주",
        "전남": "전남", "전북": "전북", "부산": "부산", "대전": "대전",
        "대구": "대구", "울산": "울산", "세종": "세종", "충북": "충북",
        "충남": "충남", "경북": "경북", "경남": "경남", "제주": "제주", "강원": "강원",
    }
    return s_si_do.map(mapping).fillna("경기")

def map_usage_to_config(usage):
    if not isinstance(usage, str): return str(usage)
    if usage in ["연립주택", "연립"]: return "연립"
    if usage in ["병원", "의료시설"]: return "의료시설"
    if "오피스텔" in usage: return "오피스텔"
    if "나대지" in usage or usage == "대지": return "대지"
    return usage

def load_regional_data(file_path):
    df = pd.read_csv(file_path)

    def parse_currency(x):
        if isinstance(x, str): return int(x.replace(",", ""))
        return x

    def parse_percentage(x):
        if isinstance(x, str): return float(x.replace("%", ""))
        return x

    df["낙찰가"] = df["낙찰가"].apply(parse_currency)
    df["감정가"] = df["감정가"].apply(parse_currency)
    df["낙찰율"] = df["낙찰율"].apply(parse_percentage)
    df["매각일"] = pd.to_datetime(df["매각일"])

    if "LTV_광주" in df.columns:
        df["분석용도"] = df["LTV_광주"]
    else:
        df["분석용도"] = df["용도"].apply(map_usage_to_config)

    df["_LTV지역구분"] = get_ltv_col_name_vec(df["시도"])
    
    ltv_standards = load_ltv_standards()
    if ltv_standards is not None:
        std_melted = ltv_standards.melt(
            id_vars=["구분", "담보종류"], var_name="_LTV지역구분", value_name="적용LTV"
        )
        df = df.merge(
            std_melted[["담보종류", "_LTV지역구분", "적용LTV"]],
            left_on=["분석용도", "_LTV지역구분"], right_on=["담보종류", "_LTV지역구분"],
            how="left"
        )
        df["적용LTV"] = df["적용LTV"].fillna(80.0)
        df.drop(columns=["담보종류", "_LTV지역구분"], inplace=True)
    else:
        df["적용LTV"] = 80.0

    return df

@lru_cache(maxsize=1)
def get_global_winning_df():
    dfs = []
    regions = ["서울", "인천", "경기", "부산", "대구", "광주", "울산", "전북", "전남"]
    for fname in regions:
        path = os.path.join(DATA_DIR, f"{fname}.csv")
        if os.path.exists(path):
            try:
                dfs.append(load_regional_data(path))
            except Exception as e:
                print(f"[Warn] {fname} 로드 실패: {e}")

    if not dfs:
        raise FileNotFoundError("데이터 파일을 찾을 수 없습니다. 경로를 확인해주세요.")

    df = pd.concat(dfs, ignore_index=True)
    if "결과" in df.columns:
        return df[df["결과"].astype(str).str.contains("낙찰|매각", na=False)].copy()
    return df.copy()


# ==========================================
# 통계 집계 로직
# ==========================================
def calculate_metrics(source_df, target_usage, ltv, current_date, mode="월별 (극단값 제외)", outlier_thresh=0.3):
    sub_df = source_df[source_df["분석용도"] == target_usage].copy()

    if mode == "월별 (극단값 제외)":
        limit = ltv * outlier_thresh
        sub_df = sub_df[abs(sub_df["낙찰율"] - ltv) <= limit]

    results = {"avg": {}, "count": {}}
    for m in [3, 6, 12, 36, 60]:
        start_date = current_date - relativedelta(months=m)
        m_filtered = sub_df[(sub_df["매각일"] > start_date) & (sub_df["매각일"] <= current_date)]
        results["avg"][m] = m_filtered["낙찰율"].mean() if not m_filtered.empty else None
        results["count"][m] = len(m_filtered)
    return results

def classify_period(avg_value, ltv, count_value, min_required=1):
    if avg_value is None or count_value < min_required: return "gray"
    abs_gap = abs(avg_value - ltv)
    if abs_gap > 10: return "red"
    if abs_gap >= 5: return "yellow"
    return "green"

def check_signal_logic(metrics, ltv, min_val):
    if metrics is None: return None
    avg12, avg6, avg3 = metrics["avg"][12], metrics["avg"][6], metrics["avg"][3]
    cnt12, cnt6, cnt3, cnt36 = metrics["count"][12], metrics["count"][6], metrics["count"][3], metrics["count"][36]

    if not all(v is not None for v in [avg12, avg6, avg3]): return None
    d12, d6, d3 = avg12 - ltv, avg6 - ltv, avg3 - ltv
    g12, g6, g3 = round(abs(d12), 1), round(abs(d6), 1), round(abs(d3), 1)

    if cnt3 < 10: return None
    
    weighted_gap = (g3 * 5 + g6 * 3 + g12 * 2) / 10.0
    is_red = weighted_gap >= 10
    is_yellow = (weighted_gap >= 5) and not is_red
    if not (is_red or is_yellow): return None

    is_pos = all(d > 0 for d in [d12, d6, d3])
    is_neg = all(d < 0 for d in [d12, d6, d3])
    is_golden = avg3 > avg6 > avg12
    is_dead = avg3 < avg6 < avg12

    direction = "▲" if is_pos and is_golden else ("▼" if is_neg and is_dead else None)
    if not direction: return None

    return {
        "direction": direction, "tone": "red" if is_red else "yellow",
        "gap3": round(avg3 - ltv, 2), "suggested_ltv": round(avg12 if direction == "▲" else avg3, 1),
        "reason": f"3/6/12개월 가중평균 낙찰가율이 기존 LTV와 {'10%p 이상' if is_red else '5%p 이상'} 차이, 건수 충족, {'상향' if direction == '▲' else '하향'} 추세 확인",
        "counts": {"3": cnt3, "6": cnt6, "12": cnt12, "36": cnt36}
    }

@lru_cache(maxsize=2)
def get_aggregated_data(mode="월별 (극단값 제외)", outlier_thresh=0.3, min_cnt=1):
    winning_df = get_global_winning_df()
    matrix_rows, urgent_cards = [], []
    ltv_standards = load_ltv_standards()
    
    unique_regions = winning_df["시도"].dropna().unique()
    for reg in unique_regions:
        reg_winning = winning_df[winning_df["시도"] == reg]
        if reg_winning.empty: continue
        
        reg_last_date = reg_winning["매각일"].max()
        reg_group = reg_winning.groupby("분석용도")

        if ltv_standards is not None:
            std_info = ltv_standards[["구분", "담보종류"]].drop_duplicates()
            for _, row_std in std_info.iterrows():
                category, usage_type = row_std["구분"], row_std["담보종류"]
                if usage_type not in reg_group.groups: continue
                
                ltv_val = reg_group.get_group(usage_type)["적용LTV"].iloc[0]
                met = calculate_metrics(reg_winning, usage_type, ltv_val, reg_last_date, mode, outlier_thresh)
                signal = check_signal_logic(met, ltv_val, min_cnt)

                if signal:
                    urgent_cards.append({"reg": reg, "category": category, "usage_type": usage_type, "ltv_val": ltv_val, "met": met, "signal": signal})
                
                row = {"지역": reg, "대분류": category, "용도": usage_type, "LTV": ltv_val, "signal_tone": signal["tone"] if signal else None}
                for m_num, m_lbl in FIXED_MONTHS:
                    row[m_lbl] = classify_period(met["avg"].get(m_num), ltv_val, met["count"].get(m_num, 0), min_cnt)
                    row[f"{m_lbl}_count"] = met["count"].get(m_num, 0)
                matrix_rows.append(row)
                
    return pd.DataFrame(matrix_rows), urgent_cards


# ==========================================
# LLM 통신 계층
# ==========================================
def get_monthly_cache_file():
    return os.path.join(CACHE_DIR, f"llm_advice_{datetime.now().strftime('%Y_%m')}.json")

def load_monthly_cache():
    path = get_monthly_cache_file()
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            try: return json.load(f)
            except: pass
    return {}

def save_to_monthly_cache(key, advice_data):
    path = get_monthly_cache_file()
    with CACHE_LOCK:
        cache = load_monthly_cache()
        cache[key] = advice_data
        with open(path, "w", encoding="utf-8") as f:
            json.dump(cache, f, ensure_ascii=False, indent=2)

def fetch_all_advice(urgent_list):
    monthly_cache = load_monthly_cache()

    def process_item(item):
        met = item["met"]
        info = {
            "region": item["reg"], "usage": item["usage_type"], "current_ltv": item["ltv_val"],
            "avg3": met["avg"][3] or 0.0, "cnt3": met["count"][3],
            "avg12": met["avg"][12] or 0.0, "cnt12": met["count"][12],
            "avg36": met["avg"][36] or 0.0, "cnt36": met["count"][36]
        }
        cache_key = f"{info['region']}_{info['usage']}_{info['current_ltv']}"
        
        if cache_key in monthly_cache:
            advice = monthly_cache[cache_key]
        else:
            advice = llm_advisor.get_ltv_advice(info)
            save_to_monthly_cache(cache_key, advice)
            
        return {**item, **advice, "reason": advice.get("reason", "").replace("\n", " ")}

    with ThreadPoolExecutor(max_workers=5) as executor:
        results = list(executor.map(process_item, urgent_list))
    return pd.DataFrame(results) if results else pd.DataFrame()
