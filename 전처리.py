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
INPUT_CSV  = "data/충남.csv"     # 원본 크롤링 결과
OUTPUT_CSV = "data/충남.csv"     # 전처리 결과 저장 경로 (덮어쓰기)


# ─────────────────────────────────────────
# 1. 로드
# ─────────────────────────────────────────
df = pd.read_csv(INPUT_CSV)
print(f"[1] 로드 완료: {df.shape}")


# ─────────────────────────────────────────
# 1.5 용도 결측치 제거
# ─────────────────────────────────────────
# NaN 제거
df = df.dropna(subset=["용도"])
# 빈 문자열 제거
df = df[df["용도"].astype(str).str.strip() != ""]
print(f"[1.5] 용도 결측치 제거 완료: {df.shape}")


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
# 7. LTV 기준별 용도 통일 (광주은행 / 전북은행)
# ─────────────────────────────────────────
def map_ltv_gwangju(usage):
    if not isinstance(usage, str): return str(usage)
    # 주거용
    if usage in ["단독", "단독주택"]: return "단독"
    if usage in ["다가구", "다가구주택"]: return "다가구"
    if usage == "아파트": return "아파트"
    if usage in ["다세대", "다세대주택"]: return "다세대주택"
    if usage in ["연립", "연립주택", "빌라"]: return "연립주택"
    if usage in ["근린주택"]: return "근린주택"
    # 비주거용
    if "지원시설" in usage: return "상가(지식산업센터 지원시설)"
    if "상가" in usage and "아파트" in usage: return "아파트상가"
    if "공장" in usage: return "공장(지식산업센터 업무시설)"
    if "오피스텔" in usage: return "오피스텔"
    if "사무실" in usage or "업무시설" in usage: return "사무실"
    if "병원" in usage or "의료시설" in usage: return "병원"
    if any(k in usage for k in ["숙박", "호텔", "모텔"]): return "숙박시설"
    if any(k in usage for k in ["창고", "노유자", "교육", "연구", "운동", "종교", "자동차", "위락", "문화", "발전", "식물"]):
        return "기타건물"
    if usage in ["근린상가", "상가", "점포"]: return "근린상가"
    # 토지
    if "나대지" in usage or usage == "대지": return "나대지"
    if usage == "전": return "전"
    if usage == "답": return "답"
    if usage == "임야": return "임야"
    if "과수원" in usage: return "과수원"
    if "잡종지" in usage: return "잡종지"
    if any(k in usage for k in ["도로", "구거", "하천", "제방", "유지"]): return "기타토지"
    return "기타건물" if "건물" in usage else "기타토지"

def map_ltv_jeonbuk(usage):
    if not isinstance(usage, str): return str(usage)
    # 주거용
    if usage in ["단독", "단독주택"]: return "단독주택"
    if usage == "아파트": return "아파트"
    if usage in ["다가구", "다가구주택"]: return "다가구"
    if usage in ["다세대", "다세대주택"]: return "다세대"
    if usage in ["연립", "연립주택", "빌라"]: return "연립·빌라"
    if usage in ["근린주택"]: return "근린주택"
    if "주상복합" in usage and "아파트" in usage: return "주상복합아파트"
    # 비주거용
    if usage in ["근린상가", "상가"]: return "근린상가"
    if "점포" in usage: return "점포상가"
    if "병원" in usage or "의료시설" in usage: return "병원"
    if "주유소" in usage: return "주유소"
    if any(k in usage for k in ["숙박", "호텔", "모텔"]): return "숙박시설"
    if "지식산업센터" in usage: return "지식산업센터"
    if "공장" in usage: return "공장"
    if "교회" in usage and "건물" in usage: return "교회건물"
    if "창고" in usage: return "창고"
    if "사무실" in usage or "업무시설" in usage: return "사무실"
    if "오피스텔" in usage: return "오피스텔(업무용)"
    if any(k in usage for k in ["목욕탕", "사우나"]): return "목욕탕(사우나)"
    if "노유자" in usage: return "노유자시설"
    if any(k in usage for k in ["교육", "연구", "운동", "종교", "자동차", "위락", "문화"]): return "기타건물"
    # 토지
    if "나대지" in usage or usage == "대지": return "나대지"
    if usage == "전": return "전"
    if usage == "답": return "답"
    if usage == "과수원" in usage: return "과수원"
    if usage == "임야": return "임야"
    if usage == "잡종지": return "잡종지"
    if "목장" in usage: return "목장용지"
    if "공장" in usage and "용지" in usage: return "공장용지"
    if "주차장" in usage: return "주차장용지"
    if "염전" in usage: return "염전"
    if any(k in usage for k in ["도로", "구거", "하천", "제방", "유지"]): return "기타토지"
    return "기타건물" if "건물" in usage else "기타토지"

df["LTV_광주"] = df["용도"].apply(map_ltv_gwangju)
df["LTV_전북"] = df["용도"].apply(map_ltv_jeonbuk)
print("[7] LTV 기준별 용도 통일 완료")


# ─────────────────────────────────────────
# 8. 컬럼 정렬 (신규 LTV 컬럼 포함)
# ─────────────────────────────────────────
COLS = [
    "사건번호", "용도", "LTV_광주", "LTV_전북", 
    "시도", "시군구", "소재지",
    "감정가", "최저가", "결과", "낙찰가", "낙찰율",
    "매각일", "분기", "기간구분",
]
df = df[COLS]
print(f"[8] 컬럼 정렬 완료: {list(df.columns)}")


# ─────────────────────────────────────────
# 8. 1차 저장 (누락 데이터 수집을 위한 임시 저장)
# ─────────────────────────────────────────
df.to_csv(OUTPUT_CSV, index=False, encoding="utf-8-sig")
print(f"[8] 전처리 결과 1차 저장 완료: {OUTPUT_CSV} ({df.shape[0]:,}행)")


# ─────────────────────────────────────────
# 9. 누락 데이터 자동 채우기 (fill_zero.py 실행)
# ─────────────────────────────────────────
import sys
import subprocess

print(f"\n[9] '{OUTPUT_CSV}'를 대상으로 누락 데이터 보완(fill_zero)을 시작합니다...")
try:
    subprocess.run(["python", "fill_zero.py", OUTPUT_CSV], check=True)
    print("\n[9] 빈칸 채우기 (fill_zero.py) 실행 정상 완료!")
except subprocess.CalledProcessError as e:
    print(f"\n[오류] 빈칸 채우기 실행 중 오류가 발생했습니다: {e}\n프로그램을 종료합니다.")
    sys.exit(1)
except FileNotFoundError:
    print("\n[오류] python 명령어를 찾을 수 없거나 fill_zero.py 파일이 없습니다.\n프로그램을 종료합니다.")
    sys.exit(1)
except Exception as e:
    print(f"\n[오류] 알 수 없는 오류가 발생했습니다: {e}\n프로그램을 종료합니다.")
    sys.exit(1)


# ─────────────────────────────────────────
# 10. 완료된 최종 데이터를 불러와서 백업본과 5년치로 분리 보관
# ─────────────────────────────────────────
import os
from datetime import datetime

print("\n[10] 빈칸 보충이 완료된 데이터를 분리합니다...")
df_filled = pd.read_csv(OUTPUT_CSV, encoding="utf-8-sig")
df_filled["매각일"] = pd.to_datetime(df_filled["매각일"], errors="coerce")

# 전체 원본 백업 파일로 저장 (data/original/ 폴더 활용)
base_name = os.path.basename(OUTPUT_CSV)
dir_name = os.path.dirname(OUTPUT_CSV)
original_dir = os.path.join(dir_name, "original")
os.makedirs(original_dir, exist_ok=True)  # 폴더가 없으면 자동 생성

name_part, ext_part = os.path.splitext(base_name)
archive_csv = os.path.join(original_dir, f"{name_part}_전체원본{ext_part}")

df_filled.to_csv(archive_csv, index=False, encoding="utf-8-sig")
print(f"  └── [10-1] 전체 데이터 백업 완료: {archive_csv} ({df_filled.shape[0]:,}행)")

# 최근 5년 치 데이터 필터링 (동시에 '낙찰' 결과만 쏙 뽑아내기)
five_years_ago = datetime.now() - relativedelta(years=5)
mask_time = df_filled["매각일"] >= five_years_ago
mask_result = df_filled["결과"].astype(str).str.contains("낙찰", na=False)

df_recent = df_filled[mask_time & mask_result].copy()

# 대시보드 로딩용(최근 5년치) 덮어쓰기 저장
df_recent.to_csv(OUTPUT_CSV, index=False, encoding="utf-8-sig")
print(f"  └── [10-2] 대시보드용 최근 5년치 추출 완료: {OUTPUT_CSV} ({df_recent.shape[0]:,}행)\n")
