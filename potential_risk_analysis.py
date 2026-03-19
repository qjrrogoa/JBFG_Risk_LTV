import pandas as pd
import streamlit as st
import glob
from dateutil.relativedelta import relativedelta

st.set_page_config(layout="wide", page_title="잠재 리스크 분석 (Gap >= 10%)")

# =========================================================
# 데이터 로드 로직 (app - 복사본.py와 동일하게 유지)
# =========================================================

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
        "충남": "충남", "경북": "경북", "경남": "경남", "제주": "제주", "강원": "강원"
    }
    return s_si_do.map(mapping).fillna("경기")

@st.cache_data
def load_ltv_standards():
    try:
        return pd.read_csv("data/LTV_기준(광주은행).csv", encoding="utf-8-sig")
    except:
        return None

def load_data(file_path, ltv_standards):
    df = pd.read_csv(file_path)
    
    # 숫자 변환
    for col in ["낙찰가", "감정가"]:
        if df[col].dtype == 'object':
            df[col] = df[col].str.replace(",", "").astype(float)
    if df["낙찰율"].dtype == 'object':
        df["낙찰율"] = df["낙찰율"].str.replace("%", "").astype(float)
    
    df["매각일"] = pd.to_datetime(df["매각일"])
    
    # 용도 매핑
    if "LTV_광주" in df.columns:
        df["분석용도"] = df["LTV_광주"]
    else:
        # Fallback (전처리 안된 경우)
        df["분석용도"] = df["용도"]

    df["_LTV지역구분"] = get_ltv_col_name_vec(df["시도"])

    if ltv_standards is not None:
        std_melted = ltv_standards.melt(
            id_vars=["구분", "담보종류"],
            var_name="_LTV지역구분",
            value_name="적용LTV"
        )
        df = df.merge(
            std_melted[["담보종류", "_LTV지역구분", "적용LTV"]],
            left_on=["분석용도", "_LTV지역구분"],
            right_on=["담보종류", "_LTV지역구분"],
            how="left"
        )
    df["적용LTV"] = df["적용LTV"].fillna(80.0)
    return df

# =========================================================
# 분석 로직
# =========================================================

def calculate_period_metrics(df, last_date, ltv_val):
    periods = {
        "3M": last_date - relativedelta(months=3),
        "6M": last_date - relativedelta(months=6),
        "12M": last_date - relativedelta(months=12),
        "36M": last_date - relativedelta(months=36),
        "60M": last_date - relativedelta(months=60)
    }
    
    res = {}
    for p_name, p_date in periods.items():
        p_df = df[df["매각일"] > p_date]
        res[f"{p_name}_count"] = len(p_df)
        res[f"{p_name}_avg"] = p_df["낙찰율"].mean() if len(p_df) > 0 else None
        
        # 10% 이상 차이나는 항목만 추출
        gap_df = p_df[abs(p_df["낙찰율"] - ltv_val) >= 10]
        res[f"{p_name}_gap_count"] = len(gap_df)
        res[f"{p_name}_gap_avg"] = gap_df["낙찰율"].mean() if len(gap_df) > 0 else None
        
    return res

def check_red_signal_logic(metrics, ltv, min_val=1):
    # app - 복사본.py의 로직 재현
    avg3, avg6, avg12 = metrics["3M_avg"], metrics["6M_avg"], metrics["12M_avg"]
    cnt3, cnt6, cnt12, cnt36 = metrics["3M_count"], metrics["6M_count"], metrics["12M_count"], metrics["36_count"] if "36_count" in metrics else metrics["36M_count"]
    
    if not all(v is not None for v in [avg12, avg6, avg3]):
        return False
    
    # 1. 건수 체크 (is_sufficient)
    t12, t6, t3 = cnt36 / 3.0, cnt36 / 6.0, cnt36 / 12.0
    is_sufficient = (cnt36 >= min_val and cnt12 >= t12 /2 and cnt6 >= t6/2 and cnt3 >= t3/2)
    if not is_sufficient: return False
    
    # 2. Gap 체크 (is_red)
    g12, g6, g3 = abs(avg12 - ltv), abs(avg6 - ltv), abs(avg3 - ltv)
    is_red = all(g >= 9.95 for g in [g12, g6, g3]) # 10% 이상 (반올림 고려)
    if not is_red: return False
    
    # 3. 추세 체크 (direction)
    d12, d6, d3 = avg12 - ltv, avg6 - ltv, avg3 - ltv
    is_pos = all(d > 0 for d in [d12, d6, d3])
    is_neg = all(d < 0 for d in [d12, d6, d3])

    is_golden = (avg3 > avg6 > avg12)
    is_dead   = (avg3 < avg6 < avg12)
    
    direction = (is_pos and is_golden) or (is_neg and is_dead)
    
    return direction

# =========================================================
# UI 메인
# =========================================================

st.title("🚨 잠재적 리스크 분석 리스트")
st.markdown("#### [조건] 3M/6M/12M 낙찰율과 LTV 차이가 모두 10%p 이상이지만, 시그널 로직에 의해 '레드'로 표시되지 않는 항목들")

ltv_standards = load_ltv_standards()
if ltv_standards is None:
    st.error("LTV 기준 파일을 찾을 수 없습니다.")
    st.stop()

csv_files = [
    "data/gwangju.csv", 
    "data/seoul.csv", 
    "data/busan.csv", 
    "data/jeonnam.csv", 
    "data/jeonbuk.csv", 
    "data/daegu.csv", 
    "data/incheon.csv"
]
all_dfs = []
for f in csv_files:
    try:
        all_dfs.append(load_data(f, ltv_standards))
    except:
        pass

if not all_dfs:
    st.error("데이터 파일이 없습니다.")
    st.stop()

df_total = pd.concat(all_dfs, ignore_index=True)
winning_df = df_total[df_total["결과"].astype(str).str.contains("낙찰|매각", na=False)].copy()

# 분석용 지역/용도 그룹핑
results = []
unique_regions = winning_df["시도"].dropna().unique()

# 대분류 매핑용 (표시용)
std_info = ltv_standards[["구분", "담보종류"]].drop_duplicates()
cat_map = dict(zip(std_info["담보종류"], std_info["구분"]))

with st.status("전체 지역/용도 전수 조사 중...", expanded=True) as status:
    for reg in unique_regions:
        reg_df = winning_df[winning_df["시도"] == reg]
        last_date = reg_df["매각일"].max()
        
        for usage in reg_df["분석용도"].dropna().unique():
            usage_df = reg_df[reg_df["분석용도"] == usage]
            ltv_val = usage_df["적용LTV"].iloc[0]
            
            # 메트릭 계산
            metrics = calculate_period_metrics(usage_df, last_date, ltv_val)
            
            # 조건 1: 모든 기간의 Gap이 10%p 이상인가?
            avg3, avg6, avg12 = metrics["3M_avg"], metrics["6M_avg"], metrics["12M_avg"]
            if not all(v is not None for v in [avg3, avg6, avg12]):
                continue
                
            gap3, gap6, gap12 = abs(avg3 - ltv_val), abs(avg6 - ltv_val), abs(avg12 - ltv_val)
            
            if gap3 >= 10 and gap6 >= 10 and gap12 >= 10:
                # 조건 2: 현재 레드시그널인가?
                is_red_signal = check_red_signal_logic(metrics, ltv_val)
                
                if not is_red_signal:
                    # 왜 레드가 아닌지 분석
                    cnt36 = metrics["36M_count"]
                    cnt12, cnt6, cnt3 = metrics["12M_count"], metrics["6M_count"], metrics["3M_count"]
                    t12, t6, t3 = cnt36 / 3.0, cnt36 / 6.0, cnt36 / 12.0
                    is_sufficient = (cnt36 >= 1 and cnt12 >= t12 /2 and cnt6 >= t6 /2 and cnt3 >= t3 /2)
                    
                    # 3. 추세 체크 (direction) - 엄격한 기준 (오차 허용 제거)
                    d12, d6, d3 = avg12 - ltv_val, avg6 - ltv_val, avg3 - ltv_val
                    is_pos = all(d > 0 for d in [d12, d6, d3])
                    is_neg = all(d < 0 for d in [d12, d6, d3])
                    
                    is_golden = (avg3 > avg6 > avg12)
                    is_dead   = (avg3 < avg6 < avg12)
                    
                    has_trend = (is_pos and is_golden) or (is_neg and is_dead)
                    
                    reason = []
                    if not is_sufficient: reason.append("건수 부족")
                    if not has_trend: reason.append("일관된 추세 없음")
                    
                    results.append({
                        "지역": reg,
                        "대분류": cat_map.get(usage, "기타"),
                        "용도": usage,
                        "LTV": ltv_val,
                        "3M (평균/건수) | Gap≥10%(평균/건수)": f"{avg3:.1f}% ({cnt3}건) | {metrics['3M_gap_avg']:.1f}% ({metrics['3M_gap_count']}건)" if metrics['3M_gap_count'] > 0 else f"{avg3:.1f}% ({cnt3}건) | -",
                        "6M (평균/건수) | Gap≥10%(평균/건수)": f"{avg6:.1f}% ({cnt6}건) | {metrics['6M_gap_avg']:.1f}% ({metrics['6M_gap_count']}건)" if metrics['6M_gap_count'] > 0 else f"{avg6:.1f}% ({cnt6}건) | -",
                        "12M (평균/건수) | Gap≥10%(평균/건수)": f"{avg12:.1f}% ({cnt12}건) | {metrics['12M_gap_avg']:.1f}% ({metrics['12M_gap_count']}건)" if metrics['12M_gap_count'] > 0 else f"{avg12:.1f}% ({cnt12}건) | -",
                        "3년 (평균/건수) | Gap≥10%(평균/건수)": f"{metrics['36M_avg']:.1f}% ({metrics['36M_count']}건) | {metrics['36M_gap_avg']:.1f}% ({metrics['36M_gap_count']}건)" if metrics.get('36M_gap_count', 0) > 0 else (f"{metrics['36M_avg']:.1f}% ({metrics['36M_count']}건) | -" if metrics['36M_avg'] else "-"),
                        "5년 (평균/건수) | Gap≥10%(평균/건수)": f"{metrics['60M_avg']:.1f}% ({metrics['60M_count']}건) | {metrics['60M_gap_avg']:.1f}% ({metrics['60M_count']}건)" if metrics.get('60M_gap_count', 0) > 0 else (f"{metrics['60M_avg']:.1f}% ({metrics['60M_count']}건) | -" if metrics['60M_avg'] else "-"),
                        "최대Gap": f"{max(gap3, gap6, gap12):.1f}%p",
                        "미검출 사유": ", ".join(reason) if reason else "기타"
                    })
    status.update(label="조사 완료!", state="complete")

if results:
    res_df = pd.DataFrame(results)
    res_df.to_csv("data/potential_risk_list.csv",encoding="utf-8-sig", index=False)  
    st.dataframe(res_df, use_container_width=True)
    st.info(f"총 {len(results)}개의 잠재적 리스트 항목을 발견했습니다.")
else:
    st.success("조건에 해당하는 잠재적 리스크 항목이 없습니다.")
