"""자회사용: DB 없이 사이트 크롤링만으로 기존 raw CSV를 갱신한다.

인포케어 사이트 계정(.env의 INFOCARE_USER_ID/PASSWORD)만 있으면 되고,
DATABASE_URL/DB 계정은 전혀 필요하지 않다 (database.py를 import하는 모듈은 의도적으로 쓰지 않음).

동작:
1. 기존 raw CSV에 있는 매각일 중 가장 최신 날짜 ~ --end-date까지 전 지역을 재크롤링
   (최신 매각일 당일도 포함해서 다시 수집 - 같은 날 결과가 뒤늦게 추가/갱신되는 경우 대비.
    이렇게 겹쳐서 재수집된 사건은 뒤의 3번 중복 제거 단계에서 신규 데이터로 덮어써짐)
2. batch_ltv_update.process_file() / fill_zero.fill_zero_multiple_csvs()로
   기존 파이프라인과 동일하게 전처리(결과필터/시도시군구/분기/기간구분/빈낙찰가 보정)
3. 기존 raw CSV와 병합 후 사건번호 기준 중복 제거(최신 수집 데이터 우선) 후 재저장
"""
from __future__ import annotations

import argparse
import os
import shutil
import sys
from datetime import datetime

import pandas as pd

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.abspath(os.path.join(SCRIPT_DIR, ".."))
if SCRIPT_DIR not in sys.path:
    sys.path.append(SCRIPT_DIR)

import full_crawling  # noqa: E402
import batch_ltv_update  # noqa: E402
import fill_zero  # noqa: E402

REGIONS = batch_ltv_update.REGIONS

# export_raw_csv.py와 동일한 순서/이름이지만, database.py를 import하지 않기 위해 여기서 별도 정의
RAW_COLUMNS_KO = [
    "사건번호", "용도", "시도", "시군구", "소재지", "감정가", "최저가",
    "결과", "낙찰가", "낙찰율", "매각일", "분기", "기간구분",
]


def log(msg):
    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {msg}")


def _to_float(value):
    """load_region_csvs_to_db.py::_to_float 와 동일 (DB 미접속, 순수 함수라 별도 복사)."""
    if pd.isna(value):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    s = str(value).strip().replace(",", "").replace("%", "")
    if not s:
        return None
    try:
        return float(s)
    except ValueError:
        return None


def crawl_and_merge(raw_csv_path: str, end_date: str) -> int:
    if not os.path.exists(raw_csv_path):
        raise SystemExit(
            f"raw CSV가 없습니다: {raw_csv_path}\n"
            "이 스크립트는 '기존 raw CSV 갱신'용입니다. 최초 파일은 export_raw_csv.py로 먼저 받아야 합니다."
        )

    existing_df = pd.read_csv(raw_csv_path)
    existing_df["매각일"] = pd.to_datetime(existing_df["매각일"], errors="coerce")
    if existing_df["매각일"].dropna().empty:
        raise SystemExit(f"raw CSV에 유효한 매각일 데이터가 없습니다: {raw_csv_path}")

    crawl_start = existing_df["매각일"].max().to_pydatetime()
    crawl_end = datetime.strptime(end_date, "%Y-%m-%d")
    if crawl_end < crawl_start:
        raise SystemExit(f"--end-date({crawl_end.date()})가 기존 데이터의 최신 매각일({crawl_start.date()})보다 이릅니다.")

    log(f"크롤링 구간: {crawl_start.date()}(기존 최신 매각일, 포함) ~ {crawl_end.date()} (전 지역)")

    tmp_csv = os.path.join(PROJECT_ROOT, "data", f"_tmp_update_{datetime.now().strftime('%Y%m%d%H%M%S')}.csv")

    cfg = full_crawling.CrawlConfig()
    driver = None
    profile_dir = None
    try:
        driver, wait, profile_dir = full_crawling.build_driver(cfg)
        full_crawling.login_and_go_to_total_search(driver, wait, cfg)

        ranges = full_crawling.generate_half_year_ranges_upto_today(
            start_year=crawl_start.year,
            end_year=crawl_end.year,
            start_month=crawl_start.month,
            start_day=crawl_start.day,
            today=crawl_end.date(),
        )
        log(f"검색 구간: {ranges}")

        for region in REGIONS:
            log(f"--- 지역: {region} ---")
            try:
                for start, end in ranges:
                    full_crawling.set_search_filters_and_search(driver, wait, cfg, region, start, end)
                    rows = full_crawling.crawl_current_result_pages(driver, wait, cfg)
                    log(f"  구간 {start}~{end}: {len(rows)}건")
                    if rows:
                        full_crawling.append_rows_to_csv(tmp_csv, rows)
                    full_crawling.jitter_sleep(cfg)
            except Exception as e:
                log(f"  지역 {region} 크롤링 실패(스킵): {e}")
                try:
                    full_crawling.ensure_info_main(driver, wait)
                except Exception:
                    pass
                continue
    finally:
        if driver is not None:
            try:
                full_crawling.clear_site_session(driver)
            except Exception:
                pass
            try:
                driver.quit()
                log("드라이버 종료")
            except Exception:
                pass
        if profile_dir:
            shutil.rmtree(profile_dir, ignore_errors=True)

    if not os.path.exists(tmp_csv):
        log("신규 수집 데이터 없음. 기존 raw CSV 그대로 유지합니다.")
        return len(existing_df)

    # 기존 파이프라인(automate_full_update.py)과 동일한 전처리
    batch_ltv_update.process_file(tmp_csv)
    fill_zero.fill_zero_multiple_csvs([tmp_csv])

    new_df = pd.read_csv(tmp_csv)
    new_df = new_df.drop(columns=["LTV_광주", "LTV_전북"], errors="ignore")

    for col in ["감정가", "최저가", "낙찰가", "낙찰율"]:
        new_df[col] = new_df[col].apply(_to_float)
    new_df["매각일"] = pd.to_datetime(new_df["매각일"], errors="coerce")
    new_df = new_df.dropna(subset=["매각일"])
    new_df = new_df[RAW_COLUMNS_KO]

    combined = pd.concat([existing_df, new_df], ignore_index=True)
    combined = combined.drop_duplicates(subset=["사건번호"], keep="last")
    combined = combined.sort_values(["매각일", "사건번호"])
    combined["매각일"] = combined["매각일"].dt.strftime("%Y-%m-%d")

    combined.to_csv(raw_csv_path, index=False, encoding="utf-8-sig")
    os.remove(tmp_csv)

    log(f"raw CSV 업데이트 완료 -> {raw_csv_path} (신규 수집 {len(new_df):,}건 반영, 총 {len(combined):,}건)")
    return len(combined)


def main():
    parser = argparse.ArgumentParser(
        description="DB 없이 크롤링만으로 기존 raw CSV를 병합/갱신 (DATABASE_URL 불필요)"
    )
    parser.add_argument("--raw-csv", required=True, help="갱신할 raw 데이터 CSV 경로")
    parser.add_argument(
        "--end-date",
        default=None,
        help="크롤링 종료일(YYYY-MM-DD). 미지정시 오늘 날짜. 시작일은 raw CSV에 있는 가장 최신 매각일로 자동 지정됨",
    )
    args = parser.parse_args()

    end_date = args.end_date or datetime.now().strftime("%Y-%m-%d")
    crawl_and_merge(args.raw_csv, end_date)


if __name__ == "__main__":
    main()
