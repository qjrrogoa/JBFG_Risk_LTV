import os
import re
import time
import random
import shutil
import tempfile
import traceback
from math import ceil
from dataclasses import dataclass
from datetime import date, timedelta
from typing import List, Tuple, Optional, Sequence

import pandas as pd
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.support.ui import WebDriverWait, Select
from selenium.webdriver.common.action_chains import ActionChains
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import (
    TimeoutException,
    WebDriverException,
    NoAlertPresentException,
    StaleElementReferenceException,
    UnexpectedAlertPresentException,
    ElementClickInterceptedException,
)


DateTuple = Tuple[int, int, int]
RangeTuple = Tuple[DateTuple, DateTuple]

CRAWL_COLUMNS = ["사건번호", "용도", "소재지", "감정가", "최저가", "결과", "낙찰가", "낙찰율", "매각일"]
FINAL_COLUMNS = ["사건번호", "용도", "시도", "시군구", "소재지", "감정가", "최저가", "결과", "낙찰가", "낙찰율", "매각일", "분기", "기간구분"]

TABLE_SELECTOR = "table.sub_table_wr.area_table_wr.table_list_wr.mulgun-list"
TBODY_SELECTOR = "tbody.mulgun-list"
ROWS_PER_PAGE_SELECTOR = "select[name='number'].rows-per-page"
PAGINATION_UL_SELECTOR = "ul.clearfix.pagenation"
TOTAL_COUNT_SELECTOR = "span.total-count"


@dataclass
class CrawlConfig:
    # Site login
    base_url: str = "https://www.infocare.co.kr/"
    userid: str = "광주은행"
    passwd: str = "1234"

    # Selenium behavior
    wait_sec: int = 25
    hold_browser: bool = False
    hold_browser_on_error: bool = True
    cleanup_profile_dir_on_exit: bool = True

    # Retry jitter
    min_delay: float = 0.3
    max_delay: float = 1.2

    # Crawl condition
    region: str = "광주"
    rows_per_page: str = "50"
    window_days: int = 60

    # Output
    recent_output_csv: str = "data/gwangju_recent_4w.csv"
    merged_output_csv: str = "data/gwangju.csv"
    save_recent_snapshot: bool = True

    # Merge behavior
    period_flag: int = 0
    merge_key_cols: Tuple[str, str, str] = ("사건번호", "매각일", "소재지")
    keep_update: str = "last"
    prepend_new_rows: bool = True


@dataclass
class MergeReport:
    original_rows: int
    update_rows: int
    update_rows_after_dedup: int
    duplicates_against_original: int
    new_rows_appended: int
    merged_rows: int


def log(msg: str) -> None:
    print(f"[LOG] {msg}")


def validate_config(cfg: CrawlConfig) -> None:
    errors: List[str] = []

    if not cfg.base_url.startswith("http"):
        errors.append("base_url must start with http/https")
    if not cfg.userid.strip():
        errors.append("userid is empty")
    if not cfg.passwd.strip():
        errors.append("passwd is empty")
    if cfg.wait_sec <= 0:
        errors.append("wait_sec must be > 0")
    if cfg.min_delay < 0 or cfg.max_delay < cfg.min_delay:
        errors.append("delay range is invalid")
    if not cfg.region.strip():
        errors.append("region is empty")
    if not cfg.rows_per_page.isdigit() or int(cfg.rows_per_page) <= 0:
        errors.append("rows_per_page must be numeric and > 0")
    if cfg.window_days < 0:
        errors.append("window_days must be >= 0")
    if cfg.keep_update not in {"first", "last"}:
        errors.append("keep_update must be one of: first, last")
    if not cfg.merge_key_cols:
        errors.append("merge_key_cols is empty")
    for c in cfg.merge_key_cols:
        if c not in FINAL_COLUMNS:
            errors.append(f"merge key column not found in schema: {c}")
    if cfg.recent_output_csv == cfg.merged_output_csv:
        errors.append("recent_output_csv and merged_output_csv must be different paths")

    if errors:
        raise ValueError("[Config validation failed]\n- " + "\n- ".join(errors))


def ensure_parent_dir(path: str) -> None:
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)


def jitter_sleep(cfg: CrawlConfig) -> None:
    time.sleep(random.uniform(cfg.min_delay, cfg.max_delay))


def handle_unexpected_alert(driver, accept: bool = True, timeout: int = 2) -> bool:
    try:
        WebDriverWait(driver, timeout).until(EC.alert_is_present())
        alert = driver.switch_to.alert
        text = ""
        try:
            text = alert.text
        except Exception:
            pass

        if accept:
            alert.accept()
            log(f"예상치 못한 alert 처리: accept (text='{text}')")
        else:
            alert.dismiss()
            log(f"예상치 못한 alert 처리: dismiss (text='{text}')")
        return True

    except (TimeoutException, NoAlertPresentException):
        return False
    except Exception as e:
        log(f"alert 처리 중 예외(무시 가능): {e}")
        return False


def safe_click(driver, wait, by, selector: str, desc: str, retries: int = 2):
    last_err = None
    for attempt in range(1, retries + 2):
        try:
            el = wait.until(EC.element_to_be_clickable((by, selector)))
            el.click()
            log(f"{desc} 클릭 완료")
            return el

        except UnexpectedAlertPresentException as e:
            last_err = e
            handle_unexpected_alert(driver, accept=True, timeout=3)
            log(f"{desc}: alert 때문에 재시도({attempt}/{retries + 1})")

        except (ElementClickInterceptedException, StaleElementReferenceException, WebDriverException) as e:
            last_err = e
            handle_unexpected_alert(driver, accept=True, timeout=1)
            log(f"{desc}: 클릭 예외로 재시도({attempt}/{retries + 1}) - {type(e).__name__}")

        except TimeoutException as e:
            last_err = e
            handle_unexpected_alert(driver, accept=True, timeout=1)
            log(f"{desc}: 대기 timeout 재시도({attempt}/{retries + 1})")

    raise last_err


def safe_select_by_value(driver, wait, by, selector: str, value: str, desc: str, retries: int = 2) -> None:
    last_err = None
    for attempt in range(1, retries + 2):
        try:
            el = wait.until(EC.presence_of_element_located((by, selector)))
            Select(el).select_by_value(value)
            log(f"{desc} 선택 완료: {value}")
            return

        except UnexpectedAlertPresentException as e:
            last_err = e
            handle_unexpected_alert(driver, accept=True, timeout=3)
            log(f"{desc}: alert 때문에 재시도({attempt}/{retries + 1})")

        except (StaleElementReferenceException, WebDriverException, TimeoutException) as e:
            last_err = e
            handle_unexpected_alert(driver, accept=True, timeout=1)
            log(f"{desc}: 선택 예외로 재시도({attempt}/{retries + 1}) - {type(e).__name__}")

    raise last_err


def switch_to_info_main_if_exists(driver, wait) -> bool:
    driver.switch_to.default_content()
    if driver.find_elements(By.NAME, "info_main"):
        wait.until(EC.frame_to_be_available_and_switch_to_it((By.NAME, "info_main")))
        return True
    return False


def ensure_info_main(driver, wait) -> bool:
    in_frame = switch_to_info_main_if_exists(driver, wait)
    if not in_frame:
        driver.switch_to.default_content()
    return in_frame


def clear_site_session(driver) -> None:
    try:
        driver.switch_to.default_content()
    except Exception:
        pass

    try:
        driver.delete_all_cookies()
        log("쿠키 삭제 완료")
    except Exception as e:
        log(f"쿠키 삭제 실패(무시 가능): {e}")

    try:
        driver.execute_script("window.localStorage.clear();")
        driver.execute_script("window.sessionStorage.clear();")
        log("localStorage / sessionStorage 삭제 완료")
    except Exception as e:
        log(f"스토리지 삭제 실패(무시 가능): {e}")

    try:
        driver.execute_cdp_cmd("Network.clearBrowserCache", {})
        log("브라우저 캐시 삭제 완료(CDP)")
    except Exception as e:
        log(f"캐시 삭제 실패(무시 가능): {e}")


def date_to_tuple(d: date) -> DateTuple:
    return (d.year, d.month, d.day)


def get_today_window_range(window_days: int, today: Optional[date] = None) -> RangeTuple:
    if today is None:
        today = date.today()
    start_d = today - timedelta(days=window_days)
    end_d = today + timedelta(days=window_days)
    return (date_to_tuple(start_d), date_to_tuple(end_d))


def build_driver(cfg: CrawlConfig):
    profile_dir = tempfile.mkdtemp(prefix="selenium-infocare-")

    options = Options()
    options.add_argument("--start-maximized")
    options.add_argument("--disable-gpu")
    options.add_argument(f"--user-data-dir={profile_dir}")

    driver = webdriver.Chrome(options=options)

    # 실행 직후 캐시를 비워서 이전 세션 잔여 영향을 줄임
    try:
        driver.execute_cdp_cmd("Network.enable", {})
        driver.execute_cdp_cmd("Network.clearBrowserCache", {})
        log("브라우저 캐시 초기화 완료")
    except Exception as e:
        log(f"브라우저 캐시 초기화 실패(무시): {e}")

    wait = WebDriverWait(driver, cfg.wait_sec)
    return driver, wait, profile_dir


def login_and_go_to_total_search(driver, wait, cfg: CrawlConfig) -> None:
    driver.get(cfg.base_url)
    log("메인 접속 완료")

    ensure_info_main(driver, wait)
    log("info_main 프레임 진입 완료(초기)")

    try:
        safe_click(driver, wait, By.CSS_SELECTOR, "ul.hd_login li.login a", "로그인 메뉴")
    except Exception:
        log("로그인 메뉴 없음/이미 로그인 화면일 수 있어 스킵")

    wait.until(EC.visibility_of_element_located((By.CSS_SELECTOR, "div.pop-up-background.login-pane")))
    userid = wait.until(
        EC.visibility_of_element_located(
            (By.CSS_SELECTOR, "div.pop-up-background.login-pane form.login input.userid")
        )
    )
    passwd = wait.until(
        EC.visibility_of_element_located(
            (By.CSS_SELECTOR, "div.pop-up-background.login-pane form.login input.passwd")
        )
    )

    userid.clear()
    userid.send_keys(cfg.userid)
    passwd.clear()
    passwd.send_keys(cfg.passwd)
    log("ID/PW 입력 완료")

    login_btn = wait.until(
        EC.element_to_be_clickable(
            (
                By.XPATH,
                "//div[contains(@class,'login-pane') and contains(@class,'pop-up-background')]//button[normalize-space()='로그인']",
            )
        )
    )
    ActionChains(driver).move_to_element(login_btn).pause(0.1).click(login_btn).perform()
    log("로그인 버튼 클릭 완료")

    handle_unexpected_alert(driver, accept=True, timeout=2)

    ensure_info_main(driver, wait)
    log("로그인 후 info_main 프레임 진입 완료")

    safe_click(driver, wait, By.CSS_SELECTOR, "li.main_nav_li02 > a", "법원경매")
    handle_unexpected_alert(driver, accept=True, timeout=2)

    safe_click(driver, wait, By.CSS_SELECTOR, "a[href='/bubwon/search/search_total.asp']", "통합검색")

    driver.switch_to.default_content()
    has_frame = bool(driver.find_elements(By.NAME, "info_main"))
    log(f"통합검색 클릭 후 info_main 프레임 존재 여부: {has_frame}")

    ensure_info_main(driver, wait)
    log("통합검색 진입 완료")


def wait_for_results_table(driver, wait) -> None:
    ensure_info_main(driver, wait)
    wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, TABLE_SELECTOR)))
    wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, TBODY_SELECTOR)))


def set_rows_per_page(driver, wait, cfg: CrawlConfig) -> None:
    ensure_info_main(driver, wait)

    el = wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, ROWS_PER_PAGE_SELECTOR)))

    try:
        driver.execute_script("arguments[0].scrollIntoView({block:'center', inline:'nearest'});", el)
        time.sleep(0.2)
    except Exception:
        pass

    try:
        Select(el).select_by_value(cfg.rows_per_page)
        log(f"페이지당 건수(rows-per-page) 선택 완료: {cfg.rows_per_page}")
    except Exception as e:
        log(f"Select로 rows-per-page 변경 실패 -> JS로 재시도: {type(e).__name__}")
        driver.execute_script(
            """
            const sel = arguments[0];
            const val = arguments[1];
            sel.value = val;
            sel.dispatchEvent(new Event('change', {bubbles:true}));
            sel.dispatchEvent(new Event('input', {bubbles:true}));
            """,
            el,
            cfg.rows_per_page,
        )
        log(f"JS로 rows-per-page 변경 완료: {cfg.rows_per_page}")

    handle_unexpected_alert(driver, accept=True, timeout=1)
    wait_for_results_table(driver, wait)
    jitter_sleep(cfg)


def get_total_count(driver, wait) -> int:
    ensure_info_main(driver, wait)
    el = wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, TOTAL_COUNT_SELECTOR)))
    txt = (el.text or "").strip().replace(",", "")
    return int(txt) if txt.isdigit() else 0


def get_current_on_page(driver, wait) -> int:
    ensure_info_main(driver, wait)
    ul = driver.find_element(By.CSS_SELECTOR, PAGINATION_UL_SELECTOR)
    on = ul.find_element(By.CSS_SELECTOR, "li.on")
    t = (on.text or "").strip()
    return int(t) if t.isdigit() else 1


def get_visible_page_numbers(driver, wait) -> List[int]:
    ensure_info_main(driver, wait)
    ul = driver.find_element(By.CSS_SELECTOR, PAGINATION_UL_SELECTOR)
    lis = ul.find_elements(By.CSS_SELECTOR, "li[data-page]")
    out: List[int] = []
    for li in lis:
        dp = li.get_attribute("data-page")
        if dp and dp.isdigit():
            out.append(int(dp))
    return sorted(set(out))


def goto_page(driver, wait, target_page: int, cfg: CrawlConfig) -> None:
    current = get_current_on_page(driver, wait)
    if current == target_page:
        return

    while True:
        visible = get_visible_page_numbers(driver, wait)
        vmin, vmax = (min(visible), max(visible)) if visible else (target_page, target_page)

        if vmin <= target_page <= vmax:
            ensure_info_main(driver, wait)
            tbody = driver.find_element(By.CSS_SELECTOR, TBODY_SELECTOR)
            trs = tbody.find_elements(By.CSS_SELECTOR, "tr")
            first_tr = trs[0] if trs else None

            safe_click(
                driver,
                wait,
                By.CSS_SELECTOR,
                f"{PAGINATION_UL_SELECTOR} li[data-page='{target_page}']",
                f"페이지 이동({target_page})",
            )
            handle_unexpected_alert(driver, accept=True, timeout=1)

            try:
                if first_tr is not None:
                    WebDriverWait(driver, 10).until(EC.staleness_of(first_tr))
                else:
                    WebDriverWait(driver, 10).until(
                        lambda d: get_current_on_page(driver, wait) == target_page
                    )
            except Exception:
                WebDriverWait(driver, 10).until(
                    lambda d: get_current_on_page(driver, wait) == target_page
                )

            wait_for_results_table(driver, wait)
            jitter_sleep(cfg)
            return

        if target_page > vmax:
            safe_click(
                driver,
                wait,
                By.CSS_SELECTOR,
                f"{PAGINATION_UL_SELECTOR} li.nextpg",
                "페이지 그룹 다음(nextpg)",
            )
            handle_unexpected_alert(driver, accept=True, timeout=1)
            wait_for_results_table(driver, wait)
            jitter_sleep(cfg)
            continue

        if target_page < vmin:
            safe_click(
                driver,
                wait,
                By.CSS_SELECTOR,
                f"{PAGINATION_UL_SELECTOR} li.prevpg",
                "페이지 그룹 이전(prevpg)",
            )
            handle_unexpected_alert(driver, accept=True, timeout=1)
            wait_for_results_table(driver, wait)
            jitter_sleep(cfg)
            continue


def normalize_address(s: str) -> str:
    s = (s or "").strip()
    s = re.sub(r"\s*-\s*", "-", s)
    s = re.sub(r"\s+", " ", s)
    return s


def parse_bid_info_from_title(title_text: str) -> Tuple[str, str]:
    if not title_text:
        return "0", "0%"

    m_price = re.search(r"낙찰가:\s*([0-9,]+)원", title_text)
    bid_price = m_price.group(1) if m_price else "0"

    m_rate = re.search(r"\(\s*([0-9.]+%)\s*\)", title_text)
    bid_rate = m_rate.group(1) if m_rate else "0%"

    return bid_price, bid_rate


def parse_one_row(tr) -> Optional[dict]:
    tds = tr.find_elements(By.CSS_SELECTOR, "td")
    if len(tds) < 7:
        return None

    case_text = tds[1].text.strip()
    case_lines = [x.strip() for x in case_text.split("\n") if x.strip()]
    if len(case_lines) >= 2:
        case_no = f"{case_lines[0]} {case_lines[1]}"
    elif len(case_lines) == 1:
        case_no = case_lines[0]
    else:
        case_no = ""

    usage = tds[2].text.strip()

    address = ""
    try:
        divs = tds[3].find_elements(By.CSS_SELECTOR, "div")
        if divs:
            address = divs[-1].text.strip()
    except Exception:
        address = tds[3].text.strip()
    address = normalize_address(address)

    appraised_price, min_price = "0", "0"
    try:
        lis = tds[4].find_elements(By.CSS_SELECTOR, "ul li")
        if len(lis) >= 2:
            appraised_price = lis[0].text.strip()
            min_price = lis[1].text.strip()
    except Exception:
        pass

    result = tds[5].text.strip()
    sale_date = tds[6].text.strip()

    title_text = tr.get_attribute("title") or ""
    bid_price, bid_rate = parse_bid_info_from_title(title_text)

    if ("낙찰" not in result) or (not title_text.strip()):
        bid_price, bid_rate = "0", "0%"

    return {
        "사건번호": case_no,
        "용도": usage,
        "소재지": address,
        "감정가": appraised_price,
        "최저가": min_price,
        "결과": result,
        "낙찰가": bid_price,
        "낙찰율": bid_rate,
        "매각일": sale_date,
    }


def collect_current_page_rows(driver, wait) -> List[dict]:
    ensure_info_main(driver, wait)
    tbody = driver.find_element(By.CSS_SELECTOR, TBODY_SELECTOR)
    trs = tbody.find_elements(By.CSS_SELECTOR, "tr")
    out: List[dict] = []
    for tr in trs:
        item = parse_one_row(tr)
        if item:
            out.append(item)
    return out


def crawl_current_result_pages(driver, wait, cfg: CrawlConfig) -> List[dict]:
    wait_for_results_table(driver, wait)

    total_count = get_total_count(driver, wait)
    if total_count <= 0:
        log("total-count=0 -> 결과 없음")
        return []

    set_rows_per_page(driver, wait, cfg)

    per_page = int(cfg.rows_per_page)
    total_pages = ceil(total_count / per_page) if total_count > 0 else 1
    log(f"총 건수(total-count)={total_count}, 페이지당={per_page}, 총 페이지={total_pages}")

    all_rows: List[dict] = []
    for p in range(1, total_pages + 1):
        if p > 1:
            goto_page(driver, wait, p, cfg)

        page_no = get_current_on_page(driver, wait)
        rows = collect_current_page_rows(driver, wait)
        all_rows.extend(rows)
        log(f"페이지 {page_no}/{total_pages} 수집: {len(rows)}건, 누적={len(all_rows)}")

        jitter_sleep(cfg)

    return all_rows


def set_search_filters_and_search(
    driver,
    wait,
    cfg: CrawlConfig,
    region: str,
    start: DateTuple,
    end: DateTuple,
) -> None:
    ensure_info_main(driver, wait)

    sy, sm, sd = start
    ey, em, ed = end

    safe_select_by_value(driver, wait, By.CSS_SELECTOR, "select[name='addr_do']", region, "지역(addr_do)")
    safe_select_by_value(driver, wait, By.CSS_SELECTOR, "select[name='startyear']", f"{sy}", "시작년도(startyear)")
    safe_select_by_value(driver, wait, By.CSS_SELECTOR, "select[name='startmonth']", f"{sm:02d}", "시작월(startmonth)")
    safe_select_by_value(driver, wait, By.CSS_SELECTOR, "select[name='startdate']", f"{sd:02d}", "시작일(startdate)")

    safe_select_by_value(driver, wait, By.CSS_SELECTOR, "select[name='endyear']", f"{ey}", "종료년도(endyear)")
    safe_select_by_value(driver, wait, By.CSS_SELECTOR, "select[name='endmonth']", f"{em:02d}", "종료월(endmonth)")
    safe_select_by_value(driver, wait, By.CSS_SELECTOR, "select[name='enddate']", f"{ed:02d}", "종료일(enddate)")

    safe_click(driver, wait, By.CSS_SELECTOR, "div.cont_btn_wr.button-pane a.search", "검색하기")
    handle_unexpected_alert(driver, accept=True, timeout=2)

    wait_for_results_table(driver, wait)
    jitter_sleep(cfg)


def _normalize_result_value(x) -> str:
    if pd.isna(x):
        s = ""
    else:
        s = str(x).strip()

    if s == "(1/1)" or s == "":
        return "취하"

    s = s.split("(", 1)[0].strip()
    return s if s else "취하"


def _normalize_sale_date_text(x) -> str:
    if pd.isna(x):
        return ""
    s = str(x).strip()
    if not s:
        return ""

    m = re.search(r"(\d{4})[.-](\d{1,2})[.-](\d{1,2})", s)
    if not m:
        return s

    y, mo, d = m.group(1), m.group(2).zfill(2), m.group(3).zfill(2)
    return f"{y}-{mo}-{d}"


def _align_columns(df: pd.DataFrame, columns: Sequence[str]) -> pd.DataFrame:
    out = df.copy()
    for c in columns:
        if c not in out.columns:
            out[c] = ""
    return out[list(columns)]


def prepare_update_dataframe(rows: List[dict], cfg: CrawlConfig) -> pd.DataFrame:
    df = pd.DataFrame(rows, columns=CRAWL_COLUMNS)
    if df.empty:
        return pd.DataFrame(columns=FINAL_COLUMNS)

    df = _align_columns(df, CRAWL_COLUMNS)

    df["결과"] = df["결과"].apply(_normalize_result_value)
    df["매각일"] = df["매각일"].apply(_normalize_sale_date_text)

    dt = pd.to_datetime(df["매각일"], errors="coerce")
    valid = dt.notna()
    df.loc[valid, "매각일"] = dt.loc[valid].dt.strftime("%Y-%m-%d")

    df["분기"] = ""
    df.loc[valid, "분기"] = (
        dt.loc[valid].dt.year.astype(str) + "_" + dt.loc[valid].dt.quarter.astype(str) + "Q"
    )

    addr_parts = df["소재지"].astype("string").fillna("").str.split()
    df["시도"] = addr_parts.str[0].fillna("")
    df["시군구"] = addr_parts.str[1].fillna("")
    df["기간구분"] = cfg.period_flag

    df = _align_columns(df, FINAL_COLUMNS)
    return df


def _normalize_key_columns(
    df: pd.DataFrame,
    key_cols: Sequence[str],
    strip: bool = True,
    collapse_spaces: bool = True,
) -> pd.DataFrame:
    out = df.copy()
    for c in key_cols:
        if c not in out.columns:
            raise KeyError(f"key column not found: {c}")

        s = out[c].astype("string").fillna("")
        if strip:
            s = s.str.strip()
        if collapse_spaces:
            s = s.str.replace(r"\s+", " ", regex=True)
        out[c] = s
    return out


def read_csv_or_empty(path: str, columns: Sequence[str]) -> pd.DataFrame:
    if not os.path.exists(path):
        log(f"기존 파일이 없어 신규 생성합니다: {path}")
        return pd.DataFrame(columns=list(columns))

    df = pd.read_csv(path)
    return _align_columns(df, columns)


def merge_append_new_only(
    original_df: pd.DataFrame,
    update_df: pd.DataFrame,
    key_cols: Sequence[str],
    *,
    keep_update: str = "last",
    prepend: bool = True,
) -> Tuple[pd.DataFrame, MergeReport]:
    o = _align_columns(original_df, FINAL_COLUMNS)
    u = _align_columns(update_df, FINAL_COLUMNS)

    o = _normalize_key_columns(o, key_cols)
    u = _normalize_key_columns(u, key_cols)

    u_dedup = u.drop_duplicates(subset=list(key_cols), keep=keep_update)

    orig_keys = set(map(tuple, o[list(key_cols)].to_numpy()))
    u_keys = list(map(tuple, u_dedup[list(key_cols)].to_numpy()))
    mask_new = [k not in orig_keys for k in u_keys]
    new_rows = u_dedup.loc[mask_new].copy()

    if prepend:
        merged = pd.concat([new_rows, o], ignore_index=True)
    else:
        merged = pd.concat([o, new_rows], ignore_index=True)

    merged = _align_columns(merged, FINAL_COLUMNS)
    merged["기간구분"] = pd.to_numeric(merged["기간구분"], errors="coerce").fillna(0).astype(int)

    report = MergeReport(
        original_rows=len(original_df),
        update_rows=len(update_df),
        update_rows_after_dedup=len(u_dedup),
        duplicates_against_original=len(u_dedup) - len(new_rows),
        new_rows_appended=len(new_rows),
        merged_rows=len(merged),
    )
    return merged, report


def write_dataframe_csv(path: str, df: pd.DataFrame) -> None:
    ensure_parent_dir(path)
    df.to_csv(path, index=False, encoding="utf-8-sig")


def main() -> None:
    cfg = CrawlConfig()
    validate_config(cfg)

    driver = None
    profile_dir = None

    try:
        driver, wait, profile_dir = build_driver(cfg)

        login_and_go_to_total_search(driver, wait, cfg)

        today = date.today()
        start, end = get_today_window_range(cfg.window_days, today=today)
        log(f"UPDATE MODE: 오늘({today}) 기준 ±{cfg.window_days}일")
        log(f"검색 구간: {start} ~ {end}")

        set_search_filters_and_search(driver, wait, cfg, cfg.region, start, end)
        rows = crawl_current_result_pages(driver, wait, cfg)
        log(f"크롤링 완료 row={len(rows)}")

        update_df = prepare_update_dataframe(rows, cfg)

        if cfg.save_recent_snapshot:
            recent_snapshot = update_df.drop(columns=["기간구분"], errors="ignore")
            write_dataframe_csv(cfg.recent_output_csv, recent_snapshot)
            log(f"최근 스냅샷 저장(write): {cfg.recent_output_csv} / rows={len(recent_snapshot)}")

        original_df = read_csv_or_empty(cfg.merged_output_csv, FINAL_COLUMNS)

        merged_df, report = merge_append_new_only(
            original_df,
            update_df,
            list(cfg.merge_key_cols),
            keep_update=cfg.keep_update,
            prepend=cfg.prepend_new_rows,
        )

        write_dataframe_csv(cfg.merged_output_csv, merged_df)
        log(f"최종 파일 저장 완료: {cfg.merged_output_csv}")
        log(
            "MERGE REPORT: "
            f"original={report.original_rows}, "
            f"update={report.update_rows}, "
            f"update_dedup={report.update_rows_after_dedup}, "
            f"duplicates_against_original={report.duplicates_against_original}, "
            f"appended={report.new_rows_appended}, "
            f"merged={report.merged_rows}"
        )

        if cfg.hold_browser:
            log("브라우저 유지 중. 엔터 입력 시 종료합니다.")
            input()

    except Exception as e:
        log(f"에러 발생: {type(e).__name__}: {e}")
        traceback.print_exc()

        if driver is not None and cfg.hold_browser_on_error:
            handle_unexpected_alert(driver, accept=True, timeout=2)
            log("에러 상태로 브라우저 유지 중. 엔터 입력 시 종료합니다.")
            input()

        raise

    finally:
        if driver is not None:
            try:
                clear_site_session(driver)
            except Exception:
                pass

            try:
                driver.quit()
                log("드라이버 종료")
            except Exception:
                pass

        if cfg.cleanup_profile_dir_on_exit and profile_dir:
            try:
                shutil.rmtree(profile_dir, ignore_errors=True)
                log(f"Selenium 프로필 폴더 삭제 완료: {profile_dir}")
            except Exception as e:
                log(f"프로필 폴더 삭제 실패(무시 가능): {e}")


if __name__ == "__main__":
    main()
