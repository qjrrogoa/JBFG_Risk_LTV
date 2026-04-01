import streamlit as st
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
from datetime import datetime
import glob
import os

# 페이지 설정
st.set_page_config(page_title="낙찰가율 분포 분석", layout="wide")

st.markdown("""
<style>
    .main { background-color: #ffffff !important; }
    .stApp { background-color: #ffffff !important; }
    div[data-testid="stMetricValue"] { color: #0f172a; }
    .stPlotlyChart { 
        background-color: #ffffff; 
        border: 1px solid #f1f5f9;
        border-radius: 16px; 
        padding: 10px;
    }
    h1, h2, h3 { color: #1e293b !important; }
    .stInfo { background-color: #f8fafc; color: #475569; border: 1px solid #e2e8f0; }
</style>
""", unsafe_allow_html=True)

st.title("📊 낙찰가율 분포 심층 분석")
st.markdown("<p style='color: #64748b; font-size: 1.1rem;'>광주은행 LTV 기준 용도별 낙찰가율의 기간별 분포와 리스크를 분석합니다.</p>", unsafe_allow_html=True)

# 1. LTV 기준 용도 로드
@st.cache_data
def load_valid_usages():
    try:
        std_df = pd.read_csv("data/LTV_기준(광주은행).csv", encoding="utf-8-sig")
        return sorted(std_df["담보종류"].unique().tolist())
    except:
        return []

valid_usage_names = load_valid_usages()

def map_usage_to_config(usage):
    if not isinstance(usage, str): return str(usage)
    # 광주은행 LTV 기준 명칭에 매핑
    if usage in ["연립주택", "연립", "빌라"]: return "연립주택"
    if usage in ["다세대", "다세대주택"]: return "다세대주택"
    if usage in ["단독", "단독주택"]: return "단독"
    if usage in ["병원", "의료시설"]: return "병원"
    if "오피스텔" in usage: return "오피스텔"
    if "상가" in usage and "아파트" in usage: return "아파트상가"
    if "상가" in usage or "근린생활" in usage: return "근린상가"
    if "토지" in usage or "대지" in usage or usage == "나대지": return "나대지"
    if "공장" in usage: return "공장(지식산업센터 업무시설)"
    return usage

# 2. 데이터 로드
@st.cache_data
def load_all_data():
    files_to_load = [
        ("광주", "data/gwangju.csv"), ("서울", "data/seoul.csv"),
        ("부산", "data/busan.csv"), ("전남", "data/jeonnam.csv"),
        ("전북", "data/jeonbuk.csv"), ("대구", "data/daegu.csv"),
        ("인천", "data/incheon.csv"),
    ]
    
    all_dfs = []
    for region_name, path in files_to_load:
        if not os.path.exists(path): continue
        try:
            temp_df = pd.read_csv(path)
            if "낙찰율" in temp_df.columns:
                temp_df["낙찰율"] = pd.to_numeric(temp_df["낙찰율"].astype(str).str.replace("%", "").str.replace(",", ""), errors='coerce')
            if "매각일" in temp_df.columns:
                temp_df["매각일"] = pd.to_datetime(temp_df["매각일"], errors='coerce')
            
            if "용도" in temp_df.columns:
                temp_df["분석용도"] = temp_df["용도"].apply(map_usage_to_config)
            
            # LTV 기준에 존재하는 용도만 필터링
            temp_df = temp_df[temp_df["분석용도"].isin(valid_usage_names)]
            
            temp_df = temp_df.dropna(subset=["낙찰율", "매각일", "시도", "분석용도"])
            all_dfs.append(temp_df)
        except:
            continue
            
    return pd.concat(all_dfs, ignore_index=True) if all_dfs else pd.DataFrame()

df = load_all_data()

if df.empty:
    st.error("데이터를 불러올 수 없습니다. data 폴더의 CSV 파일들과 LTV 기준 파일을 확인해 주세요.")
    st.stop()

# 3. 필터 사이드바
st.sidebar.header("🔍 분석 필터")
region_list = sorted([str(x) for x in df["시도"].unique() if pd.notna(x)])
usage_list = sorted([str(x) for x in df["분석용도"].unique() if pd.notna(x)])

selected_region = st.sidebar.selectbox("지역 선택", region_list)
selected_usage = st.sidebar.selectbox("용도(LTV 기준) 선택", usage_list)

# 4. 기간별 데이터 추출
def get_period_data(df, region, usage):
    target_df = df[(df["시도"] == region) & (df["분석용도"] == usage)].copy()
    target_df = target_df.sort_values("매각일", ascending=False)
    
    if target_df.empty: return None

    ref_date = target_df["매각일"].max()
    periods = [
        (3, "최근 3개월"), (6, "최근 6개월"), (12, "최근 12개월"),
        (36, "최근 3년"), (60, "최근 5년")
    ]
    
    plot_data = []
    for months, label in periods:
        cutoff = ref_date - pd.DateOffset(months=months)
        p_df = target_df[target_df["매각일"] >= cutoff].copy()
        if not p_df.empty:
            p_df["Period"] = label
            plot_data.append(p_df)
            
    return pd.concat(plot_data) if plot_data else None

period_df = get_period_data(df, selected_region, selected_usage)

if period_df is None:
    st.warning(f"'{selected_region} - {selected_usage}'에 해당하는 데이터가 부족합니다.")
else:
    st.subheader(f"📈 {selected_region} · {selected_usage} 기간별 낙찰분포")
    
    # 상단 탭 구성
    tab1, tab2 = st.tabs(["📊 히스토그램 비교", "📈 누적 확률/통계"])

    with tab1:
        st.info("💡 여러 기간의 분포를 겹쳐서 보여줍니다. 분포가 좁고 높을수록 가격이 일정한 안정적 시장입니다.")
        fig_dist = px.histogram(
            period_df,
            x="낙찰율",
            color="Period",
            marginal="box", 
            barmode="overlay",
            nbins=40,
            opacity=0.5,
            labels={"낙찰율": "낙찰가율 (%)", "count": "건수"},
            category_orders={"Period": ["최근 3개월", "최근 6개월", "최근 12개월", "최근 3년", "최근 5년"]},
            template="plotly_white",
            color_discrete_sequence=px.colors.qualitative.Safe
        )
        fig_dist.update_layout(height=600, hovermode="x unified", margin=dict(l=20, r=20, t=50, b=20))
        st.plotly_chart(fig_dist, use_container_width=True)

    with tab2:
        col_c1, col_c2 = st.columns([1.2, 1])
        
        with col_c1:
            st.subheader("누적 확률 분포")
            fig_ecdf = px.ecdf(
                period_df,
                x="낙찰율",
                color="Period",
                labels={"낙찰율": "낙찰가율 (%)", "probability": "누적 비중"},
                category_orders={"Period": ["최근 3개월", "최근 6개월", "최근 12개월", "최근 3년", "최근 5년"]},
                template="plotly_white"
            )
            fig_ecdf.update_layout(height=450)
            st.plotly_chart(fig_ecdf, use_container_width=True)

        with col_c2:
            st.subheader("기간별 정밀 통계")
            stats = period_df.groupby("Period")["낙찰율"].agg(['count', 'mean', 'std', 'median']).reset_index()
            # 정렬 순서
            order = {"최근 3개월": 0, "최근 6개월": 1, "최근 12개월": 2, "최근 3년": 3, "최근 5년": 4}
            stats["sort"] = stats["Period"].map(order)
            stats = stats.sort_values("sort").drop("sort", axis=1)
            
            stats.columns = ["분석 기간", "건수", "평균(%)", "표준편차", "중앙값"]
            st.table(stats.style.format({
                "평균(%)": "{:.1f}", "표준편차": "{:.2f}", "중앙값": "{:.1f}"
            }))
            
            st.info("""
            **분석 가이드**
            - **표준편차**가 낮을수록 낙찰가 예측이 쉽습니다.
            - **중앙값**이 평균보다 높다면 고가 낙찰 비중이 큰 시장입니다.
            """)

st.markdown("---")
st.caption("JBFG Risk LTV Analysis Tool - Distribution Module")
