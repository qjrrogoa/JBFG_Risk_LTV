
import pandas as pd
import os
import glob

# 전처리.py의 매핑 함수와 동일하게 정의 (최신 로직 반영)
def map_ltv_gwangju(usage):
    if not isinstance(usage, str): return str(usage)
    if usage in ["단독", "단독주택"]: return "단독"
    if usage in ["다가구", "다가구주택"]: return "다가구"
    if usage == "아파트": return "아파트"
    if usage in ["다세대", "다세대주택"]: return "다세대주택"
    if usage in ["연립", "연립주택", "빌라"]: return "연립주택"
    if usage in ["근린주택"]: return "근린주택"
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
    if usage in ["단독", "단독주택"]: return "단독주택"
    if usage == "아파트": return "아파트"
    if usage in ["다가구", "다가구주택"]: return "다가구"
    if usage in ["다세대", "다세대주택"]: return "다세대"
    if usage in ["연립", "연립주택", "빌라"]: return "연립·빌라"
    if usage in ["근린주택"]: return "근린주택"
    if "주상복합" in usage and "아파트" in usage: return "주상복합아파트"
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
    if "나대지" in usage or usage == "대지": return "나대지"
    if usage == "전": return "전"
    if usage == "답": return "답"
    if "과수원" in usage: return "과수원"
    if usage == "임야": return "임야"
    if usage == "잡종지": return "잡종지"
    if "목장" in usage: return "목장용지"
    if "공장" in usage and "용지" in usage: return "공장용지"
    if "주차장" in usage: return "주차장용지"
    if "염전" in usage: return "염전"
    if any(k in usage for k in ["도로", "구거", "하천", "제방", "유지"]): return "기타토지"
    return "기타건물" if "건물" in usage else "기타토지"

# 1. 모든 CSV 파일 찾기
csv_files = glob.glob("data/*.csv")
# 기준 파일 등 제외
exclude_files = ["LTV_기준(광주은행).csv", "LTV_기준(전북은행).csv", "양행별_용도_리스트.csv", "potential_risk_list.csv"]
csv_files = [f for f in csv_files if os.path.basename(f) not in exclude_files]

all_usages = set()

# 2. 파일별 unique 용도 수집
print(f"총 {len(csv_files)}개 파일을 읽습니다...")
for f in csv_files:
    try:
        df = pd.read_csv(f, usecols=["용도"])
        all_usages.update(df["용도"].dropna().unique())
        print(f" - {os.path.basename(f)} 읽기 완료")
    except Exception as e:
        print(f" - {os.path.basename(f)} 오류: {e}")

# 3. 매핑 수행
usage_list = sorted(list(all_usages))
results = []
for u in usage_list:
    results.append({
        "원천데이터_용도": u,
        "LTV_광주": map_ltv_gwangju(u),
        "LTV_전북": map_ltv_jeonbuk(u)
    })

# 4. 저장
res_df = pd.DataFrame(results)
output_path = "data/양행별_용도_리스트_최신.csv"
res_df.to_csv(output_path, index=False, encoding="utf-8-sig")

print(f"\n최종 리스트 저장 완료: {output_path} (총 {len(res_df)}개 용도)")
