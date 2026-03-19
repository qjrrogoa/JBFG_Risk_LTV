import pandas as pd
import numpy as np
from datetime import datetime
from dateutil.relativedelta import relativedelta
import warnings

warnings.filterwarnings('ignore')

# LTV 설정 모사
LTV_CONFIG = {
    "광주": {
        "주택": {"단독주택": 75, "다가구": 60, "아파트": 80, "연립": 70, "다세대": 60, "근린주택": 65},
        "건물": {"근린상가": 60, "공장": 75, "아파트상가": 55, "오피스텔": 65, "의료시설": 50, "숙박시설": 50},
        "토지": {"대지": 75, "전": 60, "답": 75, "임야": 65}
    },
    "서울": {
        "주택": {"단독주택": 75, "다가구": 75, "아파트": 80, "연립": 80, "다세대": 80, "근린주택": 80},
        "건물": {"근린상가": 60, "공장": 75, "아파트상가": 60, "오피스텔": 75, "의료시설": 60, "숙박시설": 75},
        "토지": {"대지": 60, "전": 75, "답": 70, "임야": 50}
    }
}

def map_usage_to_config(usage):
    if not isinstance(usage, str):
        return str(usage)
    if usage in ["연립주택", "연립"]: return "연립"
    if usage in ["병원", "의료시설"]: return "의료시설"
    if "오피스텔" in usage: return "오피스텔"
    if "나대지" in usage or usage == "대지": return "대지"
    return usage

def load_and_preprocess(filepath):
    df = pd.read_csv(filepath)
    def parse_currency(x):
        if isinstance(x, str): return int(x.replace(',', ''))
        return x
    def parse_percentage(x):
        if isinstance(x, str): return float(x.replace('%', ''))
        return x
    df['낙찰가'] = df['낙찰가'].apply(parse_currency)
    df['감정가'] = df['감정가'].apply(parse_currency)
    df['낙찰율'] = df['낙찰율'].apply(parse_percentage)
    df['매각일'] = pd.to_datetime(df['매각일'])
    df['분석용도'] = df['용도'].apply(map_usage_to_config)
    # 낙찰 매각건 필터링
    if '결과' in df.columns:
        df = df[df['결과'].astype(str).str.contains('낙찰|매각', na=False)]
    return df

try:
    gwangju_df = load_and_preprocess('data/gwangju.csv')
    seoul_df = load_and_preprocess('data/seoul.csv')
    df_all = pd.concat([gwangju_df, seoul_df], ignore_index=True)
except Exception as e:
    print(f"데이터 로드 실패: {e}")
    exit(1)

results = []

for region, categories in LTV_CONFIG.items():
    region_df = df_all[df_all['시도'] == region]
    if region_df.empty:
        continue
    last_date = region_df['매각일'].max()
    
    date_3m_ago = last_date - relativedelta(months=3)
    date_6m_ago = last_date - relativedelta(months=6)
    
    for cat, usages in categories.items():
        for usage, ltv in usages.items():
            sub_df = region_df[region_df['분석용도'] == usage]
            if sub_df.empty:
                continue
                
            # 1. 시그널 발생: 3개월, 6개월, 12개월 평균 낙찰가율과 LTV의 격차가 기준치 이상 벌어졌는지
            date_12m_ago = last_date - relativedelta(months=12)
            
            df_3m_cond = sub_df[(sub_df['매각일'] > date_3m_ago) & (sub_df['매각일'] <= last_date)]
            df_6m_cond = sub_df[(sub_df['매각일'] > date_6m_ago) & (sub_df['매각일'] <= last_date)]
            df_12m_cond = sub_df[(sub_df['매각일'] > date_12m_ago) & (sub_df['매각일'] <= last_date)]
            
            # 각 기간별 초기화
            avg_3m = df_3m_cond['낙찰율'].mean() if not df_3m_cond.empty else None
            avg_6m = df_6m_cond['낙찰율'].mean() if not df_6m_cond.empty else None
            avg_12m = df_12m_cond['낙찰율'].mean() if not df_12m_cond.empty else None
            
            # 10% 이상 격차 플래그
            gap10_3m = bool(avg_3m is not None and abs(avg_3m - ltv) >= 10)
            gap10_6m = bool(avg_6m is not None and abs(avg_6m - ltv) >= 10)
            gap10_12m = bool(avg_12m is not None and abs(avg_12m - ltv) >= 10)
            
            # 5~10% 격차 플래그 
            gap5_3m = bool(avg_3m is not None and 5 <= abs(avg_3m - ltv) < 10)
            gap5_6m = bool(avg_6m is not None and 5 <= abs(avg_6m - ltv) < 10)
            gap5_12m = bool(avg_12m is not None and 5 <= abs(avg_12m - ltv) < 10)
            
            # 2. 6개월 내 모수(낙찰 건수) 유기적 조건 검토
            # 과거 3년(36개월) 기준 월 평균 거래량 산출
            df_6m = sub_df[(sub_df['매각일'] > date_6m_ago) & (sub_df['매각일'] <= last_date)]
            count_6m_total = len(df_6m)
            
            # 장기 평균 및 표준편차 계산을 위해 (출력 용도)
            start_date_3y = last_date - relativedelta(years=3)
            df_3y = sub_df[(sub_df['매각일'] >= start_date_3y) & (sub_df['매각일'] <= last_date)]
            if not df_3y.empty:
                long_term_avg = df_3y['낙찰율'].mean()
                long_term_std = df_3y['낙찰율'].std()
            
            mo_condition = False
            if not df_3y.empty:
                # 과거 36개월 동안의 총 거래량을 36으로 나누어 '과거 월 평균 거래량' 계산
                monthly_avg_volume_3y = len(df_3y) / 36.0
                
                # '평소 활성도' 기준: 6개월 동안 최소한 '과거 월 평균 거래량의 3배(즉, 평소 3개월치 거래량)' 이상은 거래가 발생했는가?
                # 예: 평소 한 달에 10건 거래되는 용도라면, 최근 6개월 동안 최소 30건은 거래가 있어야 유의미한 모수로 인정
                dynamic_threshold = monthly_avg_volume_3y * 3.0
                
                # 거래가 원래 거의 없는 용도(월 0.5건 등)를 위해 최소 하한선 5건 설정
                threshold_to_apply = max(5, dynamic_threshold)
                
                if count_6m_total >= threshold_to_apply:
                    mo_condition = True
            
            # 3. 3, 6, 12개월 이동평균 계산 (월별) 및 데드크로스 여부
            # 최근 3년 데이터로 롤링평균 계산
            start_date = last_date - relativedelta(years=3)
            chart_df = sub_df[(sub_df['매각일'] >= start_date) & (sub_df['매각일'] <= last_date)].copy()
            
            dead_cross = False
            golden_cross = False
            ma_3, ma_6, ma_12 = None, None, None
            
            if not chart_df.empty:
                chart_df = chart_df.set_index('매각일').sort_index()
                monthly = chart_df.resample('ME')['낙찰율'].mean()
                
                rolling_3m = monthly.rolling(window=3, min_periods=1).mean()
                rolling_6m = monthly.rolling(window=6, min_periods=1).mean()
                rolling_12m = monthly.rolling(window=12, min_periods=1).mean()
                
                # 마지막 달의 이평선 값 가져오기
                if not rolling_3m.dropna().empty: ma_3 = rolling_3m.dropna().iloc[-1]
                if not rolling_6m.dropna().empty: ma_6 = rolling_6m.dropna().iloc[-1]
                if not rolling_12m.dropna().empty: ma_12 = rolling_12m.dropna().iloc[-1]
                
                if ma_3 is not None and ma_6 is not None and ma_12 is not None:
                    # 데드크로스: 3개월 < 6개월 < 12개월
                    if ma_3 < ma_6 and ma_6 < ma_12:
                        dead_cross = True
                    # 골든크로스: 3개월 > 6개월 > 12개월
                    if ma_3 > ma_6 and ma_6 > ma_12:
                        golden_cross = True
            
            results.append({
                "지역": region,
                "대분류": cat,
                "용도": usage,
                "LTV": ltv,
                "3M_평균낙찰율": round(avg_3m, 2) if avg_3m is not None and pd.notna(avg_3m) else "-",
                "6M_평균낙찰율": round(avg_6m, 2) if avg_6m is not None and pd.notna(avg_6m) else "-",
                "12M_평균낙찰율": round(avg_12m, 2) if avg_12m is not None and pd.notna(avg_12m) else "-",
                "3M_LTV격차10%이상": gap10_3m,
                "6M_LTV격차10%이상": gap10_6m,
                "12M_LTV격차10%이상": gap10_12m,
                "3M_LTV격차5%이상_10%미만": gap5_3m,
                "6M_LTV격차5%이상_10%미만": gap5_6m,
                "12M_LTV격차5%이상_10%미만": gap5_12m,
                "6M_모수": count_6m_total,
                "6M_모수조건충족": mo_condition,
                "데드크로스": dead_cross,
                "골든크로스": golden_cross,
                "최근3M_MA": round(ma_3, 2) if ma_3 is not None else "-",
                "최근6M_MA": round(ma_6, 2) if ma_6 is not None else "-",
                "최근12M_MA": round(ma_12, 2) if ma_12 is not None else "-"
            })

res_df = pd.DataFrame(results)

print("=== 분석 결과 (전체) ===")
print(res_df.to_string(index=False))

print("\n\n=== 1번, 2번, 3번 조건을 모두 만족하는 결과 (최종 필터링 - 3/6개월 기간 모두 10% 이상 격차 발생) ===")
# 조건1: 3M, 6M 두 기간 모두에서 평균 낙찰가율과 LTV 간의 격차가 10%p 이상
# 조건2: 6개월 내 모수 조건 (최근 6개월 거래량이 '과거 3년 월 평균 거래량의 3개월치' 이상인지. 단 최소 5건)
# 조건3: 데드크로스 (3개월 < 6개월 < 12개월)

final_df = res_df[
    (res_df["3M_LTV격차10%이상"] == True) &
    (res_df["6M_LTV격차10%이상"] == True) &
    (res_df["6M_모수조건충족"] == True) &
    (res_df["데드크로스"] == True)
]

print("\n\n=== 1번, 2번, 3번 조건을 모두 만족하는 결과 (최종 필터링 - 3/6개월 기간 모두 5% 이상 ~ 10% 미만 격차 발생) ===")
final_df_5_to_10 = res_df[
    (res_df["3M_LTV격차5%이상_10%미만"] == True) &
    (res_df["6M_LTV격차5%이상_10%미만"] == True) &
    (res_df["6M_모수조건충족"] == True) &
    (res_df["데드크로스"] == True)
]

# if final_df.empty:
#     print("조건을 모두 만족하는 항목이 없습니다.")
# else:
#     print(final_df.to_string(index=False))

# # 결과를 CSV 파일로 저장
# res_df.to_csv("ltv_analysis_all.csv", index=False, encoding="utf-8-sig")
# final_df.to_csv("ltv_analysis_filtered_10퍼이상.csv", index=False, encoding="utf-8-sig")
# final_df_5_to_10.to_csv("ltv_analysis_filtered_5퍼이상_10퍼미만.csv", index=False, encoding="utf-8-sig")
# print("\n결과가 ltv_analysis_all.csv 와 ltv_analysis_filtered_10퍼이상.csv, ltv_analysis_filtered_5퍼이상_10퍼미만.csv 에 저장되었습니다.")
