"""자회사용: DB 없이 raw CSV만으로 시그널(RED/YELLOW/GREEN)을 계산한다.
RED/YELLOW는 하향(▼) 추세일 때의 리스크 심각도, GREEN은 상향(▲) 추세(과담보) 참고 신호다.

backend/services.py의 _run_aggregated_sql() + check_signal_logic()을 pandas로 포팅한 것.
DB에 전혀 접속하지 않으며, LTV 기준표는 로컬 정적 파일(data/LTV_기준(*).csv)을 사용한다.

주의: 은행 담당자가 웹 화면에서 LTV 기준값을 바꿔도 이 로컬 파일은 자동으로 갱신되지 않는다.
      최신 값으로 계산하려면 내부에서 data/LTV_기준(광주은행).csv / data/LTV_기준(전북은행).csv를
      최신 상태로 교체해서 자회사에 다시 전달해야 한다.
"""
from __future__ import annotations

import argparse
import os
from datetime import datetime

import pandas as pd
from dateutil.relativedelta import relativedelta

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.abspath(os.path.join(SCRIPT_DIR, ".."))
DATA_DIR = os.path.join(PROJECT_ROOT, "data")

MAPPING_CSV = os.path.join(DATA_DIR, "양행별_용도_리스트_최신.csv")

BANK_LTV_FILE = {
    "광주은행": os.path.join(DATA_DIR, "LTV_기준(광주은행).csv"),
    "전북은행": os.path.join(DATA_DIR, "LTV_기준(전북은행).csv"),
}
BANK_LTV_COL = {"광주은행": "LTV_광주", "전북은행": "LTV_전북"}

SIGNAL_COLUMNS = {
    "bank_name": "은행명",
    "base_ym": "기준월",
    "region": "지역",
    "category": "구분",
    "usage_type": "용도",
    "ltv_value": "현재LTV",
    "signal_tone": "시그널",
    "signal_direction": "방향",
    "suggested_ltv": "권고LTV",
    "adjust_delta": "조정폭",
    "gap3": "3개월갭",
    "avg_3": "3개월평균",
    "avg_6": "6개월평균",
    "avg_12": "12개월평균",
    "avg_36": "36개월평균",
    "avg_60": "60개월평균",
    "cnt_3": "3개월건수",
    "cnt_6": "6개월건수",
    "cnt_12": "12개월건수",
    "cnt_36": "36개월건수",
    "cnt_60": "60개월건수",
    "reason": "판단근거",
}

# backend/services.py::REGION_COL_MAP 과 동일
REGION_COL_MAP = {
    "서울특별시": "서울", "인천광역시": "인천", "경기도": "경기",
    "광주광역시": "광주", "전라남도": "전남", "전라북도": "전북",
    "부산광역시": "부산", "대전광역시": "대전", "대구광역시": "대구",
    "울산광역시": "울산", "세종특별자치시": "세종", "충청북도": "충북",
    "충청남도": "충남", "경상북도": "경북", "경상남도": "경남",
    "제주특별자치도": "제주", "강원도": "강원",
    **{v: v for v in ["서울", "인천", "경기", "광주", "전남", "전북", "부산",
                       "대전", "대구", "울산", "세종", "충북", "충남", "경북",
                       "경남", "제주", "강원"]},
}


def map_ltv_usage(usage, bank_dict):
    """scripts/batch_ltv_update.py::map_ltv_usage 와 동일 (LTV_광주/LTV_전북 재계산용)."""
    if not isinstance(usage, str):
        usage = str(usage)
    if usage in bank_dict:
        val = bank_dict[usage]
        if pd.notna(val) and str(val).strip() != "":
            return val
    if "건물" in usage or any(k in usage for k in ["주택", "아파트", "빌라", "시설", "센터", "상가"]):
        return "기타건물"
    return "기타토지"


def check_signal_logic(metrics, ltv):
    """backend/services.py::check_signal_logic 과 동일 로직 (포팅본, 원본과 동기화 필요)."""
    avg12, avg6, avg3 = metrics["avg"][12], metrics["avg"][6], metrics["avg"][3]
    cnt3 = metrics["count"][3]
    if not all(v is not None for v in [avg12, avg6, avg3]):
        return None

    d12, d6, d3 = avg12 - ltv, avg6 - ltv, avg3 - ltv
    g12, g6, g3 = round(abs(d12), 1), round(abs(d6), 1), round(abs(d3), 1)

    if cnt3 < 10:
        return None

    weighted_gap = (g3 * 5 + g6 * 3 + g12 * 2) / 10.0
    is_red = weighted_gap >= 10
    is_yellow = (weighted_gap >= 5) and not is_red
    if not (is_red or is_yellow):
        return None

    is_pos = all(d > 0 for d in [d12, d6, d3])
    is_neg = all(d < 0 for d in [d12, d6, d3])
    is_golden = avg3 > avg6
    is_dead = avg3 < avg6

    direction = "▲" if (is_pos and is_golden) else ("▼" if (is_neg and is_dead) else None)
    if not direction:
        return None

    # 상향(낙찰가율이 LTV보다 높고 계속 오르는 추세)은 리스크가 아니라 과담보 상태 참고 신호(Green).
    # 실제 리스크 심각도(RED/YELLOW)는 하향 추세일 때만 매긴다.
    tone = "green" if direction == "▲" else ("red" if is_red else "yellow")

    suggested_ltv = round(avg12 if direction == "▲" else avg3, 1)
    adjust_delta = round(suggested_ltv - ltv, 1)

    return {
        "direction": direction,
        "tone": tone,
        "gap3": round(avg3 - ltv, 2),
        "suggested_ltv": suggested_ltv,
        "adjust_delta": adjust_delta,
        "reason": f"3/6/12개월 가중평균 낙찰가율이 기존 LTV와 {'10%p 이상' if is_red else '5%p 이상'} 차이, 건수 충족, {'상향' if direction == '▲' else '하향'} 추세 확인",
    }


def _load_ltv_lookup(bank_name: str):
    path = BANK_LTV_FILE[bank_name]
    df = pd.read_csv(path)
    id_cols = [c for c in ["적용시작일", "구분", "담보종류"] if c in df.columns]
    region_cols = [c for c in df.columns if c not in id_cols]
    melted = df.melt(id_vars=id_cols, value_vars=region_cols, var_name="region", value_name="ltv_value")
    melted = melted.dropna(subset=["ltv_value"])

    ltv_lookup = {}
    category_lookup = {}
    for _, row in melted.iterrows():
        ltv_lookup[(row["담보종류"], row["region"])] = float(row["ltv_value"])
        category_lookup[row["담보종류"]] = row["구분"]
    return ltv_lookup, category_lookup


def _map_region(bank_name: str, df: pd.DataFrame) -> pd.Series:
    mapped = df["시도"].astype(str).str.strip().map(REGION_COL_MAP).fillna("경기")
    if bank_name != "전북은행":
        return mapped

    mapped = mapped.where(~mapped.isin(["광주", "대구", "울산", "부산"]), "광역시")
    mask_jb = df["시도"].astype(str).isin(["전북", "전라북도"])
    district = df["시군구"].astype(str)
    mapped = mapped.mask(mask_jb & district.str.contains("전주", na=False), "전주")
    mapped = mapped.mask(mask_jb & district.str.contains("군산", na=False), "군산")
    mapped = mapped.mask(mask_jb & district.str.contains("익산", na=False), "익산")
    return mapped


def calc_signal(raw_csv_path: str, bank_name: str, base_ym: str, outlier_thresh: float = 0.3) -> pd.DataFrame:
    map_df = pd.read_csv(MAPPING_CSV)
    bank_dict = map_df.set_index("원천데이터_용도")[BANK_LTV_COL[bank_name]].to_dict()

    df = pd.read_csv(raw_csv_path)
    df = df[df["결과"].astype(str).str.contains("낙찰", na=False)].copy()
    df["매각일"] = pd.to_datetime(df["매각일"], errors="coerce")
    df = df.dropna(subset=["매각일"])
    df["낙찰율"] = pd.to_numeric(df["낙찰율"], errors="coerce")
    df = df.dropna(subset=["낙찰율"])

    month_end = pd.to_datetime(base_ym, format="%Y%m").to_period("M").to_timestamp("M")
    df = df[df["매각일"] <= month_end].copy()

    df["mapped_region"] = _map_region(bank_name, df)
    df["usage_mapped"] = df["용도"].apply(lambda u: map_ltv_usage(u, bank_dict))

    ltv_lookup, category_lookup = _load_ltv_lookup(bank_name)
    df["ltv_value"] = df.apply(lambda r: ltv_lookup.get((r["usage_mapped"], r["mapped_region"])), axis=1)
    df["category"] = df["usage_mapped"].map(category_lookup)
    df = df.dropna(subset=["ltv_value", "category"])

    if df.empty:
        return pd.DataFrame(columns=list(SIGNAL_COLUMNS.values()))

    df["not_outlier"] = (df["낙찰율"] - df["ltv_value"]).abs() <= df["ltv_value"] * outlier_thresh

    group_keys = ["mapped_region", "usage_mapped", "category", "ltv_value"]
    groups = df[group_keys].drop_duplicates()

    windows = [3, 6, 12, 36, 60]
    metric_frames = {}
    for m in windows:
        start = month_end - relativedelta(months=m)
        mask = df["not_outlier"] & (df["매각일"] > start) & (df["매각일"] <= month_end)
        metric_frames[m] = df[mask].groupby(group_keys)["낙찰율"].agg(["mean", "count"])

    results = []
    for _, g in groups.iterrows():
        key = tuple(g[k] for k in group_keys)
        metrics = {"avg": {}, "count": {}}
        for m in windows:
            agg = metric_frames[m]
            if key in agg.index:
                metrics["avg"][m] = float(agg.loc[key, "mean"])
                metrics["count"][m] = int(agg.loc[key, "count"])
            else:
                metrics["avg"][m] = None
                metrics["count"][m] = 0

        signal = check_signal_logic(metrics, g["ltv_value"])
        if not signal:
            continue

        results.append({
            "bank_name": bank_name,
            "base_ym": base_ym,
            "region": g["mapped_region"],
            "category": g["category"],
            "usage_type": g["usage_mapped"],
            "ltv_value": g["ltv_value"],
            "signal_tone": signal["tone"],
            "signal_direction": signal["direction"],
            "suggested_ltv": signal["suggested_ltv"],
            "adjust_delta": signal["adjust_delta"],
            "gap3": signal["gap3"],
            "avg_3": metrics["avg"][3],
            "avg_6": metrics["avg"][6],
            "avg_12": metrics["avg"][12],
            "avg_36": metrics["avg"][36],
            "avg_60": metrics["avg"][60],
            "cnt_3": metrics["count"][3],
            "cnt_6": metrics["count"][6],
            "cnt_12": metrics["count"][12],
            "cnt_36": metrics["count"][36],
            "cnt_60": metrics["count"][60],
            "reason": signal["reason"],
        })

    if not results:
        return pd.DataFrame(columns=list(SIGNAL_COLUMNS.values()))

    out = pd.DataFrame(results)
    out = out[list(SIGNAL_COLUMNS.keys())].rename(columns=SIGNAL_COLUMNS)
    out["시그널"] = out["시그널"].str.upper()
    return out


def main():
    parser = argparse.ArgumentParser(description="DB 없이 raw CSV만으로 시그널(RED/YELLOW) 계산")
    parser.add_argument("--raw-csv", required=True, help="raw 데이터 CSV 경로")
    parser.add_argument(
        "--base-ym",
        default=None,
        help="기준연월(YYYYMM), 예: 202608. 미지정시 이번 달(오늘 날짜 기준)",
    )
    parser.add_argument("--banks", nargs="+", default=["광주은행", "전북은행"], choices=["광주은행", "전북은행"])
    parser.add_argument("--out-dir", default=os.path.join(DATA_DIR, "export"), help="CSV 저장 폴더")
    parser.add_argument(
        "--no-date-suffix",
        action="store_true",
        help="파일명에 날짜(YYYYMMDD)를 붙이지 않고 고정 파일명으로 덮어쓰기",
    )
    args = parser.parse_args()

    base_ym = args.base_ym or datetime.now().strftime("%Y%m")

    frames = []
    for bank in args.banks:
        df = calc_signal(args.raw_csv, bank, base_ym)
        print(f"[OK] {bank} 시그널 계산 완료: {len(df):,}건")
        frames.append(df)

    combined = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
    if not combined.empty:
        combined = combined.sort_values(["은행명", "지역", "기준월"])

    os.makedirs(args.out_dir, exist_ok=True)
    suffix = "" if args.no_date_suffix else f"_{datetime.now().strftime('%Y%m%d')}"
    out_path = os.path.join(args.out_dir, f"signal_data{suffix}.csv")
    combined.to_csv(out_path, index=False, encoding="utf-8-sig")
    print(f"[OK] 시그널 데이터 export (기준월: {base_ym}) -> {out_path} ({len(combined):,}건)")


if __name__ == "__main__":
    main()
