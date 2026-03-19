import pandas as pd
import streamlit as st
import plotly.express as px
import plotly.graph_objects as go
from datetime import datetime
from dateutil.relativedelta import relativedelta
import llm_advisor
import time
from concurrent.futures import ThreadPoolExecutor

st.set_page_config(layout="wide", page_title="LTV 적정성 대시보드")

# =========================================================
# 기본 설정
# =========================================================
COLORS = px.colors.qualitative.Plotly
FIXED_MONTHS = [(3, "3개월"), (6, "6개월"), (12, "12개월"), (36, "3년"), (60, "5년")]
COL_LABELS = [m[1] for m in FIXED_MONTHS]

# STATUS_MAP은 UI 상단 상태 표기용
STATUS_MAP = {
    "green": {"label": "적정", "bg": "#e8f7ee", "fg": "#1f7a42", "dot": "#22c55e"},
    "yellow": {"label": "주의", "bg": "#fff8db", "fg": "#9a6700", "dot": "#facc15"},
    "red": {"label": "부적정", "bg": "#feecec", "fg": "#b91c1c", "dot": "#ef4444"},
    "gray": {"label": "모수 부족", "bg": "#f3f4f6", "fg": "#6b7280", "dot": "#9ca3af"},
}


# =========================================================
# 데이터 로드
# =========================================================
@st.cache_data
def load_ltv_standards():
    try:
        # 광주은행 LTV 기준 파일 로드
        return pd.read_csv("data/LTV_기준(광주은행).csv", encoding="utf-8-sig")
    except Exception:
        return None


ltv_standards = load_ltv_standards()


def get_ltv_col_name_vec(s_si_do):
    mapping = {
        "서울특별시": "서울", "인천광역시": "인천", "경기도": "경기",
        "광주광역시": "광주", "전라남도": "전남", "전라북도": "전북",
        "부산광역시": "부산", "대전광역시": "대전", "대구광역시": "대구",
        "울산광역시": "울산", "세종특별자치시": "세종", "충청북도": "충북",
        "충청남도": "충남", "경상북도": "경북", "경상남도": "경남",
        "제주특별자치도": "제주", "강원도": "강원",
        # 축약된 이름 대응
        "서울": "서울", "인천": "인천", "경기": "경기", "광주": "광주",
        "전남": "전남", "전북": "전북", "부산": "부산", "대전": "대전",
        "대구": "대구", "울산": "울산", "세종": "세종", "충북": "충북",
        "충남": "충남", "경북": "경북", "경남": "경남", "제주": "제주", "강원": "강원"
    }
    return s_si_do.map(mapping).fillna("경기")


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


@st.cache_data
def load_data(file_path):
    df = pd.read_csv(file_path)

    def parse_currency(x):
        if isinstance(x, str):
            return int(x.replace(",", ""))
        return x

    def parse_percentage(x):
        if isinstance(x, str):
            return float(x.replace("%", ""))
        return x

    df["낙찰가"] = df["낙찰가"].apply(parse_currency)
    df["감정가"] = df["감정가"].apply(parse_currency)
    df["낙찰율"] = df["낙찰율"].apply(parse_percentage)
    df["매각일"] = pd.to_datetime(df["매각일"])

    # 전처리된 파일인 경우 LTV_광주 컬럼 우선 사용
    if "LTV_광주" in df.columns:
        df["분석용도"] = df["LTV_광주"]
    else:
        df["분석용도"] = df["용도"].apply(map_usage_to_config)

    df["_LTV지역구분"] = get_ltv_col_name_vec(df["시도"])

    if ltv_standards is not None:
        # 새로운 CSV 구조 (구분, 담보종류, 지역별 컬럼...)에 맞춰 melt
        std_melted = ltv_standards.melt(
            id_vars=["구분", "담보종류"],
            var_name="_LTV지역구분",
            value_name="적용LTV",
        )
        df = df.merge(
            std_melted[["담보종류", "_LTV지역구분", "적용LTV"]],
            left_on=["분석용도", "_LTV지역구분"],
            right_on=["담보종류", "_LTV지역구분"],
            how="left",
        )
        df["적용LTV"] = df["적용LTV"].fillna(80.0)
        df.drop(columns=["담보종류", "_LTV지역구분"], inplace=True)
    else:
        df["적용LTV"] = 80.0

    return df


def load_all_data():
    dfs = []
    for fname, path in [
        ("광주", "data/gwangju.csv"),
        ("서울", "data/seoul.csv"),
        ("부산", "data/busan.csv"),
        ("전남", "data/jeonnam.csv"),
        ("전북", "data/jeonbuk.csv"),
        ("대구", "data/daegu.csv"),
        ("인천", "data/incheon.csv"),
    ]:
        try:
            temp_df = load_data(path)
            dfs.append(temp_df)
        except Exception as e:
            st.warning(f"{fname} 데이터 로드 실패: {e}")

    if not dfs:
        st.error("데이터 파일들을 찾을 수 없습니다.")
        st.stop()

    return pd.concat(dfs, ignore_index=True)


df = load_all_data()
if "결과" in df.columns:
    global_winning_df = df[df["결과"].astype(str).str.contains("낙찰|매각", na=False)].copy()
else:
    global_winning_df = df.copy()


# =========================================================
# 세션 상태 초기화
# =========================================================
if "region_select_matrix" not in st.session_state:
    st.session_state["region_select_matrix"] = "전체 지역"
if "filter_category" not in st.session_state:
    st.session_state["filter_category"] = "전체"
if "filter_usage" not in st.session_state:
    st.session_state["filter_usage"] = "전체"
if "min_count_val" not in st.session_state:
    st.session_state["min_count_val"] = 1
if "filter_status_mode" not in st.session_state:
    st.session_state["filter_status_mode"] = "전체"
if "filter_outlier_on" not in st.session_state:
    st.session_state["filter_outlier_on"] = False
if "outlier_val" not in st.session_state:
    st.session_state["outlier_val"] = 20.0
if "show_detail_panel" not in st.session_state:
    st.session_state["show_detail_panel"] = False
if "filter_urgent_mode" not in st.session_state:
    st.session_state["filter_urgent_mode"] = "전체"
if "search_query" not in st.session_state:
    st.session_state["search_query"] = ""

min_count = st.session_state.get("min_count_val", 1)
analysis_mode = "월별 (극단값 제외)" if st.session_state.get("filter_outlier_on", False) else "월별 (최근)"
outlier_threshold = st.session_state.get("outlier_val", 20.0) / 100.0 if analysis_mode == "월별 (극단값 제외)" else 0.2


# =========================================================
# 분석 로직
# =========================================================
def calculate_metrics(source_df, target_usage, ltv, current_date, mode, outlier_thresh):
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


def classify_period(avg_value, ltv, count_value, min_required):
    if avg_value is None or count_value < min_required:
        return "gray"
    abs_gap = abs(avg_value - ltv)
    if abs_gap > 10:
        return "red"
    if abs_gap >= 5:
        return "yellow"
    return "green"


@st.dialog("상세 분석 결과", width="large")
def show_details_dialog(region, category, usage_type, ltv, src_df, mode, outlier_thresh):
    st.subheader(f"[{region}] {category} > {usage_type} 상세 분석")
    st.markdown(f"**현재 LTV 기준:** {ltv}%")

    reg_df = src_df[src_df["시도"] == region].copy()
    if reg_df.empty:
        st.warning("분석할 지역 데이터가 없습니다.")
        return

    last_dt = reg_df["매각일"].max()
    met = calculate_metrics(reg_df, usage_type, ltv, last_dt, mode, outlier_thresh)

    m_cols = st.columns(6)

    def draw_mini_card(col, label, avg, count, base_ltv, is_base=False):
        with col:
            val_color = "#ef4444" if is_base else "#0f172a"
            val_text = f"{avg:.0f}%" if is_base else (f"{avg:.1f}%" if avg is not None else "-")
            diff_text = ""
            if not is_base and avg is not None:
                diff = avg - base_ltv
                diff_text = f"<div style='font-size:11px; color:#64748b; margin-top:2px;'>{diff:+.1f}% ({int(count)}건)</div>"

            st.markdown(
                f"""
                <div style="text-align:center; padding:12px 5px; background:#f8fafc; border-radius:14px; border:1px solid #eef2f6; min-height:105px; display:flex; flex-direction:column; justify-content:center;">
                    <div style="font-size:11px; color:#94a3b8; font-weight:700; margin-bottom:4px; text-transform:uppercase;">{label}</div>
                    <div style="font-size:17px; font-weight:800; color:{val_color}; line-height:1.2;">{val_text}</div>
                    {diff_text}
                </div>
                """,
                unsafe_allow_html=True,
            )

    draw_mini_card(m_cols[0], "현재 LTV", ltv, 0, ltv, is_base=True)
    draw_mini_card(m_cols[1], "3개월", met["avg"][3], met["count"][3], ltv)
    draw_mini_card(m_cols[2], "6개월", met["avg"][6], met["count"][6], ltv)
    draw_mini_card(m_cols[3], "12개월", met["avg"][12], met["count"][12], ltv)
    draw_mini_card(m_cols[4], "3년", met["avg"][36], met["count"][36], ltv)
    draw_mini_card(m_cols[5], "5년", met["avg"][60], met["count"][60], ltv)

    st.write("")

    sub_df = reg_df[reg_df["분석용도"] == usage_type].copy()
    if mode == "월별 (극단값 제외)":
        limit = ltv * outlier_thresh
        sub_df = sub_df[abs(sub_df["낙찰율"] - ltv) <= limit]

    if sub_df.empty:
        st.warning("분석 가능한 데이터가 부족합니다.")
        return

    end_date = sub_df["매각일"].max()
    start_date = end_date - relativedelta(years=3)
    chart_df = sub_df[(sub_df["매각일"] >= start_date) & (sub_df["매각일"] <= end_date)].copy()

    if chart_df.empty:
        st.warning("분석할 데이터가 부족합니다.")
        return

    chart_df = chart_df.set_index("매각일").sort_index()
    monthly_series = chart_df.resample("ME")["낙찰율"].mean()

    monthly = monthly_series
    rolling_3m = monthly_series.rolling(window=3, min_periods=1).mean()
    rolling_6m = monthly_series.rolling(window=6, min_periods=1).mean()
    rolling_12m = monthly_series.rolling(window=12, min_periods=1).mean()

    view_start_date = end_date - relativedelta(years=2)
    view_mask = monthly.index >= view_start_date

    monthly = monthly.loc[view_mask]
    rolling_3m = rolling_3m.loc[view_mask]
    rolling_6m = rolling_6m.loc[view_mask]
    rolling_12m = rolling_12m.loc[view_mask]

    fig = go.Figure()
    fig.add_hrect(y0=ltv - 5, y1=ltv + 5, line_width=0, fillcolor="green", opacity=0.1)
    fig.add_hrect(y0=ltv + 5, y1=ltv + 10, line_width=0, fillcolor="yellow", opacity=0.1)
    fig.add_hrect(y0=ltv - 10, y1=ltv - 5, line_width=0, fillcolor="yellow", opacity=0.1)
    fig.add_hrect(y0=ltv + 10, y1=200, line_width=0, fillcolor="red", opacity=0.05)
    fig.add_hrect(y0=0, y1=ltv - 10, line_width=0, fillcolor="red", opacity=0.05)

    fig.add_trace(go.Scatter(
        x=monthly.index, y=monthly.values, mode='lines+markers', name='월별 평균(실제값)',
        line=dict(color='gray', width=1, dash='dot'),
        marker=dict(size=4, color='gray', opacity=0.5),
        connectgaps=True
    ))
    fig.add_trace(go.Scatter(
        x=rolling_3m.index, y=rolling_3m.values, mode='lines', name='3개월 이동평균',
        line=dict(color='#1f77b4', width=1.5, dash='dot'),
        connectgaps=True
    ))
    fig.add_trace(go.Scatter(
        x=rolling_6m.index, y=rolling_6m.values, mode='lines', name='6개월 이동평균',
        line=dict(color='#9467bd', width=2),
        connectgaps=True
    ))
    fig.add_trace(go.Scatter(
        x=rolling_12m.index, y=rolling_12m.values, mode='lines', name='12개월 이동평균',
        line=dict(color='#ff7f0e', width=3),
        connectgaps=True
    ))
    fig.add_hline(y=ltv, line_dash="solid", line_color="red", line_width=1, annotation_text=f"LTV {ltv}%")

    all_values = []
    if not monthly.empty:
        all_values.extend(monthly.values)
    if not rolling_12m.empty:
        all_values.extend(rolling_12m.dropna().values)
    all_values.append(ltv)
    y_min, y_max = min(all_values), max(all_values)

    fig.update_layout(
        title="이동평균 기반 낙찰가율 추이 (최근 2년)",
        xaxis_title="기준일",
        yaxis_title="낙찰가율(%)",
        yaxis_range=[max(0, y_min - 10), max(100, y_max + 10)],
        hovermode="x unified",
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
        margin=dict(l=10, r=10, t=60, b=20),
        height=380,
    )
    st.plotly_chart(fig, use_container_width=True)

    st.markdown(f"### {category} 부문 상세 시계열 분석 ({mode})")
    graph_start_date = end_date - relativedelta(months=12)
    graph_df = sub_df[sub_df['매각일'] >= graph_start_date]

    if not graph_df.empty:
        monthly_avg = graph_df.set_index('매각일').resample('ME')['낙찰율'].mean().reset_index()
        fig2 = go.Figure()
        fig2.add_hrect(y0=ltv - 5, y1=ltv + 5, line_width=0, fillcolor="green", opacity=0.1)
        fig2.add_hrect(y0=ltv + 5, y1=ltv + 10, line_width=0, fillcolor="yellow", opacity=0.1)
        fig2.add_hrect(y0=ltv - 10, y1=ltv - 5, line_width=0, fillcolor="yellow", opacity=0.1)
        y_max2 = max(100, (monthly_avg['낙찰율'].max() if not monthly_avg['낙찰율'].dropna().empty else 100) + 10)
        fig2.add_hrect(y0=ltv + 10, y1=y_max2, line_width=0, fillcolor="red", opacity=0.05)
        fig2.add_hrect(y0=0, y1=ltv - 10, line_width=0, fillcolor="red", opacity=0.05)
        fig2.add_hline(y=ltv, line_dash="dash", line_color="black", line_width=1, annotation_text=f"LTV {ltv}%")
        fig2.add_trace(go.Scatter(
            x=monthly_avg['매각일'], y=monthly_avg['낙찰율'],
            mode='lines+markers', name='낙찰율 (월별)',
            line=dict(color='blue', width=2), connectgaps=True
        ))

        fig2.add_trace(go.Scatter(x=[None], y=[None], mode='markers', marker=dict(color='green', size=10, symbol='square'), name='현행유지'))
        fig2.add_trace(go.Scatter(x=[None], y=[None], mode='markers', marker=dict(color='#FDFD96', size=10, symbol='square'), name='조정검토'))
        fig2.add_trace(go.Scatter(x=[None], y=[None], mode='markers', marker=dict(color='#FFCCCB', size=10, symbol='square'), name='조정필요'))

        fig2.update_layout(
            title=f"{usage_type} (LTV: {ltv}%)",
            xaxis=dict(tickformat="%y.%m"),
            yaxis_range=[0, y_max2],
            height=350,
            margin=dict(l=10, r=10, t=40, b=20),
            legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
        )
        st.plotly_chart(fig2, use_container_width=True)


def check_signal_logic(metrics, ltv, min_val):
    if metrics is None:
        return None

    avg12, avg6, avg3 = metrics["avg"][12], metrics["avg"][6], metrics["avg"][3]
    cnt12, cnt6, cnt3, cnt36 = metrics["count"][12], metrics["count"][6], metrics["count"][3], metrics["count"][36]

    if not all(v is not None for v in [avg12, avg6, avg3]):
        return None

    d12, d6, d3 = avg12 - ltv, avg6 - ltv, avg3 - ltv
    g12, g6, g3 = round(abs(d12), 1), round(abs(d6), 1), round(abs(d3), 1)

    t12, t6, t3 = cnt36 / 3.0, cnt36 / 6.0, cnt36 / 12.0
    is_sufficient = (cnt36 >= min_val and cnt12 >= t12 / 2 and cnt6 >= t6 / 2 and cnt3 >= t3 / 2)
    if not is_sufficient:
        return None

    is_red = all(g >= 10 for g in [g12, g6, g3])
    # 모든 기간이 5% 이상이되, 모두 10% 이상(레드)은 아닌 경우를 옐로우로 지정
    is_yellow = all(g >= 5 for g in [g12, g6, g3]) and not is_red
    
    if not (is_red or is_yellow):
        return None

    is_pos = all(d > 0 for d in [d12, d6, d3])
    is_neg = all(d < 0 for d in [d12, d6, d3])
    # eps = 2.0  # 1%p까지는 오차 허용

    # is_golden = (avg3 >= avg6 - eps) and (avg6 >= avg12 - eps)
    # is_dead   = (avg3 <= avg6 + eps) and (avg6 <= avg12 + eps) 

    is_golden = avg3 > avg6 > avg12
    is_dead = avg3 < avg6 < avg12

    direction = "▲" if is_pos and is_golden else ("▼" if is_neg and is_dead else None)
    if not direction:
        return None

    current_gap = avg3 - ltv
    suggested_ltv = round(avg12 if direction == "▲" else avg3, 1)
    adjust_delta = round(suggested_ltv - ltv, 1)

    tone = "red" if is_red else "yellow"
    reason = (
        f"3M/6M/12M 낙찰값이 모두 기존 LTV와 {'10%p 이상' if is_red else '5%p 이상'} 차이, "
        f"건수 충족, {'상향' if direction == '▲' else '하향'} 추세 확인"
    )

    return {
        "direction": direction,
        "tone": tone,
        "gap3": round(current_gap, 2),
        "suggested_ltv": suggested_ltv,
        "adjust_delta": adjust_delta,
        "reason": reason,
        "counts": {"3": cnt3, "6": cnt6, "12": cnt12, "36": cnt36},
    }


# =========================================================
# 집계 데이터 생성 (캐싱 적용)
# =========================================================
@st.cache_data
def get_aggregated_data(winning_df, mode, outlier_thresh, min_cnt):
    matrix_rows = []
    urgent_cards = []
    
    unique_regions = winning_df["시도"].dropna().unique()
    
    for reg in unique_regions:
        reg_winning = winning_df[winning_df["시도"] == reg]
        if reg_winning.empty:
            continue
            
        reg_last_date = reg_winning["매각일"].max()
        reg_group = reg_winning.groupby("분석용도")
        
        # ltv_standards에서 카테고리와 품목을 동적으로 가져옵니다.
        if ltv_standards is not None:
             # 구분/담보종류 쌍 추출
             std_info = ltv_standards[["구분", "담보종류"]].drop_duplicates()
             for _, row_std in std_info.iterrows():
                category = row_std["구분"]
                usage_type = row_std["담보종류"]
                
                if usage_type not in reg_group.groups:
                    continue
                    
                target_df = reg_group.get_group(usage_type)
                ltv_val = target_df["적용LTV"].iloc[0] if "적용LTV" in target_df.columns else 80.0
                met = calculate_metrics(reg_winning, usage_type, ltv_val, reg_last_date, mode, outlier_thresh)
                
                signal = check_signal_logic(met, ltv_val, min_cnt)
                if signal:
                    urgent_cards.append({
                        "reg": reg,
                        "category": category,
                        "usage_type": usage_type,
                        "ltv_val": ltv_val,
                        "met": met,
                        "signal": signal
                    })
                   
                matrix_row = {
                    "지역": reg,
                    "대분류": category,
                    "용도": usage_type,
                    "LTV": ltv_val,
                    "signal_tone": signal["tone"] if signal else None,
                }
                
                for month_num, month_label in FIXED_MONTHS:
                    avg_val = met["avg"].get(month_num)
                    cnt_val = met["count"].get(month_num, 0)
                    matrix_row[month_label] = classify_period(avg_val, ltv_val, cnt_val, min_cnt)
                    matrix_row[f"{month_label}_count"] = cnt_val
                    
                matrix_rows.append(matrix_row)
                
    m_df = pd.DataFrame(matrix_rows)
    return m_df, urgent_cards

# 캐시된 함수 호출
matrix_df, raw_urgent_list = get_aggregated_data(
    global_winning_df, analysis_mode, outlier_threshold, min_count
)

if not matrix_df.empty:
    matrix_df["_search_text"] = (
        matrix_df["지역"].astype(str) + " " +
        matrix_df["대분류"].astype(str) + " " +
        matrix_df["용도"].astype(str)
    ).str.lower()
else:
    matrix_df["_search_text"] = ""

@st.cache_data
def get_cached_llm_advice(item_info):
    return llm_advisor.get_ltv_advice(item_info)

def fetch_all_advice(urgent_list):
    def process_item(item):
        met = item["met"]
        info = {
            "region": item["reg"],
            "usage": item["usage_type"],
            "current_ltv": item["ltv_val"],
            "avg3": met["avg"][3] or 0.0, "cnt3": met["count"][3],
            "avg6": met["avg"][6] or 0.0, "cnt6": met["count"][6],
            "avg12": met["avg"][12] or 0.0, "cnt12": met["count"][12],
            "avg36": met["avg"][36] or 0.0, "cnt36": met["count"][36],
        }
        advice = get_cached_llm_advice(info)
        return {
            **item,
            "region": item["reg"],
            "usage": item["usage_type"],
            "current_ltv": item["ltv_val"],
            "conservative_ltv": advice["conservative_ltv"],
            "conservative_delta": advice["conservative_delta"],
            "relaxed_ltv": advice["relaxed_ltv"],
            "relaxed_delta": advice["relaxed_delta"],
            "reason": advice["reason"],
            "tone": item["signal"]["tone"],
            "direction": item["signal"]["direction"],
            "gap3": item["signal"]["gap3"],
            "delta": advice["relaxed_delta"],
        }

    with ThreadPoolExecutor(max_workers=5) as executor:
        results = list(executor.map(process_item, urgent_list))
    return pd.DataFrame(results)

urgent_cards_df = fetch_all_advice(raw_urgent_list) if raw_urgent_list else pd.DataFrame()

if not global_winning_df.empty:
    last_date = global_winning_df["매각일"].max()
else:
    last_date = datetime.now()


# =========================================================
# 스타일
# =========================================================
with open("style.css", encoding="utf-8") as f:
    st.markdown(f"<style>{f.read()}</style>", unsafe_allow_html=True)


# =========================================================
# 상단 헤더
# =========================================================
red_count = len(urgent_cards_df[urgent_cards_df["tone"] == "red"]) if not urgent_cards_df.empty else 0
yellow_count = len(urgent_cards_df[urgent_cards_df["tone"] == "yellow"]) if not urgent_cards_df.empty else 0
# 오늘 날짜 이전의 데이터 중 가장 최근 날짜를 찾습니다.
valid_dates = df[df["매각일"] <= datetime.now()]["매각일"]
last_update_date = valid_dates.max().strftime("%y.%m.%d") if not valid_dates.empty else "데이터 없음"

h_l, h_r = st.columns([0.8, 1.6])

with h_l:
    st.markdown(
        """
        <div style='font-size:45px; font-weight:800; color:#0f172a; line-height:1.15; margin-bottom:18px; letter-spacing:-0.5px;'>
            LTV 적정성 대시보드 
        </div>
        """,
        unsafe_allow_html=True
    )

with h_r:
    # 우측 대시보드 요약 지표 카드
    st.markdown(
        f"""
        <div class='metric-container' style='justify-content: flex-end; gap: 10px; padding-top: 15px;'>
            <div class='metric-card' style='padding: 20px 10px; min-width: 75px; text-align: center; border-radius: 14px;'>
                <div class='metric-label' style='font-size: 15px; margin-bottom: 15px; color:#94a3b8;'>즉시 조정필요</div>
                <div class='metric-value' style='font-size: 25px; font-weight:800;'>{red_count}<span style='font-size:13px; font-weight:600;'>건</span></div>
            </div>
            <div class='metric-card' style='padding: 20px 10px; min-width: 75px; text-align: center; border-radius: 14px;'>
                <div class='metric-label' style='font-size: 15px; margin-bottom: 15px; color:#94a3b8;'>검토 필요건수</div>
                <div class='metric-value' style='font-size: 25px; font-weight:800;'>{yellow_count}<span style='font-size:13px; font-weight:600;'>건</span></div>
            </div>
            <div class='metric-card' style='padding: 20px 10px; min-width: 75px; text-align: center; border-radius: 14px;'>
                <div class='metric-label' style='font-size: 15px; margin-bottom: 15px; color:#94a3b8;'>최종 업데이트</div>
                <div class='metric-value' style='font-size: 20px; font-weight:800;'>{last_update_date}</div>
            </div>
        </div>
        """,
        unsafe_allow_html=True
    )


# =========================================================
# 상단 긴급 조정 카드
# =========================================================
st.write("")
st.write("")

u_c1, u_c2 = st.columns([0.45, 0.55], vertical_alignment="center")

with u_c1:
    st.markdown(
        """
        <div style='display:flex; align-items:center; min-height:48px;'>
            <div class='section-title' style='margin:0; white-space:nowrap; line-height:1;'>
                🔔 지금 당장 조정이 필요한 건물
            </div>
            <div class='help-tooltip' style='margin-left:8px;'>
                ⓘ
                <div class='tooltiptext'>
                    <div class='tooltip-item'>
                        <span class='tooltip-title'>🔴 레드 시그널</span>
                        3M·6M·12M 낙찰값이 모두 기존 LTV와 10%p 이상 차이, 건수 충족, 상향/하향 추세 확인
                    </div>
                    <div class='tooltip-item'>
                        <span class='tooltip-title'>🟡 옐로우 시그널</span>
                        3M·6M·12M 낙찰값이 모두 기존 LTV와 5~9%p 차이, 건수 충족, 방향성 존재
                    </div>
                </div>
            </div>
        </div>
        """,
        unsafe_allow_html=True
    )

with u_c2:
    st.markdown("<div style='display:flex; align-items:center; min-height:48px;'>", unsafe_allow_html=True)
    st.radio(
        "긴급 카드 필터",
        ["전체", "긴급 조정 필요", "검토 필요"],
        key="filter_urgent_mode",
        horizontal=True,
        label_visibility="collapsed"
    )
    st.markdown("</div>", unsafe_allow_html=True)


# Green 등급 제외 및 위험도 순 정렬
urgent_display_df = urgent_cards_df[urgent_cards_df["tone"].isin(["red", "yellow"])].copy() if not urgent_cards_df.empty else pd.DataFrame()

# 상단 필터 적용
if not urgent_display_df.empty:
    urgent_mode = st.session_state.get("filter_urgent_mode", "전체")
    if urgent_mode == "긴급 조정 필요":
        urgent_display_df = urgent_display_df[urgent_display_df["tone"] == "red"]
    elif urgent_mode == "검토 필요":
        urgent_display_df = urgent_display_df[urgent_display_df["tone"] == "yellow"]

if urgent_display_df.empty:
    st.success("현재 조건을 충족하는 즉시 조정 대상이 없습니다.")
else:
    sort_map = {"red": 0, "yellow": 1}
    urgent_display_df["sort_key"] = urgent_display_df["tone"].map(sort_map)
    urgent_display_df = urgent_display_df.sort_values(["sort_key", "delta"])

    top_cards = urgent_display_df.to_dict("records")
    card_columns = st.columns(4)
    for idx, item in enumerate(top_cards):
        is_red = item["tone"] == "red"
        is_yellow = item["tone"] == "yellow"

        bg_class = "urgent-red" if is_red else ("urgent-yellow" if is_yellow else "urgent-green")
        pill_class = "red-pill" if is_red else ("yellow-pill" if is_yellow else "green-pill")

        action_label = "즉시 조정 필요" if is_red else ("검토 필요" if is_yellow else "상향 여지")
        delta_text = f"{item['delta']:+.1f}%p"

        with card_columns[idx % 4]:
            st.markdown(
                f"""
                <div class='urgent-card {bg_class}'>
                    <div class='card-top'>
                        <div class='card-region'>{item['region']}</div>
                        <span class='status-pill {pill_class}'>{action_label}</span>
                    </div>
                    <div class='card-title'>{item['usage']} / {item['category']}</div>
                    <div class='subgrid'>
                        <div class='subbox'>
                            <div class='subbox-label'>현재 LTV</div>
                            <div class='subbox-value'>{item['current_ltv']:.0f}%</div>
                            <div style='font-size:11px; font-weight:700;'>&nbsp;</div>
                        </div>
                        <div class='subbox'>
                            <div class='subbox-label'>보수적 안</div>
                            <div class='subbox-value'>{item['conservative_ltv']:.1f}%</div>
                            <div style='font-size:11px; color:{"#dc2626" if item["conservative_delta"] < 0 else "#16a34a"}; font-weight:700;'>{item['conservative_delta']:+.1f}%p</div>
                        </div>
                        <div class='subbox'>
                            <div class='subbox-label'>완화적 안</div>
                            <div class='subbox-value'>{item['relaxed_ltv']:.1f}%</div>
                            <div style='font-size:11px; color:{"#dc2626" if item["relaxed_delta"] < 0 else "#16a34a"}; font-weight:700;'>{item['relaxed_delta']:+.1f}%p</div>
                        </div>
                    </div>
                    <div class='reason-box'>
                        <div class='reason-title'>권고안 산출 사유</div>
                        <div>{item['reason']}</div>
                    </div>
                </div>
                """,
                unsafe_allow_html=True,
            )

st.write("")
st.write("")


# =========================================================
# 매트릭스 제목
# =========================================================
st.markdown("<div class='section-title'>📊 지역·용도별 LTV 적정성 매트릭스</div>", unsafe_allow_html=True)
st.markdown("<div class='section-desc'>모든 지역과 용도의 기간별 분석 결과를 한눈에 확인합니다.</div>", unsafe_allow_html=True)

# =========================================================
# 매트릭스 검색 및 필터 바 (상시 노출)
# =========================================================
def handle_reset():
    st.session_state["region_select_matrix"] = "전체 지역"
    st.session_state["filter_status_mode"] = "전체"
    st.session_state["filter_category"] = "전체"
    st.session_state["filter_usage"] = "전체"
    st.session_state["min_count_val"] = 1
    st.session_state["filter_outlier_on"] = False
    st.session_state["outlier_val"] = 20.0
    st.session_state["search_query"] = ""

f_c1, f_c2, f_c3 = st.columns([0.6, 3.0, 0.6], vertical_alignment="center")

with f_c1:
    unique_regions = sorted(df["시도"].dropna().unique())
    st.selectbox(
        "지역 선택",
        ["전체 지역"] + unique_regions,
        key="region_select_matrix",
        label_visibility="collapsed"
    )

with f_c2:
    st.markdown("<div class='filter-pill-container'>", unsafe_allow_html=True)
    st.radio(
        "필터 모드",
        ["전체", "긴급 조정 필요", "검토 필요"],
        key="filter_status_mode",
        horizontal=True,
        label_visibility="collapsed"
    )
    st.markdown("</div>", unsafe_allow_html=True)

with f_c3:
    st.button("초기화", use_container_width=True, on_click=handle_reset)

# 상세 필터 패널 (st.expander 적용)
with st.expander("🔍 상세 필터 설정"):
    # 카테고리(대분류)를 ltv_standards에서 동적으로 가져옴
    if ltv_standards is not None:
        unique_cats = sorted(ltv_standards["구분"].unique().tolist())
    else:
        unique_cats = []
        
    unique_usages = sorted(matrix_df["용도"].unique())
    
    d_c1, d_c2, d_c3, d_c4 = st.columns([1, 1, 1, 2])
    
    with d_c1:
        st.selectbox("대분류", ["전체"] + unique_cats, key="filter_category")
    with d_c2:
        st.selectbox("용도", ["전체"] + unique_usages, key="filter_usage")
    with d_c3:
        st.number_input("최소 건수", min_value=1, key="min_count_val", help="각 기간별(3, 6, 12개월 등) 실제 건수가 이 값 이상인 항목만 매트릭스에 색상 표시됩니다.")
    with d_c4:
        st.markdown("<div style='font-size:13px; font-weight:700; color:#1e293b; margin-bottom:4px;'>극단값 제외 옵션</div>", unsafe_allow_html=True)
        o_l, o_r = st.columns([1.2, 1], vertical_alignment="center")
        with o_l:
            st.toggle("극단값 제외", key="filter_outlier_on")
        with o_r:
            st.number_input("제외 비율(%)", min_value=20.0, max_value=100.0, step=0.5, key="outlier_val", label_visibility="collapsed")



# =========================================================
# 매트릭스 헤더
# =========================================================
h_cols = st.columns([1.0, 1.1, 1.2, 0.7, 1.0, 1.0, 1.0, 1.0, 1.0, 0.9])
labels = ["지역", "대분류", "용도", "LTV"] + COL_LABELS + ["상세보기"]
for i, label in enumerate(labels):
    h_cols[i].markdown(
        f"<div style='font-weight:700; color:#1e293b; font-size:14px; text-align:center;'>{label}</div>",
        unsafe_allow_html=True
    )
st.markdown("<hr style='margin: 8px 0; border: none; border-top: 1px solid #e2e8f0;'>", unsafe_allow_html=True)


# =========================================================
# 필터링 로직
# =========================================================
show_cols = ["지역", "대분류", "용도", "LTV"] + COL_LABELS
matrix_display_df = matrix_df[show_cols].copy().sort_values(["지역", "대분류", "용도"])

# 1. 지역 필터
if st.session_state["region_select_matrix"] != "전체 지역":
    matrix_display_df = matrix_display_df[
        matrix_display_df["지역"] == st.session_state["region_select_matrix"]
    ]

# 2. 대분류 필터
if st.session_state["filter_category"] != "전체":
    matrix_display_df = matrix_display_df[
        matrix_display_df["대분류"] == st.session_state["filter_category"]
    ]

# 3. 용도 필터
if st.session_state["filter_usage"] != "전체":
    matrix_display_df = matrix_display_df[
        matrix_display_df["용도"] == st.session_state["filter_usage"]
    ]

# 4. 상태 필터
status_mode = st.session_state.get("filter_status_mode", "전체")
if status_mode == "긴급 조정 필요":
    matrix_display_df = matrix_display_df[
        matrix_df.loc[matrix_display_df.index, "signal_tone"] == "red"
    ]
elif status_mode == "검토 필요":
    matrix_display_df = matrix_display_df[
        matrix_df.loc[matrix_display_df.index, "signal_tone"] == "yellow"
    ]

# 5. 검색 필터
search_query = st.session_state.get("search_query", "").strip().lower()
if search_query:
    matrix_display_df = matrix_display_df[
        matrix_df.loc[matrix_display_df.index, "_search_text"].str.contains(search_query, na=False)
    ]

# 6. 최소 건수 필터
min_count_filter = st.session_state.get("min_count_val", 10)
count_cols = [f"{label}_count" for label in COL_LABELS]
matrix_display_df = matrix_display_df[
    matrix_df.loc[matrix_display_df.index, count_cols].max(axis=1) >= min_count_filter
]

st.markdown(
    f"<div style='font-size:13px; color:#64748b; margin:0 0 10px 0;'>검색 결과 <b>{len(matrix_display_df)}</b>건</div>",
    unsafe_allow_html=True
)


# =========================================================
# 매트릭스 본문
# =========================================================
with st.container(height=600, border=False):
    for idx, row in matrix_display_df.iterrows():
        r_cols = st.columns([1.0, 1.1, 1.2, 0.7, 1.0, 1.0, 1.0, 1.0, 1.0, 0.9], vertical_alignment="center")
        r_cols[0].markdown(f"<div style='text-align:center;'><b>{row['지역']}</b></div>", unsafe_allow_html=True)
        r_cols[1].markdown(f"<div style='text-align:center; font-size:13px;'>{row['대분류']}</div>", unsafe_allow_html=True)
        r_cols[2].markdown(f"<div style='text-align:center; font-size:13px;'>{row['용도']}</div>", unsafe_allow_html=True)
        r_cols[3].markdown(f"<div style='text-align:center;'>{row['LTV']:.0f}%</div>", unsafe_allow_html=True)

        for i, col in enumerate(COL_LABELS):
            stat = row[col]
            conf = STATUS_MAP[stat]
            r_cols[4 + i].markdown(
                f"<div style='display:flex; justify-content:center; align-items:center; height:100%; padding: 4px 0;'>"
                f"<div style='width:14px; height:14px; border-radius:50%; background:{conf['dot']}; box-shadow: 0 0 4px {conf['dot']}44;'></div>"
                f"</div>",
                unsafe_allow_html=True
            )

        with r_cols[9]:
            _, btn_c, _ = st.columns([1, 8, 1])
            if btn_c.button("보기", key=f"mat_btn_{idx}", use_container_width=True):
                show_details_dialog(
                    row["지역"],
                    row["대분류"],
                    row["용도"],
                    row["LTV"],
                    global_winning_df,
                    analysis_mode,
                    outlier_threshold
                )
