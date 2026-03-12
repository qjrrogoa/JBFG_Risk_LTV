import pandas as pd
import streamlit as st
import plotly.express as px
import plotly.graph_objects as go
from datetime import datetime
from dateutil.relativedelta import relativedelta

# 페이지 설정
st.set_page_config(layout="wide", page_title="LTV 분석 대시보드")

# 색상 팔레트
COLORS = px.colors.qualitative.Plotly

# LTV 설정 (사용f자 요청)
LTV_CONFIG = {
    "주택": {
        "단독주택": 75,
        "다가구": 60,
        "아파트": 80,
        "연립": 70, # 매핑: 연립 -> 연립주택 등 데이터 확인 필요
        "다세대": 60,
        "근린주택": 65
    },
    "건물": {
        "근린상가": 60,
        "공장": 75,
        "아파트상가": 55,
        "오피스텔": 65, # 매핑: 오피스텔(주거/상가) 포함
        "의료시설": 50, # 매핑: 병원 -> 의료시설
        "숙박시설": 50
    },
    "토지": {
        "대지": 75, # 매핑: 나대지 -> 대지
        "전": 60,
        "답": 75,
        "임야": 65
    }
}

# 데이터 매핑 헬퍼
def map_usage_to_config(usage):
    # 데이터 상의 용도를 설정 상의 키로 변환
    if not isinstance(usage, str):
        return str(usage)
        
    if usage in ["연립주택", "연립"]: return "연립"
    if usage in ["병원", "의료시설"]: return "의료시설"
    if "오피스텔" in usage: return "오피스텔"
    if "나대지" in usage or usage == "대지": return "대지"
    return usage

@st.cache_data
def load_data(file_path):
    df = pd.read_csv(file_path)
    
    def parse_currency(x):
        if isinstance(x, str):
            return int(x.replace(',', ''))
        return x

    def parse_percentage(x):
        if isinstance(x, str):
            return float(x.replace('%', ''))
        return x

    df['낙찰가'] = df['낙찰가'].apply(parse_currency)
    df['감정가'] = df['감정가'].apply(parse_currency)
    df['낙찰율'] = df['낙찰율'].apply(parse_percentage)
    df['매각일'] = pd.to_datetime(df['매각일'])
    
    # 설정 키로 매핑된 '분석용도' 컬럼 생성
    df['분석용도'] = df['용도'].apply(map_usage_to_config)
    
    # 기존 낙찰/매각 필터링 로직 제거 (전체 데이터 기준으로 날짜를 잡기 위함)
    # 분석 시에만 필터링하도록 변경
    
    return df

# 데이터 로드
try:
    df_gwangju = load_data('data/gwangju.csv')
    df_seoul = load_data('data/seoul.csv')
    df = pd.concat([df_gwangju, df_seoul], ignore_index=True)
except FileNotFoundError:
    st.error("데이터 파일(gwangju.csv 또는 seoul.csv)을 찾을 수 없습니다.")
    st.stop()
except Exception as e:
    # 혹시 '결과' 컬럼 문제 등 발생 시 에러 메시지
    st.error(f"데이터 로드 중 오류 발생: {e}")
    st.stop()

# 1. 초기 상태 관리 (session_state 초기화)
if "region_select" not in st.session_state:
    unique_regions_init = df['시도'].dropna().unique()
    st.session_state["region_select"] = "광주" if "광주" in unique_regions_init else unique_regions_init[0]

if "analysis_mode_select" not in st.session_state:
    st.session_state["analysis_mode_select"] = "월별 (최근)"

if "min_count_val" not in st.session_state:
    st.session_state["min_count_val"] = 1

if "outlier_val" not in st.session_state:
    st.session_state["outlier_val"] = 20.0

if "category_selector" not in st.session_state:
    st.session_state["category_selector"] = "주택"

# 2. 변수 할당 (UI에서 업데이트 되기 전의 데이터 필터링을 위함)
selected_region = st.session_state["region_select"]
analysis_mode = st.session_state["analysis_mode_select"]
min_count = st.session_state["min_count_val"]
selected_category = st.session_state["category_selector"]

# 데이터 필터링 (지역 기준)
filtered_df = df[df['시도'] == selected_region].copy()

st.title(f"[{selected_region}] 담보인정비율(LTV) 적정성 점검")

# 분석용 데이터 (낙찰/매각 건만 필터링)
if '결과' in filtered_df.columns:
    winning_df = filtered_df[filtered_df['결과'].astype(str).str.contains('낙찰|매각', na=False)].copy()
else:
    # 결과 컬럼이 없으면 일단 전체 사용 (또는 낙찰가 > 0 등 다른 로직)
    winning_df = filtered_df.copy()

# 기준일 설정 (낙찰/매각 건 기준)
if not winning_df.empty:
    last_date = winning_df['매각일'].max()
else:
    last_date = datetime.now()

st.markdown(f"**데이터 기준일:** {last_date.date()}")

# 극단값 제외 기준 설정값 가져오기 (UI는 하단에 위치)
if "outlier_val" not in st.session_state:
    st.session_state["outlier_val"] = 20.0

if analysis_mode == "월별 (극단값 제외)":
    outlier_threshold = st.session_state["outlier_val"] / 100.0
else:
    outlier_threshold = 0.2

# 분석 로직 함수 - Helper functions
def calculate_metrics(df, target_usage, ltv, current_date, mode, outlier_thresh):
    sub_df = df[df['분석용도'] == target_usage].copy()
    
    # 극단값 제외 모드일 경우 필터링
    if mode == "월별 (극단값 제외)":
        limit = ltv * outlier_thresh
        filtered_sub_df = sub_df[abs(sub_df['낙찰율'] - ltv) <= limit]
    else:
        filtered_sub_df = sub_df.copy()

    if mode == "월별 (최근)" or mode == "월별 (극단값 제외)":
        def get_avg_months(months):
            start_date = current_date - relativedelta(months=months)
            mask = (filtered_sub_df['매각일'] > start_date) & (filtered_sub_df['매각일'] <= current_date)
            period_df = filtered_sub_df.loc[mask]
            if period_df.empty: return None
            return period_df['낙찰율'].mean()
            
        def get_count_months(months):
            start_date = current_date - relativedelta(months=months)
            
            mask_orig = (sub_df['매각일'] > start_date) & (sub_df['매각일'] <= current_date)
            orig_count = len(sub_df.loc[mask_orig])
            
            mask_filtered = (filtered_sub_df['매각일'] > start_date) & (filtered_sub_df['매각일'] <= current_date)
            filtered_count = len(filtered_sub_df.loc[mask_filtered])
            
            return filtered_count, orig_count - filtered_count
            
        results = {'avg': {}, 'count': {}, 'excl': {}}
        for m in [3, 6, 12, 36, 60]:
            results['avg'][m] = get_avg_months(m)
            fc, diff = get_count_months(m)
            results['count'][m] = fc
            results['excl'][m] = diff
            
        return results
        
    return None

# 0. 기간별 적정성 판단 요약 테이블
period_judgment_data = []

# 1. 용도별 적정성 검토 요약 테이블
summary_data = []

# 정해진 고정 기간 매핑 (화면에 보여줄 이름 및 개월수 매칭)
fixed_months = [(3, "3개월"), (6, "6개월"), (12, "12개월"), (36, "3년 평균"), (60, "5년 평균")]
period_months = [(3, "3개월"), (6, "6개월"), (12, "12개월"), (36, "3년"), (60, "5년")]

# 컬럼명 설정 (동적 라벨링 제거)
cols_labels = [m[1] for m in fixed_months]
resample_rule = 'ME'

# 그래프를 그릴 항목을 추적하기 위한 리스트
valid_items_for_graph = []

for category, types in LTV_CONFIG.items():
    for usage_type, ltv in types.items():
        metrics = calculate_metrics(
            winning_df, usage_type, ltv, last_date, analysis_mode, outlier_threshold
        )
        
        if metrics is None:
            continue
            
        def format_val_with_gap_and_count(val, target_ltv, count):
            if val is None:
                return "-"
            gap = val - target_ltv
            return f"{val:.2f}% ({gap:+.2f}%)<br><span style='font-size: 0.85em; color: gray;'>[{int(count)}건]</span>"
            
        val1 = format_val_with_gap_and_count(metrics['avg'][3], ltv, metrics['count'][3])
        val2 = format_val_with_gap_and_count(metrics['avg'][6], ltv, metrics['count'][6])
        val3 = format_val_with_gap_and_count(metrics['avg'][12], ltv, metrics['count'][12])
        avg_3y = format_val_with_gap_and_count(metrics['avg'][36], ltv, metrics['count'][36])
        avg_5y = format_val_with_gap_and_count(metrics['avg'][60], ltv, metrics['count'][60])
        
        # 건수는 가장 최근인 3개월 기준으로 하거나 어떻게 할지 정해야하지만 기존 로직 유지
        b_count_default = metrics['count'].get(12, 0)
        excl_count_default = metrics['excl'].get(12, 0)
        
        # 적정성은 12개월(기본) 기준으로 평가 (기존 비교 기준B 역할 대체)
        target_val = metrics['avg'].get(12, None)
        
        def get_judgment(val, count):
            
            if (val is None) or (count is None) or (count < min_count):
                return "모수 부족", "⚪", "-"
            
            gap_val = val - ltv
            if abs(gap_val) <= 5: return "현행 유지", "🟢", gap_val
            elif abs(gap_val) <= 10: return "조정 여부 검토", "🟡", gap_val
            else: return "조정 필요", "🔴", gap_val

        judgment, action_step, _ = get_judgment(target_val, b_count_default)
        
        if category == selected_category and judgment != "모수 부족":
            valid_items_for_graph.append(usage_type)

        # 1. 기존 표 데이터 모으기
        row_data = {
            "대분류": category,
            "용도": usage_type,
            "LTV(A)": ltv,
            cols_labels[0]: val1,
            cols_labels[1]: val2,
            cols_labels[2]: val3,
            cols_labels[3]: avg_3y,
            cols_labels[4]: avg_5y,
        }

        summary_data.append(row_data)

        # 0. 신규 기간별 판단 데이터 모으기
        pj_row = {"대분류": category, "용도": usage_type, "LTV(A)": ltv}
        for m, m_lbl in period_months:
            _, action_step, _ = get_judgment(metrics['avg'].get(m, None), metrics['count'].get(m, 0))
            pj_row[m_lbl] = action_step
        period_judgment_data.append(pj_row)

summary_df = pd.DataFrame(summary_data)
period_judgment_df = pd.DataFrame(period_judgment_data)



# -----------------------------------------------------------------------------
# [New Feature] 상세 분석 팝업 (st.dialog) - Rolling Average Logic
# -----------------------------------------------------------------------------
@st.dialog("상세 분석 결과", width="large")
def show_details_dialog(category, usage_type, ltv, df, mode, outlier_thresh):
    st.subheader(f"[{category} > {usage_type}] 낙찰가율 추이 분석")
    st.markdown(f"**LTV 기준:** {ltv}%")

    # 데이터 필터링 (해당 용도)
    sub_df = df[df['분석용도'] == usage_type].copy()
    
    # [NEW] 선택된 탭(mode)에 따라 극단값 제외 로직 동적 적용
    if mode == "월별 (극단값 제외)":
        limit = ltv * outlier_thresh
        sub_df = sub_df[abs(sub_df['낙찰율'] - ltv) <= limit]
    
    if sub_df.empty:
        st.warning("해당 용도의 데이터가 없습니다.")
        return

    # 날짜 범위: 최근 2년 데이터를 보여주되, Rolling 계산을 위해 앞쪽 데이터도 필요함
    # 따라서 3년 전부터 가져와서 Rolling 계산 후 최근 2년만 잘라내기
    end_date = sub_df['매각일'].max()
    start_date = end_date - relativedelta(years=3) 
    
    # 분석 대상 전체 데이터 (Rolling 계산용)
    mask = (sub_df['매각일'] >= start_date) & (sub_df['매각일'] <= end_date)
    chart_df = sub_df.loc[mask].copy()

    if chart_df.empty:
        st.warning("분석할 데이터가 부족합니다.")
        return

    # 1. 월별로 Resample (빈 달은 NaN이 됨 -> interpolate or leave as NaN)
    #    낙찰가율은 연속적인 값이므로 ffill 보다는 interpolate나 그냥 NaN 유지 후 rolling(min_periods) 고려
    #    여기서는 거래가 없었던 달은 '직전 거래'를 따라가는 게 합리적일 수 있음 (ffill)
    chart_df = chart_df.set_index('매각일').sort_index()
    monthly_series = chart_df.resample('ME')['낙찰율'].mean()
    
    # 결측치 처리: 거래 없는 달은 NaN. Rolling 계산 시 min_periods 설정으로 처리 가능.
    # 하지만 시각적으로 끊어지면 안 예쁘므로, 
    # '해당 월 평균'은 점으로(Scatter), '이동평균선'은 선으로(Line) 표현.
    
    # ----------------------------------------
    # 이동 평균 (Rolling Average) 계산
    # ----------------------------------------
    # 1. 월별 (Monthly) - 그대로 사용
    monthly = monthly_series

    # 2. 3개월 이동평균 (Quarterly Trend)
    rolling_3m = monthly_series.rolling(window=3, min_periods=1).mean()

    # 3. 6개월 이동평균 (Half-Yearly Trend)
    rolling_6m = monthly_series.rolling(window=6, min_periods=1).mean()

    # 4. 12개월 이동평균 (Yearly Trend)
    rolling_12m = monthly_series.rolling(window=12, min_periods=1).mean()
    
    # 시각화 범위: 최근 2년
    view_start_date = end_date - relativedelta(years=2)
    view_mask = monthly.index >= view_start_date
    
    monthly = monthly.loc[view_mask]
    rolling_3m = rolling_3m.loc[view_mask]
    rolling_6m = rolling_6m.loc[view_mask]
    rolling_12m = rolling_12m.loc[view_mask]

    # ----------------------------------------
    # 그래프 그리기
    # ----------------------------------------
    fig = go.Figure()

    # 배경 색상 밴드 (신뢰구간 느낌) - 메인 그래프와 동일하게 적용
    # 1. 현행 유지 (Green): LTV ± 5%
    fig.add_hrect(y0=ltv-5, y1=ltv+5, line_width=0, fillcolor="green", opacity=0.1)
    
    # 2. 조정 여부 검토 (Yellow): LTV ± 10% (Green 영역 제외)
    # 위쪽 영역 (LTV+5 ~ LTV+10)
    fig.add_hrect(y0=ltv+5, y1=ltv+10, line_width=0, fillcolor="yellow", opacity=0.1)
    # 아래쪽 영역 (LTV-10 ~ LTV-5)
    fig.add_hrect(y0=ltv-10, y1=ltv-5, line_width=0, fillcolor="yellow", opacity=0.1)

    # 3. 조정 필요 (Red): LTV ± 10% 초과
    # 위쪽 영역 (LTV+10 ~ )
    fig.add_hrect(y0=ltv+10, y1=200, line_width=0, fillcolor="red", opacity=0.05)
    # 아래쪽 영역 ( ~ LTV-10)
    fig.add_hrect(y0=0, y1=ltv-10, line_width=0, fillcolor="red", opacity=0.05)

    # 월별 데이터 (Scatter + 얇은 선)
    fig.add_trace(go.Scatter(
        x=monthly.index, y=monthly.values,
        mode='lines+markers',
        name='월별 평균(실제값)',
        line=dict(color='gray', width=1, dash='dot'),
        marker=dict(size=4, color='gray', opacity=0.5)
    ))

    # 3개월 이동평균
    fig.add_trace(go.Scatter(
        x=rolling_3m.index, y=rolling_3m.values,
        mode='lines',
        name='3개월 이동평균',
        line=dict(color='#1f77b4', width=1.5, dash='dot')
    ))

    # 6개월 이동평균
    fig.add_trace(go.Scatter(
        x=rolling_6m.index, y=rolling_6m.values,
        mode='lines',
        name='6개월 이동평균',
        line=dict(color='#9467bd', width=2)
    ))

    # 12개월 이동평균 (가장 중요)
    fig.add_trace(go.Scatter(
        x=rolling_12m.index, y=rolling_12m.values,
        mode='lines',
        name='12개월 이동평균',
        line=dict(color='#ff7f0e', width=3)
    ))

    # LTV 기준선
    fig.add_hline(y=ltv, line_dash="solid", line_color="red", line_width=1, annotation_text=f"LTV {ltv}%")

    # Y축 범위 자동 설정 (데이터 기준 + 여유분 + 최소 100 보장)
    # 데이터의 Min/Max와 LTV 기준선을 모두 고려
    all_values = []
    if not monthly.empty: all_values.extend(monthly.values)
    if not rolling_12m.empty: all_values.extend(rolling_12m.dropna().values)
    all_values.append(ltv) # LTV 선은 항상 보여야 함
    
    y_min = min(all_values) if all_values else 0
    y_max = max(all_values) if all_values else 100
    
    # 여유분 추가 (위아래 10% 정도) 및 최소 100 보장
    y_range_min = max(0, y_min - 10) # 0 밑으로는 안 내려가게
    y_range_max = max(100, y_max + 10)

    fig.update_layout(
        title="이동평균 기반 낙찰가율 추이 (최근 2년)",
        xaxis_title="기준일",
        yaxis_title="낙찰가율(%)",
        yaxis_range=[y_range_min, y_range_max],
        hovermode="x unified",
        legend=dict(
            orientation="h",
            yanchor="bottom",
            y=1.02,
            xanchor="right",
            x=1
        ),
        margin=dict(l=20, r=20, t=60, b=20)
    )

    st.plotly_chart(fig, use_container_width=True)
    
    st.info("""
    **💡 그래프 보는 법**
    - **12개월 이동평균(주황색 굵은 선)**: 장기적인 추세를 보여줍니다.
    - **6개월/3개월 이동평균**: 중단기적인 변화 흐름을 보여줍니다.
    - **월별 평균(회색 점과 점선)**
    """)

    # 4. 상세 시계열 분석 차트 (최근 1년 월별 낙찰율)
    st.markdown(f"### {category} 부문 상세 시계열 분석 ({mode})")
    
    graph_start_date = last_date - relativedelta(months=12)
    graph_mask = (sub_df['매각일'] >= graph_start_date) & (sub_df['매각일'] <= last_date)
    graph_df = sub_df.loc[graph_mask]
    
    if not graph_df.empty:
        monthly_avg = graph_df.set_index('매각일').resample('ME')['낙찰율'].mean().reset_index()
        
        if not monthly_avg.empty:
            fig2 = go.Figure()
            
            fig2.add_hrect(y0=ltv-5, y1=ltv+5, line_width=0, fillcolor="green", opacity=0.1)
            fig2.add_hrect(y0=ltv+5, y1=ltv+10, line_width=0, fillcolor="yellow", opacity=0.1)
            fig2.add_hrect(y0=ltv-10, y1=ltv-5, line_width=0, fillcolor="yellow", opacity=0.1)
            
            all_graph_values = [ltv]
            all_graph_values.extend(monthly_avg['낙찰율'].dropna().tolist())
            y_target_max = max(all_graph_values) if all_graph_values else 100
            y_range_max2 = max(100, y_target_max + 10)
            
            fig2.add_hrect(y0=ltv+10, y1=y_range_max2, line_width=0, fillcolor="red", opacity=0.05)
            fig2.add_hrect(y0=0, y1=ltv-10, line_width=0, fillcolor="red", opacity=0.05)
            
            fig2.add_hline(y=ltv, line_dash="dash", line_color="black", line_width=1, annotation_text=f"LTV {ltv}%", annotation_position="bottom right")
            
            fig2.add_trace(go.Scatter(
                x=monthly_avg['매각일'],
                y=monthly_avg['낙찰율'],
                mode='lines+markers',
                name='낙찰율 (월별)',
                line=dict(color='blue', width=2),
                marker=dict(size=4)
            ))
            
            fig2.add_trace(go.Scatter(x=[None], y=[None], mode='markers', marker=dict(color='green', size=10, symbol='square'), name='현행유지'))
            fig2.add_trace(go.Scatter(x=[None], y=[None], mode='markers', marker=dict(color='#FDFD96', size=10, symbol='square'), name='조정검토'))
            fig2.add_trace(go.Scatter(x=[None], y=[None], mode='markers', marker=dict(color='#FFCCCB', size=10, symbol='square'), name='조정필요'))
            
            fig2.update_layout(
                title=f"{usage_type} (LTV: {ltv}%)",
                xaxis_title="",
                xaxis=dict(tickformat="%y.%m", dtick="M1" if mode in ["월별 (최근)", "월별 (극단값 제외)"] else "M3"),
                yaxis_title="낙찰율",
                yaxis_range=[0, y_range_max2],
                height=400,
                margin=dict(l=20, r=20, t=40, b=20),
                showlegend=True,
                legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1)
            )
            
            st.plotly_chart(fig2, use_container_width=True)

st.subheader("기간별 적정성 요약")

# --- UI 컨트롤 영역 ---
unique_regions = df['시도'].dropna().unique()

# 분석 기준 모드 선택 UI 추가 (기존 코드에서 상단에 있던 radio)
analysis_mode_ui = st.radio("분석 기준 선택", ["월별 (최근)", "월별 (극단값 제외)"], horizontal=True, key="analysis_mode_select")

# 전체 너비 중 절반(1:1:1의 3비율)만 사용하고 나머지 절반(3)은 공백으로 둡니다.
col_opt1, col_opt2, col_opt3, _ = st.columns([1, 1, 1, 3])
with col_opt1:
    st.selectbox("지역 선택", unique_regions, key="region_select")
with col_opt2:
    st.number_input("최소 건수", min_value=1, max_value=10000, step=1, key="min_count_val")
with col_opt3:
    if analysis_mode == "월별 (극단값 제외)":
        st.number_input("극단값 제외 기준 (%)", min_value=1.0, max_value=100.0, step=1.0, value=20.0, key="outlier_val")

# 색상 적용 로직 및 CSS (공통 사용)
def get_color_style(val):
    if val == "조정 필요": return 'background-color: #5a1e1e; color: #ffcccc; font-size: 1.2em'
    elif val == "조정 여부 검토": return 'background-color: #5a5a1e; color: #ffffcc; font-size: 1.2em'
    elif val == "모수 부족": return 'background-color: #3e3e3e; color: #cccccc; font-style: italic; font-size: 1.2em'
    elif val == "현행 유지": return 'background-color: #1e4620; color: #ccffcc; font-weight: bold; font-size: 1.2em'
    elif val in ["🔴", "🟡", "⚪", "🟢"]: return 'background-color: transparent; font-size: 1.0em'
    return ''

custom_css = """
<style>
    table {
        width: 100%;
        border-collapse: collapse;
        color: #e0e0e0;
        font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif;
        font-size: 14px;
        background-color: #262730; /* 테이블 전체 배경 */
    }
    th {
        background-color: #0e1117 !important; /* 헤더 배경 강제 적용 */
        color: #ffffff !important;
        font-weight: 600;
        text-align: center;
        vertical-align: middle;
        border: 1px solid #444;
        padding: 12px 10px;
    }
    td {
        text-align: center;
        vertical-align: middle;
        border: 1px solid #444;
        padding: 10px 8px;
        background-color: #262730; /* 기본 셀 배경 */
        color: #e0e0e0;
    }
    /* 대분류(인덱스 레벨0) 스타일 */
    tbody th {
        background-color: #262730 !important;
        color: #e0e0e0 !important;
        font-weight: bold;
        border-right: 1px solid #444;
        border-bottom: 1px solid #444;
        vertical-align: middle;
    }
    /* 마우스 호버 효과 */
    tr:hover td {
        background-color: #363945 !important; /* 호버 시 약간 밝게 */
        transition: 0.1s;
    }
</style>
"""

common_table_styles = [
    {'selector': 'th', 'props': [
        ('text-align', 'center'), ('vertical-align', 'middle'), 
        ('background-color', '#0e1117'), ('color', '#fafafa'), 
        ('font-weight', 'bold'), ('border-bottom', '1px solid #444')
    ]},
    {'selector': 'td', 'props': [('text-align', 'center'), ('vertical-align', 'middle')]}
]

# 0. 기간별 테이블 렌더링
period_display_df = period_judgment_df.copy()
period_display_df = period_display_df.set_index(["대분류", "용도"])
period_display_df["LTV(A)"] = period_display_df["LTV(A)"].apply(lambda x: f"{x:.0f}%" if isinstance(x, (int, float)) else x)

# 기간 컬럼(.col1 ~ .col5)만 균등 너비 지정
period_col_styles = common_table_styles + [
    # LTV(A): col0은 자연 너비 (고정 좁게)
    {'selector': 'th.col0, td.col0', 'props': [('width', '7%')]},
    # 기간 컬럼 5개: col1~col5은 동일 너비
    {'selector': 'th.col1, td.col1, th.col2, td.col2, th.col3, td.col3, th.col4, td.col4, th.col5, td.col5',
     'props': [('width', '16%')]},
]

p_styler = period_display_df.style.set_properties(**{'text-align': 'center', 'vertical-align': 'middle'})
p_styler.set_table_styles(period_col_styles)
for m_lbl in [m[1] for m in period_months]:
    p_styler.map(get_color_style, subset=[m_lbl])

# table-layout: fixed로 지정된 너비가 실제로 적용되게 함
period_css = """
<style>
#period-table table {
    table-layout: fixed;
    width: 100%;
}
</style>
"""
period_html = f'<div id="period-table">{p_styler.to_html()}</div>'

st.markdown(custom_css + period_css + period_html, unsafe_allow_html=True)
st.write("")
st.divider()

with st.expander(f"용도별 적정성 검토 및 {selected_category} 부문 상세 시계열 분석 ({analysis_mode})", expanded=False):
    st.subheader("1. 용도별 적정성 검토 요약")

    # 헤더 및 데이터를 스크롤 가능한 컨테이너에 넣기
    with st.container(height=400):
        # 헤더 렌더링
        summary_header_cols = st.columns([1.0, 1.2, 0.8, 1.2, 1.2, 1.2, 1.2, 1.2, 1.2])
        summary_headers = ["대분류", "용도", "LTV(A)"] + cols_labels + ["상세"]
        
        for col, header in zip(summary_header_cols, summary_headers):
            col.markdown(f"<div style='text-align: center;'><b>{header}</b></div>", unsafe_allow_html=True)

        st.divider()

        # 데이터 행 렌더링
        for idx, row in summary_df.iterrows():
            cat = row["대분류"]
            use = row["용도"]
            ltv_val = row["LTV(A)"]
            
            row_cols = st.columns([1.0, 1.2, 0.8, 1.2, 1.2, 1.2, 1.2, 1.2, 1.2])
            
            # 헬퍼 함수 (가운데 정렬)
            def center(text):
                return f"<div style='text-align: center; margin-top: 8px;'>{text}</div>"
                
            row_cols[0].markdown(center(cat), unsafe_allow_html=True)
            row_cols[1].markdown(center(use), unsafe_allow_html=True)
            row_cols[2].markdown(center(f"{ltv_val:.0f}%"), unsafe_allow_html=True)
            row_cols[3].markdown(center(row[cols_labels[0]]), unsafe_allow_html=True)
            row_cols[4].markdown(center(row[cols_labels[1]]), unsafe_allow_html=True)
            row_cols[5].markdown(center(row[cols_labels[2]]), unsafe_allow_html=True)
            row_cols[6].markdown(center(row[cols_labels[3]]), unsafe_allow_html=True)
            row_cols[7].markdown(center(row[cols_labels[4]]), unsafe_allow_html=True)
            
            btn_key_summary = f"summary_detail_btn_{cat}_{use}"
            with row_cols[8]:
                if st.button("상세 사항 검토", key=btn_key_summary, use_container_width=True):
                    found_ltv = ltv_val
                    show_details_dialog(cat, use, found_ltv, winning_df, analysis_mode, outlier_threshold)
                    
            st.markdown("<hr style='margin: 0px;'>", unsafe_allow_html=True)
