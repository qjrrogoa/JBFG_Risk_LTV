
import pandas as pd
import glob
import os
import subprocess
import sys
from dateutil.relativedelta import relativedelta
from datetime import datetime

# 설정
MAPPING_CSV = "data/양행별_용도_리스트_최신.csv"
DATA_DIR = "data"
REGIONS = ["서울", "인천", "경기", "부산", "대구", "대전", "광주", "울산", "전북", "전남", "경북", "경남", "제주", "충남", "충북", "강원", "세종"]

# 매핑 테이블 로드
try:
    map_df = pd.read_csv(MAPPING_CSV)
    kjb_dict = map_df.set_index("원천데이터_용도")["LTV_광주"].to_dict()
    jbb_dict = map_df.set_index("원천데이터_용도")["LTV_전북"].to_dict()
    print(f"[Loading] Mapping Master: {len(map_df)} items")
except Exception as e:
    print(f"[Error] Mapping table loading failed: {e}")
    sys.exit(1)

def map_ltv_usage(usage, bank_dict):
    if not isinstance(usage, str): usage = str(usage)
    if usage in bank_dict:
        val = bank_dict[usage]
        if pd.notna(val) and str(val).strip() != "":
            return val
    if "건물" in usage or any(k in usage for k in ["주택", "아파트", "빌라", "시설", "센터", "상가"]):
        return "기타건물"
    return "기타토지"

def process_file(file_path):
    print(f"--- Processing: {os.path.basename(file_path)} ---")
    df = pd.read_csv(file_path)
    
    # 1. 용도 결측치 제거
    df = df.dropna(subset=["용도"])
    df = df[df["용도"].astype(str).str.strip() != ""]
    
    # 2. 결과 정리
    # 괄호 앞 단어만 추출
    df["결과"] = df["결과"].fillna("").astype(str).str.split("(").str[0].str.strip()
    # 빈 문자열은 '취하'로
    df.loc[df["결과"] == "", "결과"] = "취하"
    
    # 3. 매각일 변환
    df["매각일"] = pd.to_datetime(df["매각일"], errors="coerce")
    null_dates = df["매각일"].isna().sum()
    if null_dates > 0:
        df = df.dropna(subset=["매각일"])
    
    # 4. 시도/시군구
    df["시도"]  = df["소재지"].str.split().str[0]
    df["시군구"] = df["소재지"].str.split().str[1]
    
    # 5. 분기
    df["분기"] = df["매각일"].dt.year.astype(str) + "_" + df["매각일"].dt.quarter.astype(str) + "Q"
    
    # 6. 기간구분
    last_date = df["매각일"].max()
    def get_period_code(d):
        if pd.isna(d): return 3
        if d > last_date - relativedelta(months=3): return 0
        elif d > last_date - relativedelta(months=6): return 1
        elif d > last_date - relativedelta(months=12): return 2
        else: return 3
    df["기간구분"] = df["매각일"].apply(get_period_code)
    
    # 7. 용도 매핑 (새로운 CSV 기준!)
    df["LTV_광주"] = df["용도"].apply(lambda x: map_ltv_usage(x, kjb_dict))
    df["LTV_전북"] = df["용도"].apply(lambda x: map_ltv_usage(x, jbb_dict))
    
    # 8. 저장 및 fill_zero 실행
    cols = ["사건번호", "용도", "LTV_광주", "LTV_전북", "시도", "시군구", "소재지", "감정가", "최저가", "결과", "낙찰가", "낙찰율", "매각일", "분기", "기간구분"]
    df = df[cols]
    df.to_csv(file_path, index=False, encoding="utf-8-sig")
    
    print(f" [Step 1] Preprocessing done. Starting fill_zero...")
    try:
        subprocess.run(["python", "fill_zero.py", file_path], check=True)
        print(f" [Step 2] {os.path.basename(file_path)} SUCCESS!")
    except Exception as e:
        print(f" [Error] {os.path.basename(file_path)} FAILED: {e}")

# 전체 실행
for r in REGIONS:
    path = os.path.join(DATA_DIR, f"{r}.csv")
    if os.path.exists(path):
        process_file(path)
    else:
        print(f"[Skip] {r}.csv not found.")

print("\nAll regions updated based on the new mapping CSV!")

# 전체 실행
for r in REGIONS:
    path = os.path.join(DATA_DIR, f"{r}.csv")
    if os.path.exists(path):
        process_file(path)
    else:
        print(f"⚠️ {r}.csv 파일을 찾을 수 없습니다.")

print("\n🎉 모든 지역 데이터 일괄 업데이트가 완료되었습니다!")
