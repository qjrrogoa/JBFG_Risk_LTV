"""
지정한 종료일 기준 최근 N일(기본 50일) 구간만 크롤링 → 전처리 → 빈 낙찰가 보정
→ DB 적재 → 시그널 캐시 계산 → AI 권고안 생성까지 한 번에 처리하는 파이프라인.

automate_full_update.py와 동일한 단계를 그대로 거치되, 크롤링 시작일이 코드에
고정되어 있지 않고 --end-date / --days로 매번 지정할 수 있습니다.

사용 예:
    python3 scripts/automate_range_update.py --end-date 20260801
    python3 scripts/automate_range_update.py --end-date 2026-08-01 --days 30
    python3 scripts/automate_range_update.py --end-date 20260801 --regions 서울,경기
    python3 scripts/automate_range_update.py --end-date 20260801 --skip-ai
"""

import os
import sys
import time
import argparse
from datetime import datetime, timedelta, date

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.append(PROJECT_ROOT)
sys.path.append(os.path.join(PROJECT_ROOT, "backend"))

from backend import services
from scripts import full_crawling, load_region_csvs_to_db, batch_ltv_update, fill_zero

REGIONS = ["서울", "인천", "경기", "부산", "대구", "대전", "전남광주", "울산", "전북", "경북", "경남", "제주", "충남", "충북", "강원", "세종"]


def log(msg):
    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {msg}")


def run_step(name, func):
    log(f">>> STEP START: {name}")
    start_time = time.time()
    try:
        func()
        elapsed = time.time() - start_time
        log(f"<<< STEP COMPLETED: {name} ({elapsed:.1f}s)")
    except Exception as e:
        log(f"!!! STEP FAILED: {name} - {e}")
        raise


def parse_end_date(raw: str) -> date:
    raw = raw.strip()
    for fmt in ("%Y%m%d", "%Y-%m-%d", "%Y/%m/%d"):
        try:
            return datetime.strptime(raw, fmt).date()
        except ValueError:
            continue
    raise ValueError(f"--end-date 형식을 인식할 수 없습니다: {raw} (예: 20260801 또는 2026-08-01)")


def step_crawling(start_date: date, end_date: date, regions):
    """1. 브라우저 1회 실행 → 지정 구간(start_date~end_date)만 전 지역 순회 크롤링"""
    import shutil

    regions_to_crawl = []
    for r in regions:
        new_csv = f"data/{r}_new.csv"
        if os.path.exists(new_csv) and os.path.getsize(new_csv) > 500:
            log(f"Skipping region: {r} (Already crawled)")
            continue
        regions_to_crawl.append(r)

    if not regions_to_crawl:
        log("All regions already crawled. Nothing to do.")
        return

    log(f"Regions to crawl: {regions_to_crawl}")
    log(f"Date range: {start_date} ~ {end_date}")

    # today=end_date로 넘기면 "오늘" 대신 지정한 종료일을 기준으로 구간이 계산됨
    ranges = full_crawling.generate_half_year_ranges_upto_today(
        start_year=start_date.year,
        end_year=end_date.year,
        start_month=start_date.month,
        start_day=start_date.day,
        today=end_date,
    )
    log(f"Date ranges: {ranges}")

    cfg = full_crawling.CrawlConfig()
    driver = None
    profile_dir = None

    try:
        driver, wait, profile_dir = full_crawling.build_driver(cfg)
        full_crawling.login_and_go_to_total_search(driver, wait, cfg)
        log("✅ Browser ready. Starting multi-region crawl...")

        for r in regions_to_crawl:
            new_csv = f"data/{r}_new.csv"
            log(f"--- Region: {r} ---")

            try:
                for start, end in ranges:
                    log(f"  Searching: {start} ~ {end}")
                    full_crawling.set_search_filters_and_search(driver, wait, cfg, r, start, end)
                    rows = full_crawling.crawl_current_result_pages(driver, wait, cfg)
                    log(f"  Collected: {len(rows)} rows")

                    if rows:
                        full_crawling.append_rows_to_csv(new_csv, rows)

                    full_crawling.jitter_sleep(cfg)

                log(f"✅ Region {r} completed.")
            except Exception as e:
                log(f"❌ Error in region {r}: {e} (Skipping)")
                try:
                    full_crawling.ensure_info_main(driver, wait)
                except Exception:
                    pass
                time.sleep(3)
                continue

    except Exception as e:
        log(f"❗ Fatal crawling error: {e}")
        import traceback
        traceback.print_exc()
    finally:
        if driver is not None:
            try:
                full_crawling.clear_site_session(driver)
            except Exception:
                pass
            try:
                driver.quit()
                log("Driver closed.")
            except Exception:
                pass
        if profile_dir:
            try:
                shutil.rmtree(profile_dir, ignore_errors=True)
            except Exception:
                pass


def step_preprocessing(regions):
    """2. 수집된 신규 데이터 전처리 (용도 매핑 및 파생 컬럼 계산)"""
    for r in regions:
        new_csv = f"data/{r}_new.csv"
        if os.path.exists(new_csv):
            batch_ltv_update.process_file(new_csv)


def step_fill_zero(regions):
    """2.5. 신규 수집 데이터 중 빈 낙찰가 보정"""
    csv_paths = [f"data/{r}_new.csv" for r in regions if os.path.exists(f"data/{r}_new.csv")]
    if csv_paths:
        log("Starting bulk fill_zero for all regions...")
        fill_zero.fill_zero_multiple_csvs(csv_paths)


def step_load_db():
    """3. DB 업서트 (신규 데이터만 반영)"""
    load_region_csvs_to_db.load_all_region_data(clear_existing=False, file_pattern="*_new.csv")


def step_build_signal_cache(base_date: str):
    """4. 시그널 캐시 구축 (지정한 종료일 기준월로 계산)"""
    banks = ["광주은행", "전북은행"]
    for bank in banks:
        log(f"Building signal cache for {bank} (base_date={base_date})...")
        services.get_signal_cache_rows(bank, base_date)


def step_ai_analysis(base_date: str):
    """5. AI 권고안 일괄 생성 (Red/Yellow 시그널 대상, 지정한 종료일 기준월)"""
    banks = ["광주은행", "전북은행"]
    for bank in banks:
        log(f"Starting AI Analysis for {bank}...")
        rows, _ = services.get_signal_cache_rows(bank, base_date)
        services.fetch_all_advice(rows, bank, base_date=base_date, async_only=False)


def step_cleanup(regions):
    """6. 사용한 임시 파일(_new.csv) 정리"""
    for r in regions:
        new_csv = f"data/{r}_new.csv"
        if os.path.exists(new_csv):
            os.remove(new_csv)


def main():
    parser = argparse.ArgumentParser(
        description="지정한 종료일 기준 최근 N일 구간을 전 지역 크롤링→전처리→DB적재→시그널/AI 계산까지 한 번에 처리"
    )
    parser.add_argument("--end-date", required=True, help="기준 종료일 (예: 20260801 또는 2026-08-01)")
    parser.add_argument("--days", type=int, default=50, help="종료일 기준 며칠 전부터 크롤링할지 (기본 50일)")
    parser.add_argument("--regions", type=str, default=None, help="쉼표로 구분한 지역 목록 (미지정 시 전 지역)")
    parser.add_argument("--skip-ai", action="store_true", help="AI 권고안 생성 단계 생략 (시간/비용 절약)")
    args = parser.parse_args()

    end_date = parse_end_date(args.end_date)
    start_date = end_date - timedelta(days=args.days)
    base_date_str = end_date.strftime("%Y-%m-%d")
    regions = [r.strip() for r in args.regions.split(",") if r.strip()] if args.regions else REGIONS

    log("=== LTV 구간 업데이트 파이프라인 시작 ===")
    log(f"대상 지역: {regions}")
    log(f"크롤링 구간: {start_date} ~ {end_date} ({args.days}일)")
    log(f"시그널/AI 기준월: {base_date_str}")

    try:
        run_step("CRAWLING", lambda: step_crawling(start_date, end_date, regions))
        run_step("PREPROCESSING", lambda: step_preprocessing(regions))
        run_step("FILL_ZERO", lambda: step_fill_zero(regions))
        run_step("LOAD_DB", step_load_db)
        run_step("SIGNAL_CACHE_BUILD", lambda: step_build_signal_cache(base_date_str))

        if args.skip_ai:
            log("--skip-ai 지정됨: AI 권고안 생성 단계 생략")
        else:
            run_step("AI_ADVICE_GENERATION", lambda: step_ai_analysis(base_date_str))

        run_step("CLEANUP", lambda: step_cleanup(regions))

        log("=== 모든 업데이트 공정이 성공적으로 완료되었습니다! ===")

    except Exception as e:
        log(f"업데이트 중단됨: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
