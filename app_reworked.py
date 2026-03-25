import pandas as pd
import streamlit as st
import plotly.express as px
import plotly.graph_objects as go
from datetime import datetime
from dateutil.relativedelta import relativedelta
import llm_advisor
from concurrent.futures import ThreadPoolExecutor
import json
import os
import threading
import time

st.set_page_config(layout="wide", page_title="LTV 적정성 대시보드")

COLORS = px.colors.qualitative.Plotly
FIXED_MONTHS = [(3, "3개월"), (6, "6개월"), (12, "12개월"), (36, "3년"), (60, "5년")]
COL_LABELS = [m[1] for m in FIXED_MONTHS]

STATUS_MAP = {
    "green": {"label": "적정", "bg": "#e8f7ee", "fg": "#1f7a42", "dot": "#22c55e"},
    "yellow": {"label": "주의", "bg": "#fff8db", "fg": "#9a6700", "dot": "#facc15"},
    "red": {"label": "부적정", "bg": "#feecec", "fg": "#b91c1c", "dot": "#ef4444"},
    "gray": {"label": "모수 부족", "bg": "#f3f4f6", "fg": "#6b7280", "dot": "#9ca3af"},
}

URGENT_BADGE = {
    "red": {"label": "조정 대상", "class": "urgent-red"},
    "yellow": {"label": "검토 대상", "class": "urgent-yellow"},
    "green": {"label": "참고 대상", "class": "urgent-green"},
}


def load_ltv_standard_file():
    path = "data/LTV_기준(광주은행).csv"
    if os.path.exists(path):
        return pd.read_csv(path, encoding="utf-8-sig")
    return None

ltv_standards = load_ltv_standard_file()

def save_final_ltv(region, usage, final_ltp):
    std = load_ltv_standard_file()
    if std is None:
        st.error("LTV 기준 파일을 찾을 수 없습니다.")
        return

    # 지역명 매핑 (표준 리전명으로 변환)
    region_map = {
        "서울특별시": "서울", "인천광역시": "인천", "경기도": "경기",
        "광주광역시": "광주", "전라남도": "전남", "전라북도": "전북",
        "부산광역시": "부산", "대전광역시": "대전", "대구광역시": "대구",
        "울산광역시": "울산", "세종특별자치시": "세종", "충청북도": "충북",
        "충청남도": "충남", "경상북도": "경북", "경상남도": "경남",
        "제주특별자치도": "제주", "강원도": "강원",
    }
    target_col = region_map.get(region, region)
    
    # 해당 용도(담보종류) 행 찾기
    mask = (std["담보종류"] == usage)
    if not mask.any():
        st.error(f"'{usage}' 용도를 기준 테이블에서 찾을 수 없습니다.")
        return
    
    std.loc[mask, target_col] = final_ltp
    std.to_csv("data/LTV_기준(광주은행).csv", index=False, encoding="utf-8-sig")
    st.toast(f"✅ [{region}] {usage}: LTV {final_ltp}%로 적용 완료!", icon="🚀")
    st.rerun()


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
def load_raw_data(file_path):
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

    if "LTV_광주" in df.columns:
        df["분석용도"] = df["LTV_광주"]
    else:
        df["분석용도"] = df["용도"].apply(map_usage_to_config)

    df["_LTV지역구분"] = get_ltv_col_name_vec(df["시도"])
    return df


@st.cache_data
def load_all_raw_data():
    dfs = []
    regions = [
        "서울", "인천", "경기", "부산", "대구", "대전", "광주", "울산", 
        "전북", "전남", "경북", "경남", "제주"
    ]
    for fname in regions:
        path = f"data/{fname}.csv"
        if os.path.exists(path):
            try:
                dfs.append(load_raw_data(path))
            except Exception as e:
                st.warning(f"{fname} 데이터 로드 실패: {e}")

    if not dfs:
        st.error("데이터 파일들을 찾을 수 없습니다. data 폴더를 확인해주세요.")
        st.stop()

    return pd.concat(dfs, ignore_index=True)


def merge_ltv_standards(df, ltv_std):
    if ltv_std is not None:
        std_melted = ltv_std.melt(
            id_vars=["구분", "담보종류"],
            var_name="_LTV지역구분",
            value_name="적용LTV",
        )
        if "적용LTV" in df.columns:
            df = df.drop(columns=["적용LTV"], errors='ignore')
            
        merged = df.merge(
            std_melted[["담보종류", "_LTV지역구분", "적용LTV"]],
            left_on=["분석용도", "_LTV지역구분"],
            right_on=["담보종류", "_LTV지역구분"],
            how="left",
        )
        merged["적용LTV"] = merged["적용LTV"].fillna(80.0)
        # 담보종류 column might be duplicated if not dropped
        if "담보종류" in merged.columns:
            merged = merged.drop(columns=["담보종류"])
        return merged
    else:
        df["적용LTV"] = 80.0
        return df


raw_df = load_all_raw_data()
df = merge_ltv_standards(raw_df, ltv_standards)

if "결과" in df.columns:
    global_winning_df = df[df["결과"].astype(str).str.contains("낙찰|매각", na=False)].copy()
else:
    global_winning_df = df.copy()


st.sidebar.markdown("### ⚙️ 분석 설정")
max_dt = global_winning_df["매각일"].max().date()
min_dt = global_winning_df["매각일"].min().date()

base_date = st.sidebar.date_input(
    "기준 만기일 선택", 
    value=max_dt, 
    min_value=min_dt, 
    max_value=max_dt,
    help="선택한 기준일을 바탕으로 직전 3개월, 6개월 등의 통계를 계산합니다."
)
base_date_str = base_date.strftime("%Y-%m-%d")

for key, value in {
    "region_select_matrix": "전체 지역",
    "filter_category": "전체",
    "filter_usage": "전체",
    "min_count_val": 1,
    "filter_status_mode": "전체",
    "filter_urgent_mode": "전체",
}.items():
    if key not in st.session_state:
        st.session_state[key] = value

min_count = st.session_state.get("min_count_val", 1)
analysis_mode = "월별 (극단값 제외)"
outlier_threshold = 0.3


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
def show_details_dialog(region, category, usage_type, ltv, src_df, mode, outlier_thresh, cons_ltv=None, relax_ltv=None, reason=None, base_dt=None):
    st.subheader(f"[{region}] {category} > {usage_type} 상세 분석")

    reg_df = src_df[src_df["시도"] == region].copy()
    if base_dt is not None:
        reg_df = reg_df[reg_df["매각일"] <= base_dt]
        
    if reg_df.empty:
        st.warning("분석할 지역 데이터가 없습니다.")
        return

    last_dt = base_dt if base_dt is not None else reg_df["매각일"].max()
    met = calculate_metrics(reg_df, usage_type, ltv, last_dt, mode, outlier_thresh)

    if cons_ltv is not None and relax_ltv is not None:
        c_val = f"{cons_ltv:.0f}%" if isinstance(cons_ltv, (int, float)) else cons_ltv
        r_val = f"{relax_ltv:.0f}%" if isinstance(relax_ltv, (int, float)) else relax_ltv
        
        def get_delta_html(val, base_ltv):
            if isinstance(val, (int, float)):
                d = val - base_ltv
                c = "#dc2626" if d < 0 else "#16a34a" if d > 0 else "#64748b"
                sign = "+" if d > 0 else ""
                return f"<div style='font-size:14px; font-weight:800; color:{c}; margin-top:4px;'>{sign}{d:.0f}%</div>"
            return ""

        c_delta_html = get_delta_html(cons_ltv, ltv)
        r_delta_html = get_delta_html(relax_ltv, ltv)
        
        box_style = "padding:12px; background:#f8fafc; border-radius:8px; border:1px solid #e2e8f0; text-align:center; display:flex; flex-direction:column; justify-content:center; min-height:115px; height:100%;"
        
        if reason:
            top_cols = st.columns([1, 1, 1, 3.5])
        else:
            top_cols = st.columns(3)
            
        top_cols[0].markdown(f"<div style='{box_style}'>"
                            f"<div style='font-size:14px; color:#64748b; font-weight:700; margin-bottom:8px;'>현재 LTV</div>"
                            f"<div style='font-size:20px; font-weight:800; color:#0f172a;'>{ltv:.0f}%</div>"
                            f"<div style='font-size:14px; font-weight:800; color:transparent; margin-top:4px;'>-</div>"
                            f"</div>", unsafe_allow_html=True)
        top_cols[1].markdown(f"<div style='{box_style}'>"
                            f"<div style='font-size:14px; color:#64748b; font-weight:700; margin-bottom:8px;'>보수적 안</div>"
                            f"<div style='font-size:20px; font-weight:800; color:#0f172a;'>{c_val}</div>"
                            f"{c_delta_html}"
                            f"</div>", unsafe_allow_html=True)
        top_cols[2].markdown(f"<div style='{box_style}'>"
                            f"<div style='font-size:14px; color:#64748b; font-weight:700; margin-bottom:8px;'>완화적 안</div>"
                            f"<div style='font-size:20px; font-weight:800; color:#0f172a;'>{r_val}</div>"
                            f"{r_delta_html}"
                            f"</div>", unsafe_allow_html=True)

        if reason:
            top_cols[3].markdown(f"<div style='padding:12px 20px; background:#f8fafc; border-radius:8px; border:1px solid #e2e8f0; display:flex; flex-direction:column; justify-content:center; min-height:115px; height:100%;'>"
                                f"<div style='font-size:13px; color:#64748b; font-weight:800; margin-bottom:6px;'>권고안 산출 사유</div>"
                                f"<div style='font-size:14px; font-weight:700; color:#334155; line-height:1.5;'>{reason}</div>"
                                f"</div>", unsafe_allow_html=True)
        st.write("")
        st.markdown("### 기간별 매각 통계")

    m_cols = st.columns(5)

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
                    <div style="font-size:15px; color:#94a3b8; font-weight:700; margin-bottom:4px; text-transform:uppercase;">{label}</div>
                    <div style="font-size:20px; font-weight:800; color:{val_color}; line-height:1.2;">{val_text}</div>
                    {diff_text}
                </div>
                """,
                unsafe_allow_html=True,
            )

    draw_mini_card(m_cols[0], "3개월", met["avg"][3], met["count"][3], ltv)
    draw_mini_card(m_cols[1], "6개월", met["avg"][6], met["count"][6], ltv)
    draw_mini_card(m_cols[2], "12개월", met["avg"][12], met["count"][12], ltv)
    draw_mini_card(m_cols[3], "3년", met["avg"][36], met["count"][36], ltv)
    draw_mini_card(m_cols[4], "5년", met["avg"][60], met["count"][60], ltv)

    st.write("")

    sub_df = reg_df[reg_df["분석용도"] == usage_type].copy()
    if mode == "월별 (극단값 제외)":
        limit = ltv * outlier_thresh
        sub_df = sub_df[abs(sub_df["낙찰율"] - ltv) <= limit]

    if sub_df.empty:
        st.warning("분석 가능한 데이터가 부족합니다.")
        return

    end_date = base_dt if base_dt is not None else sub_df["매각일"].max()
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
    fig.add_trace(go.Scatter(x=monthly.index, y=monthly.values, mode='lines+markers', name='월별 평균(실제값)', line=dict(color='gray', width=1, dash='dot'), marker=dict(size=4, color='gray', opacity=0.5), connectgaps=True))
    fig.add_trace(go.Scatter(x=rolling_3m.index, y=rolling_3m.values, mode='lines', name='3개월 이동평균', line=dict(color='#1f77b4', width=1.5, dash='dot'), connectgaps=True))
    fig.add_trace(go.Scatter(x=rolling_6m.index, y=rolling_6m.values, mode='lines', name='6개월 이동평균', line=dict(color='#9467bd', width=2), connectgaps=True))
    fig.add_trace(go.Scatter(x=rolling_12m.index, y=rolling_12m.values, mode='lines', name='12개월 이동평균', line=dict(color='#ff7f0e', width=3), connectgaps=True))
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
        fig2.add_trace(go.Scatter(x=monthly_avg['매각일'], y=monthly_avg['낙찰율'], mode='lines+markers', name='낙찰율 (월별)', line=dict(color='blue', width=2), connectgaps=True))
        fig2.add_trace(go.Scatter(x=[None], y=[None], mode='markers', marker=dict(color='green', size=10, symbol='square'), name='현행유지'))
        fig2.add_trace(go.Scatter(x=[None], y=[None], mode='markers', marker=dict(color='#FDFD96', size=10, symbol='square'), name='조정검토'))
        fig2.add_trace(go.Scatter(x=[None], y=[None], mode='markers', marker=dict(color='#FFCCCB', size=10, symbol='square'), name='조정필요'))
        fig2.update_layout(title=f"{usage_type} (LTV: {ltv}%)", xaxis=dict(tickformat="%y.%m"), yaxis_range=[0, y_max2], height=350, margin=dict(l=10, r=10, t=40, b=20), legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1))
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

    is_sufficient = cnt3 >= 10
    if not is_sufficient:
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

    direction = "▲" if is_pos and is_golden else ("▼" if is_neg and is_dead else None)
    if not direction:
        return None

    current_gap = avg3 - ltv
    suggested_ltv = round(avg12 if direction == "▲" else avg3, 1)
    adjust_delta = round(suggested_ltv - ltv, 1)
    tone = "red" if is_red else "yellow"
    reason = f"3/6/12개월 가중평균 낙찰가율이 기존 LTV와 {'10%p 이상' if is_red else '5%p 이상'} 차이, 건수 충족, {'상향' if direction == '▲' else '하향'} 추세 확인"

    return {
        "direction": direction,
        "tone": tone,
        "gap3": round(current_gap, 2),
        "suggested_ltv": suggested_ltv,
        "adjust_delta": adjust_delta,
        "reason": reason,
        "counts": {"3": cnt3, "6": cnt6, "12": cnt12, "36": cnt36},
    }


@st.cache_data
def get_aggregated_data(winning_df, mode, outlier_thresh, min_cnt, max_date_str, ltv_std):
    matrix_rows = []
    urgent_cards = []
    
    selected_dt = pd.to_datetime(max_date_str) if max_date_str else None

    unique_regions = winning_df["시도"].dropna().unique()
    for reg in unique_regions:
        reg_winning = winning_df[winning_df["시도"] == reg]
        if selected_dt is not None:
             reg_winning = reg_winning[reg_winning["매각일"] <= selected_dt]
             
        if reg_winning.empty:
            continue

        reg_last_date = selected_dt if selected_dt is not None else reg_winning["매각일"].max()
        reg_group = reg_winning.groupby("분석용도")

        if ltv_std is not None:
            std_info = ltv_std[["구분", "담보종류"]].drop_duplicates()
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
                    urgent_cards.append({"reg": reg, "category": category, "usage_type": usage_type, "ltv_val": ltv_val, "met": met, "signal": signal})

                matrix_row = {"지역": reg, "대분류": category, "용도": usage_type, "LTV": ltv_val, "signal_tone": signal["tone"] if signal else None}
                for month_num, month_label in FIXED_MONTHS:
                    avg_val = met["avg"].get(month_num)
                    cnt_val = met["count"].get(month_num, 0)
                    matrix_row[month_label] = classify_period(avg_val, ltv_val, cnt_val, min_cnt)
                    matrix_row[f"{month_label}_count"] = cnt_val
                matrix_rows.append(matrix_row)

    return pd.DataFrame(matrix_rows), urgent_cards


matrix_df, raw_urgent_list = get_aggregated_data(global_winning_df, analysis_mode, outlier_threshold, min_count, base_date_str, ltv_standards)




CACHE_DIR = "data/llm_cache"
os.makedirs(CACHE_DIR, exist_ok=True)
CACHE_LOCK = threading.Lock()

def get_monthly_cache_file(target_ym_str):
    return os.path.join(CACHE_DIR, f"llm_advice_{target_ym_str}.json")

def load_monthly_cache(target_ym_str):
    path = get_monthly_cache_file(target_ym_str)
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            try:
                return json.load(f)
            except Exception:
                return {}
    return {}

def save_to_monthly_cache(key, advice_data, target_ym_str):
    path = get_monthly_cache_file(target_ym_str)
    with CACHE_LOCK:
        cache = {}
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as f:
                try:
                    cache = json.load(f)
                except Exception:
                    pass
        cache[key] = advice_data
        with open(path, "w", encoding="utf-8") as f:
            json.dump(cache, f, ensure_ascii=False, indent=2)


def format_reason(reason: str) -> str:
    if not isinstance(reason, str) or not reason.strip():
        return "권고 사유가 없습니다."
    reason = reason.strip()
    if reason.startswith("LLM("):
        return "자동 권고 생성 실패로 수동 검토가 필요합니다."
    return reason.replace("\n", "<br>")


@st.cache_data
def fetch_all_advice(urgent_list, base_date_str):
    if not base_date_str:
        return pd.DataFrame()
    ym_str = base_date_str[:7].replace("-", "_")
    monthly_cache = load_monthly_cache(ym_str)

    def process_item(item):
        met = item["met"]
        info = {
            "region": item["reg"],
            "usage": item["usage_type"],
            "current_ltv": item["ltv_val"],
            "avg3": met["avg"][3] or 0.0,
            "cnt3": met["count"][3],
            "avg6": met["avg"][6] or 0.0,
            "cnt6": met["count"][6],
            "avg12": met["avg"][12] or 0.0,
            "cnt12": met["count"][12],
            "avg36": met["avg"][36] or 0.0,
            "cnt36": met["count"][36],
        }
        
        cache_key = f"{info['region']}_{info['usage']}_{info['current_ltv']}"
        if cache_key in monthly_cache:
            advice = monthly_cache[cache_key]
        else:
            advice = llm_advisor.get_ltv_advice(info)
            save_to_monthly_cache(cache_key, advice, ym_str)
            monthly_cache[cache_key] = advice

        return {
            **item,
            "region": item["reg"],
            "usage": item["usage_type"],
            "current_ltv": item["ltv_val"],
            "conservative_ltv": advice.get("conservative_ltv", item["ltv_val"]),
            "conservative_delta": advice.get("conservative_delta", 0),
            "relaxed_ltv": advice.get("relaxed_ltv", item["ltv_val"]),
            "relaxed_delta": advice.get("relaxed_delta", 0),
            "reason": format_reason(advice.get("reason", "분석 완료")),
            "tone": item["signal"]["tone"],
            "direction": item["signal"]["direction"],
            "gap3": item["signal"]["gap3"],
            "delta": advice.get("relaxed_delta", 0),
        }

    with ThreadPoolExecutor(max_workers=5) as executor:
        results = list(executor.map(process_item, urgent_list))
    return pd.DataFrame(results)

def format_top_items(items):
    values = [str(x).strip() for x in items if pd.notna(x) and str(x).strip()]
    unique_values = []
    for v in values:
        if v not in unique_values:
            unique_values.append(v)

    if not unique_values:
        return "-"

    return ", ".join(unique_values)

def build_monthly_summary_text(summary_df):
    if summary_df is None or summary_df.empty:
        return "이번 달에는 즉시 조정 또는 검토가 필요한 대상이 없습니다."

    down_df = summary_df[summary_df.get("direction", "") == "▼"]
    up_df = summary_df[summary_df.get("direction", "") == "▲"]
    
    red_df = down_df[down_df["tone"] == "red"]
    yellow_df = down_df[down_df["tone"] == "yellow"]
    
    red_cnt = len(red_df)
    yellow_cnt = len(yellow_df)
    ref_cnt = len(up_df)

    text = f"이번달 LTV 점검 결과, 총 <span style='color:#e11d48; font-weight:800;'>{red_cnt}건</span>의 조정 대상과 <span style='color:#ea580c; font-weight:800;'>{yellow_cnt}건</span>의 검토 대상이 확인되었습니다.<br>"
    
    if red_cnt > 0:
        red_grouped = red_df.groupby("region")["usage"].apply(list)
        sorted_regions = sorted(red_grouped.keys(), key=lambda r: len(red_grouped[r]), reverse=True)
        
        region_strs = []
        for reg in sorted_regions[:3]:
            usages = red_grouped[reg]
            u_str = ", ".join(usages)
            region_strs.append(f"{reg}의 {u_str}")
            
        top_red_ru = " 및 ".join(region_strs)
        if len(sorted_regions) > 3:
            text += f"조정 대상은 주로 <b>{top_red_ru}</b> 등이며, "
        else:
            text += f"조정 대상은 <b>{top_red_ru}</b> 등이며, "
    else:
        text += "조정 대상은 없으며, "

    reg_counts = down_df["region"].value_counts()
    if not reg_counts.empty:
        reg_str = ", ".join([f"{k} {v}건" for k, v in reg_counts.items()])
        text += f"지역별로는 <b>{reg_str}</b>으로 나타났습니다.<br>"
    else:
        text += "지역별 하향 조정 건은 없습니다.<br>"
        
    text += f"참고 대상인 상향 조정 건은 <span style='color:#15803d; font-weight:800;'>{ref_cnt}건</span>입니다."
    
    return text

urgent_cards_df = fetch_all_advice(raw_urgent_list, base_date_str) if raw_urgent_list else pd.DataFrame()
current_base_dt = pd.to_datetime(base_date_str)
last_update_date = (
    df[df["매각일"] <= current_base_dt]["매각일"].max().strftime("%y.%m.%d")
    if not df[df["매각일"] <= current_base_dt].empty else "데이터 없음"
)

with open("style_reworked.css", encoding="utf-8") as f:
    st.markdown(f"<style>{f.read()}</style>", unsafe_allow_html=True)


if not urgent_cards_df.empty:
    downward_df = urgent_cards_df[urgent_cards_df["direction"] == "▼"]
    upward_df = urgent_cards_df[urgent_cards_df["direction"] == "▲"]
    red_count = len(downward_df[downward_df["tone"] == "red"])
    yellow_count = len(downward_df[downward_df["tone"] == "yellow"])
    ref_count = len(upward_df)
else:
    red_count = yellow_count = ref_count = 0

h_l, h_r = st.columns([0.85, 1.15])
with h_l:
    st.markdown("<div class='hero-title'>LTV 적정성 대시보드</div>", unsafe_allow_html=True)
    st.markdown("<div class='hero-subtitle'>지역·용도별 낙찰가율 흐름을 바탕으로 조정 우선순위를 빠르게 확인합니다.</div>", unsafe_allow_html=True)
with h_r:
    st.markdown(
        f"""
        <div class='metric-container metric-right'>
            <div class='metric-card'>
                <div class='metric-label'>조정 대상</div>
                <div class='metric-value'>{red_count}<span class='metric-unit'>건</span></div>
            </div>
            <div class='metric-card'>
                <div class='metric-label'>검토 대상</div>
                <div class='metric-value'>{yellow_count}<span class='metric-unit'>건</span></div>
            </div>
            <div class='metric-card'>
                <div class='metric-label'>참고 대상</div>
                <div class='metric-value'>{ref_count}<span class='metric-unit'>건</span></div>
            </div>
            <div class='metric-card'>
                <div class='metric-label'>최종 업데이트</div>
                <div class='metric-value metric-date'>{last_update_date}</div>
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )

st.markdown("<div class='section-spacer'></div>", unsafe_allow_html=True)

# =========================================================
# 이번달 요약
# =========================================================
monthly_summary_text = build_monthly_summary_text(urgent_cards_df)

st.markdown(
    f"""
    <div class="monthly-summary-card">
        <div class="monthly-summary-label">이번달 결과 요약</div>
        <div class="monthly-summary-text">
            {monthly_summary_text}
        </div>
    </div>
    """,
    unsafe_allow_html=True
)

st.write("")
st.write("")

u_c1, u_c2 = st.columns([0.75, 0.25], vertical_alignment="bottom")

with u_c1:
    st.markdown(
        """
        <div style='display:flex; align-items:center; gap:8px; min-height:30px; padding-bottom: 5px;'>
            <div class='section-title' style='margin:0; white-space:nowrap; line-height:1;'>
                🔔 지금 당장 조정이 필요한 건물
            </div>
            <div class='help-tooltip'>
                ⓘ
                <div class='tooltiptext'>
                    <div class='tooltip-item' style='margin-bottom:8px; padding-bottom:8px; border-bottom:1px solid #6b728040;'>
                        <span class='tooltip-title'>💡 기본 분석 기준</span>
                        • <b>최소 건수:</b> 최근 3개월 누적 낙찰건수 10건 이상<br>
                        • <b>이상치 제거:</b> 기존 LTV 기준표 대비 위아래로 30%를 초과하는 값은 극단값으로 간주하여 평균 계산에서 제외
                    </div>
                    <div class='tooltip-item'>
                        <span class='tooltip-title'>🔴 레드 시그널 (조정 대상)</span>
                        3M·6M·12M 낙찰값이 모두 기존 LTV와 10%p 이상 차이, 상/하향 추세 뚜렷함
                    </div>
                    <div class='tooltip-item'>
                        <span class='tooltip-title'>🟡 옐로우 시그널 (검토 대상)</span>
                        3M·6M·12M 낙찰값이 모두 기존 LTV와 5~9%p 차이, 방향성 존재
                    </div>
                </div>
            </div>
        </div>
        """,
        unsafe_allow_html=True
    )

with u_c2:
    st.markdown("""
        <style>
        div[data-testid="stRadio"] > div {
            display: flex;
            justify-content: flex-end !important;
            align-items: center;
            flex-wrap: nowrap !important;
            width: 100%;
            margin-top: 0 !important;
            padding-top: 0 !important;
        }

        div[data-testid="stRadio"] div[role="radiogroup"] > label {
            margin-bottom: 0 !important;
            padding: 2px 6px !important;
            min-height: 18px !important;
            font-size: 8px !important;
            white-space: nowrap !important;
        }

        div[data-testid="stRadio"] {
            height: 30px;
            display: flex;
            align-items: center;
            justify-content: flex-end;
            margin-bottom: -15px !important;
        }
        </style>
    """, unsafe_allow_html=True)

    st.radio(
        "긴급 카드 필터",
        ["전체", "조정 대상", "검토 대상", "참고 대상"],
        key="filter_urgent_mode",
        horizontal=True,
        label_visibility="collapsed"
    )

urgent_display_df = urgent_cards_df[urgent_cards_df["tone"].isin(["red", "yellow"])].copy() if not urgent_cards_df.empty else pd.DataFrame()
if not urgent_display_df.empty:
    urgent_mode = st.session_state.get("filter_urgent_mode", "전체")
    if urgent_mode == "조정 대상":
        urgent_display_df = urgent_display_df[(urgent_display_df["tone"] == "red") & (urgent_display_df["direction"] == "▼")]
    elif urgent_mode == "검토 대상":
        urgent_display_df = urgent_display_df[(urgent_display_df["tone"] == "yellow") & (urgent_display_df["direction"] == "▼")]
    elif urgent_mode == "참고 대상":
        urgent_display_df = urgent_display_df[urgent_display_df["direction"] == "▲"]

if urgent_display_df.empty:
    st.success("현재 조건을 충족하는 조정 대상이 없습니다.")
else:
    def enforce_round_to_5(val):
        try: val = float(val)
        except: return 0.0
        r = val % 5
        return val - r if r <= 3 else val + (5 - r)

    urgent_display_df["conservative_ltv"] = urgent_display_df["conservative_ltv"].apply(enforce_round_to_5)
    urgent_display_df["relaxed_ltv"] = urgent_display_df["relaxed_ltv"].apply(enforce_round_to_5)
    urgent_display_df["conservative_delta"] = urgent_display_df["conservative_ltv"] - urgent_display_df["current_ltv"]
    urgent_display_df["relaxed_delta"] = urgent_display_df["relaxed_ltv"] - urgent_display_df["current_ltv"]

    urgent_display_df["target_delta"] = urgent_display_df.apply(
        lambda row: row["conservative_delta"] if row["tone"] == "red" else row["relaxed_delta"], axis=1
    )
    urgent_display_df["abs_target_delta"] = urgent_display_df["target_delta"].abs()
    urgent_display_df["is_downward"] = urgent_display_df["target_delta"] < 0
    urgent_display_df = urgent_display_df.sort_values(
        by=["is_downward", "abs_target_delta"], ascending=[False, False]
    )

    tbl_ratio = [0.9, 1.7, 0.8, 0.8, 0.8, 3.0, 1.3, 0.7]
    header_cols = st.columns(tbl_ratio)
    headers = ["상태", "지역 / 용도", "현재 LTV", "보수적 안", "완화적 안", "권고안 산출 사유", "최종 설정 LTV", "상세 검토"]
    for col, label in zip(header_cols, headers):
        extra_class = " center" if "안" in label or "LTV" in label else ""
        col.markdown(f"<div class='table-head-cell{extra_class}'>{label}</div>", unsafe_allow_html=True)

    for i, (_, item) in enumerate(urgent_display_df.iterrows()):
        is_red = item["tone"] == "red"
        target_ltv_val = item['conservative_ltv'] if is_red else item['relaxed_ltv']

        raw_reason = str(item['reason']).strip()
        if is_red:
            parts = raw_reason.split("완화적안")
            if len(parts) == 1: parts = raw_reason.split("완화적 안")
            p = parts[0].replace("보수적안:", "").replace("보수적 안:", "").replace("보수적안", "").replace("보수적 안", "").strip()
            if p.endswith("<br>"): p = p[:-4].strip()
            reason_text = p if p else raw_reason
        else:
            parts = raw_reason.split("완화적안:")
            if len(parts) == 1: parts = raw_reason.split("완화적 안:")
            if len(parts) == 1: parts = raw_reason.split("완화적안")
            if len(parts) == 1: parts = raw_reason.split("완화적 안")
            if len(parts) > 1:
                p = parts[-1].replace(":", "", 1).strip()
                reason_text = p if p else raw_reason
            else:
                reason_text = raw_reason

        is_upward = item.get("direction", "") == "▲"
        badge_key = "green" if is_upward else item["tone"]
        if badge_key not in URGENT_BADGE:
            badge_key = "green"

        row_class = URGENT_BADGE[badge_key]["class"]
        st.markdown(f"<div class='table-row {row_class}'></div>", unsafe_allow_html=True)
        cols = st.columns(tbl_ratio, vertical_alignment="center")
        badge_html = f"<span class='status-badge {row_class}'>{URGENT_BADGE[badge_key]['label']}</span>"
        cols[0].markdown(badge_html, unsafe_allow_html=True)
        cols[1].markdown(
            f"<div class='main-cell'>{item['region']}</div><div class='sub-cell'>{item['usage']} / {item['category']}</div>",
            unsafe_allow_html=True,
        )
        cols[2].markdown(f"<div class='ltv-col'><div class='ltv-big'>{item['current_ltv']:.0f}%</div><div class='delta-text'>&nbsp;</div></div>", unsafe_allow_html=True)
        if is_red:
            c_html = f"<div class='ltv-col'><div class='ltv-big'>{item['conservative_ltv']:.0f}%</div><div class='delta-text {'down' if item['conservative_delta'] < 0 else 'up' if item['conservative_delta'] > 0 else 'flat'}'>{item['conservative_delta']:+.0f}%</div></div>"
            r_html = "<div class='ltv-col' style='color:#cbd5e1; font-weight:600;'>-</div>"
        else:
            c_html = "<div class='ltv-col' style='color:#cbd5e1; font-weight:600;'>-</div>"
            r_html = f"<div class='ltv-col'><div class='ltv-big'>{item['relaxed_ltv']:.0f}%</div><div class='delta-text {'down' if item['relaxed_delta'] < 0 else 'up' if item['relaxed_delta'] > 0 else 'flat'}'>{item['relaxed_delta']:+.0f}%</div></div>"

        cols[3].markdown(c_html, unsafe_allow_html=True)
        cols[4].markdown(r_html, unsafe_allow_html=True)
        cols[5].markdown(f"<div class='reason-cell'>{reason_text}</div>", unsafe_allow_html=True)
        
        final_key = f"final_ltv_{i}"
        if final_key not in st.session_state:
            st.session_state[final_key] = int(target_ltv_val)
        
        with cols[6]:
            ic1, ic2 = st.columns([0.65, 0.35], vertical_alignment="center")
            val = ic1.number_input("최종 설정 LTV", key=final_key, disabled=False, label_visibility="collapsed", step=5)
            if ic2.button("✔️", key=f"row_apply_btn_{i}", use_container_width=True, help="입력한 최종 설정 LTV 값을 확정합니다."):
                save_final_ltv(item["region"], item["usage"], val)

        if cols[7].button("보기", key=f"urgent_btn_{i}", use_container_width=True):
            show_details_dialog(item["region"], item["category"], item["usage"], item["current_ltv"], global_winning_df, analysis_mode, outlier_threshold, item["conservative_ltv"], item["relaxed_ltv"], item["reason"], current_base_dt)
st.write("")
st.write("")
st.markdown("<div class='section-spacer'></div>", unsafe_allow_html=True)
st.markdown("<div class='section-title'>📊 지역·용도별 LTV 적정성 매트릭스</div>", unsafe_allow_html=True)
st.markdown("<div class='section-desc'>상태·지역·용도 필터로 필요한 항목만 추려서 기간별 적정성을 비교할 수 있습니다.</div>", unsafe_allow_html=True)


def handle_reset():
    st.session_state["region_select_matrix"] = "전체 지역"
    st.session_state["filter_status_mode"] = "전체"
    st.session_state["filter_category"] = "전체"
    st.session_state["filter_usage"] = "전체"


bar1, bar2, bar3, bar4, bar5 = st.columns([1.1, 1.1, 1.1, 1.6, 0.6])
with bar1:
    unique_regions = sorted(df["시도"].dropna().unique())
    st.selectbox("지역 선택", ["전체 지역"] + unique_regions, key="region_select_matrix")
with bar2:
    unique_cats = sorted(ltv_standards["구분"].unique().tolist()) if ltv_standards is not None else []
    st.selectbox("대분류 선택", ["전체"] + unique_cats, key="filter_category")
with bar3:
    unique_usages = sorted(matrix_df["용도"].unique()) if not matrix_df.empty else []
    st.selectbox("용도 선택", ["전체"] + unique_usages, key="filter_usage")
with bar4:
    st.markdown("<div class='filter-pill-container'>", unsafe_allow_html=True)
    st.radio("상태 필터", ["전체", "조정 대상", "검토 대상"], key="filter_status_mode", horizontal=True, label_visibility="hidden")
    st.markdown("</div>", unsafe_allow_html=True)
with bar5:
    st.markdown("<div style='margin-top:28px;'></div>", unsafe_allow_html=True)
    st.button("초기화", use_container_width=True, on_click=handle_reset)

show_cols = ["지역", "대분류", "용도", "LTV"] + COL_LABELS
matrix_display_df = matrix_df[show_cols].copy().sort_values(["지역", "대분류", "용도"]) if not matrix_df.empty else pd.DataFrame(columns=show_cols)

if st.session_state["region_select_matrix"] != "전체 지역":
    matrix_display_df = matrix_display_df[matrix_display_df["지역"] == st.session_state["region_select_matrix"]]
if st.session_state["filter_category"] != "전체":
    matrix_display_df = matrix_display_df[matrix_display_df["대분류"] == st.session_state["filter_category"]]
if st.session_state["filter_usage"] != "전체":
    matrix_display_df = matrix_display_df[matrix_display_df["용도"] == st.session_state["filter_usage"]]

status_mode = st.session_state.get("filter_status_mode", "전체")
if status_mode == "조정 대상":
    matrix_display_df = matrix_display_df[matrix_df.loc[matrix_display_df.index, "signal_tone"] == "red"]
elif status_mode == "검토 대상":
    matrix_display_df = matrix_display_df[matrix_df.loc[matrix_display_df.index, "signal_tone"] == "yellow"]

count_cols = [f"{label}_count" for label in COL_LABELS]
if not matrix_display_df.empty:
    matrix_display_df = matrix_display_df[matrix_df.loc[matrix_display_df.index, count_cols].max(axis=1) >= 1]

st.markdown(f"<div class='result-count'>검색 결과 <b>{len(matrix_display_df)}</b>건</div>", unsafe_allow_html=True)

matrix_header = st.columns([1.0, 1.2, 1.3, 0.8, 0.8, 0.8, 0.8, 0.8, 0.8, 0.9])
for col, label in zip(matrix_header, ["지역", "대분류", "용도", "LTV"] + COL_LABELS + ["상세"]):
    col.markdown(f"<div class='matrix-head'>{label}</div>", unsafe_allow_html=True)

with st.container(height=620, border=False):
    if matrix_display_df.empty:
        st.info("현재 필터 조건에 맞는 항목이 없습니다.")
    else:
        for idx, row in matrix_display_df.iterrows():
            cols = st.columns([1.0, 1.2, 1.3, 0.8, 0.8, 0.8, 0.8, 0.8, 0.8, 0.9], vertical_alignment="center")
            cols[0].markdown(f"<div class='matrix-text strong'>{row['지역']}</div>", unsafe_allow_html=True)
            cols[1].markdown(f"<div class='matrix-text'>{row['대분류']}</div>", unsafe_allow_html=True)
            cols[2].markdown(f"<div class='matrix-text'>{row['용도']}</div>", unsafe_allow_html=True)
            cols[3].markdown(f"<div class='matrix-text strong'>{row['LTV']:.0f}%</div>", unsafe_allow_html=True)
            for i, col_label in enumerate(COL_LABELS):
                stat = row[col_label]
                conf = STATUS_MAP[stat]
                cols[4 + i].markdown(
                    f"<div class='matrix-dot-wrap'><span class='matrix-dot' style='background:{conf['dot']}; box-shadow:0 0 0 4px {conf['dot']}22;'></span></div>",
                    unsafe_allow_html=True,
                )
            if cols[9].button("보기", key=f"matrix_btn_{idx}", use_container_width=True):
                # Search if this item has LLM advice generated inside urgent_cards_df
                matched_urgent = urgent_cards_df[(urgent_cards_df["reg"] == row["지역"]) & (urgent_cards_df["usage_type"] == row["용도"])] if not urgent_cards_df.empty else pd.DataFrame()
                if not matched_urgent.empty:
                    match_row = matched_urgent.iloc[0]
                    show_details_dialog(row["지역"], row["대분류"], row["용도"], row["LTV"], global_winning_df, analysis_mode, outlier_threshold, match_row.get("conservative_ltv"), match_row.get("relaxed_ltv"), match_row.get("reason"), current_base_dt)
                else:
                    show_details_dialog(row["지역"], row["대분류"], row["용도"], row["LTV"], global_winning_df, analysis_mode, outlier_threshold, base_dt=current_base_dt)
            st.markdown("<div class='matrix-divider'></div>", unsafe_allow_html=True)
