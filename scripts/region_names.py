"""크롤링 원천 데이터의 시도명을 표준 시도명으로 정규화한다.

인포케어 검색 지역은 '전남광주'처럼 두 개 시도를 묶어 놓은 항목이 있고,
소재지 첫 토큰을 그대로 시도로 쓰기 때문에 표준 시도명이 아닌 값이 들어온다.
표준 시도명이 아니면 backend/services.py 의 REGION_COL_MAP 조회에 실패해
전부 '경기'로 집계되므로, 적재 전 단계에서 반드시 정규화한다.
"""

# backend/services.py 의 REGIONS_ALL 과 동일한 표준 시도명
CANONICAL_PROVINCES = [
    "서울", "인천", "경기", "부산", "대구", "대전", "광주", "울산",
    "전북", "전남", "경북", "경남", "제주", "충남", "충북", "강원", "세종",
]

PROVINCE_ALIASES = {
    "서울특별시": "서울", "인천광역시": "인천", "경기도": "경기",
    "광주광역시": "광주", "전라남도": "전남", "전라북도": "전북",
    "부산광역시": "부산", "대전광역시": "대전", "대구광역시": "대구",
    "울산광역시": "울산", "세종특별자치시": "세종", "충청북도": "충북",
    "충청남도": "충남", "경상북도": "경북", "경상남도": "경남",
    "제주특별자치도": "제주", "강원도": "강원",
    "강원특별자치도": "강원", "전북특별자치도": "전북", "제주도": "제주",
}

# 두 개 시도를 묶어 놓은 검색 지역: (구 단위 시군구일 때 시도, 그 외 시도)
# 광주광역시는 자치구(광산/북/서/남/동구)만, 전남은 시·군만 있어 시군구로 분리된다.
COMBINED_PROVINCES = {
    "전남광주": ("광주", "전남"),
}


def normalize_province(province, district=None):
    """시도명을 표준 시도명으로 변환한다. 판별 불가하면 None."""
    p = str(province or "").strip()
    if not p:
        return None

    if p in COMBINED_PROVINCES:
        gu, rest = COMBINED_PROVINCES[p]
        return gu if str(district or "").strip().endswith("구") else rest

    if p in PROVINCE_ALIASES:
        return PROVINCE_ALIASES[p]
    if p in CANONICAL_PROVINCES:
        return p
    return None


def normalize_address(address, province, district=None):
    """소재지 앞의 합본 지역명('전남광주 ...')을 표준 시도명으로 치환한다."""
    p = str(province or "").strip()
    if p not in COMBINED_PROVINCES:
        return address
    resolved = normalize_province(p, district)
    if not resolved:
        return address
    s = str(address or "")
    return resolved + s[len(p):] if s.startswith(p) else s
