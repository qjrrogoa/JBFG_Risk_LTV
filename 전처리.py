"""
전처리.py
---------
크롤링된 원본 CSV(seoul.csv 등)를 읽어
gwangju.csv와 동일한 13개 컬럼 구조로 전처리 후 저장.

출력 컬럼:
  사건번호 / 용도 / 시도 / 시군구 / 소재지 /
  감정가 / 최저가 / 결과 / 낙찰가 / 낙찰율 / 매각일 / 분기 / 기간구분

기간구분 기준 (가장 최근 매각일 기준):
  0: 최근 3개월 이내
  1: 최근 3~6개월
  2: 최근 6~12개월
  3: 그 외 (12개월 초과)
"""

import pandas as pd
from dateutil.relativedelta import relativedelta


# ─────────────────────────────────────────
# 설정
# ─────────────────────────────────────────
INPUT_CSV  = "data/seoul.csv"     # 원본 크롤링 결과
OUTPUT_CSV = "data/seoul.csv"     # 전처리 결과 저장 경로 (덮어쓰기)


# ─────────────────────────────────────────
# 1. 로드
# ─────────────────────────────────────────
df = pd.read_csv(INPUT_CSV)
print(f"[1] 로드 완료: {df.shape}")


# ─────────────────────────────────────────
# 2. 결과 컬럼 정리
#    '낙찰(1/2)' → '낙찰' / '취하(0/0)' → '취하' / 빈 값 → '취하'
# ─────────────────────────────────────────
# 괄호 앞 단어만 추출
df["결과"] = df["결과"].fillna("").astype(str).str.split("(").str[0].str.strip()

# 빈 문자열은 '취하'로
df.loc[df["결과"] == "", "결과"] = "취하"

print("[2] 결과 컬럼 정리 완료")
print("    unique:", df["결과"].unique())


# ─────────────────────────────────────────
# 3. 매각일 → datetime
#    '2001.1.9' 형식 처리
# ─────────────────────────────────────────
df["매각일"] = pd.to_datetime(df["매각일"], errors="coerce")

null_dates = df["매각일"].isna().sum()
if null_dates > 0:
    print(f"[경고] 매각일 변환 실패 {null_dates}건 → 해당 행 제거")
    df = df.dropna(subset=["매각일"])

print(f"[3] 매각일 변환 완료: {df.shape}")


# ─────────────────────────────────────────
# 4. 시도 / 시군구 추출
#    소재지: '서울 강남구 삼성동 ...'
# ─────────────────────────────────────────
df["시도"]  = df["소재지"].str.split().str[0]
df["시군구"] = df["소재지"].str.split().str[1]

print("[4] 시도/시군구 추출 완료")
print("    시도 unique:", df["시도"].unique())


# ─────────────────────────────────────────
# 5. 분기 컬럼 생성 (YYYY_nQ)
# ─────────────────────────────────────────
df["분기"] = (
    df["매각일"].dt.year.astype(str)
    + "_"
    + df["매각일"].dt.quarter.astype(str)
    + "Q"
)
print("[5] 분기 컬럼 생성 완료")


# ─────────────────────────────────────────
# 6. 기간구분 컬럼 생성
#    기준: 가장 최근 매각일
# ─────────────────────────────────────────
last_date = df["매각일"].max()
print(f"[6] 기준일(최근 매각일): {last_date.date()}")

def get_period_code(d):
    if d > last_date - relativedelta(months=3):
        return 0
    elif d > last_date - relativedelta(months=6):
        return 1
    elif d > last_date - relativedelta(months=12):
        return 2
    else:
        return 3

df["기간구분"] = df["매각일"].apply(get_period_code)
print("    기간구분 분포:")
print(df["기간구분"].value_counts().sort_index().to_string())


# ─────────────────────────────────────────
# 7. 컬럼 정렬 (gwangju.csv 동일 순서)
# ─────────────────────────────────────────
COLS = [
    "사건번호", "용도", "시도", "시군구", "소재지",
    "감정가", "최저가", "결과", "낙찰가", "낙찰율",
    "매각일", "분기", "기간구분",
]
df = df[COLS]
print(f"[7] 컬럼 정렬 완료: {list(df.columns)}")


# ─────────────────────────────────────────
# 8. 저장
# ─────────────────────────────────────────
df.to_csv(OUTPUT_CSV, index=False, encoding="utf-8-sig")
print(f"[8] 저장 완료: {OUTPUT_CSV}  ({df.shape[0]:,}행)")
