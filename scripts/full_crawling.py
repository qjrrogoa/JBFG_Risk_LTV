import os
import re
import csv
import time
import random
import shutil
import tempfile
import traceback
from math import ceil
from datetime import date
from dataclasses import dataclass
from typing import List, Tuple, Optional

from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.action_chains import ActionChains
from selenium.webdriver.support.ui import WebDriverWait, Select
from selenium.webdriver.support import expected_conditions as EC

from selenium.common.exceptions import (
    TimeoutException,
    WebDriverException,
    NoAlertPresentException,
    StaleElementReferenceException,
    UnexpectedAlertPresentException,
    ElementClickInterceptedException,
)

# =========================
# Types
# =========================
DateTuple = Tuple[int, int, int]
RangeTuple = Tuple[DateTuple, DateTuple]

# =========================
# Config
# =========================
@dataclass
class CrawlConfig:
    base_url: str = "https://www.infocare.co.kr/"
    userid: str = "광주은행"
    passwd: str = "1234"

    wait_sec: int = 25
    hold_browser: bool = False           # 전체 크롤링이면 보통 False 권장
    hold_browser_on_error: bool = False
    cleanup_profile_dir_on_exit: bool = True

    min_delay: float = 0.2
    max_delay: float = 0.6

    region: str = "강원"
    rows_per_page: str = "50"          

    output_csv: str = "data/강원.csv"

    start_year: int = 2008
    start_half: int = 1    # 사용 안 함 (아래 start_month/day로 대체 가능)
    start_month: int = 1   # 시작 월
    start_day: int = 1     # 시작 일
    end_year: int = 2026


# =========================
# Logging / timing
# =========================
def log(msg: str):
    print(f"[LOG] {msg}")

def jitter_sleep(cfg: CrawlConfig):
    time.sleep(random.uniform(cfg.min_delay, cfg.max_delay))


# =========================
# Robust helpers
# =========================
def handle_unexpected_alert(driver, accept=True, timeout=2) -> bool:
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


def safe_click(driver, wait, by, selector, desc, retries=2):
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
            log(f"{desc}: alert 때문에 재시도({attempt}/{retries+1})")

        except ElementClickInterceptedException as e:
            last_err = e
            # 이미지 등 다른 요소가 겹쳐 클릭을 가로채는 경우 → JS click으로 즉시 폴백
            try:
                el = driver.find_element(by, selector)
                driver.execute_script("arguments[0].scrollIntoView({block:'center'});", el)
                time.sleep(0.15)
                driver.execute_script("arguments[0].click();", el)
                log(f"{desc} JS click 완료 (ElementClickInterceptedException 폴백)")
                return el
            except Exception as js_err:
                log(f"{desc}: JS click 폴백도 실패({attempt}/{retries+1}) - {js_err}")

        except (StaleElementReferenceException, WebDriverException) as e:
            last_err = e
            handle_unexpected_alert(driver, accept=True, timeout=1)
            log(f"{desc}: 클릭 예외로 재시도({attempt}/{retries+1}) - {type(e).__name__}")

        except TimeoutException as e:
            last_err = e
            handle_unexpected_alert(driver, accept=True, timeout=1)
            log(f"{desc}: 대기 timeout 재시도({attempt}/{retries+1})")

    raise last_err


def safe_select_by_value(driver, wait, by, selector, value, desc, retries=2):
    """
    일반 select 유틸(다른 곳에서 사용)
    - rows-per-page는 별도 함수(set_rows_per_page)에서 더 강하게 처리함
    """
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
            log(f"{desc}: alert 때문에 재시도({attempt}/{retries+1})")

        except (StaleElementReferenceException, WebDriverException, TimeoutException) as e:
            last_err = e
            handle_unexpected_alert(driver, accept=True, timeout=1)
            log(f"{desc}: 선택 예외로 재시도({attempt}/{retries+1}) - {type(e).__name__}")

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


def clear_site_session(driver):
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


# =========================
# Date ranges
# =========================
def generate_half_year_ranges_upto_today(
    start_year: int,
    end_year: int,
    start_month: int = 1,
    start_day: int = 1,
    today: Optional[date] = None,
) -> List[RangeTuple]:
    if today is None:
        today = date.today()

    max_year = min(end_year, today.year)
    out: List[RangeTuple] = []

    for y in range(start_year, max_year + 1):
        # 해당 연도가 시작 연도이면 사용자가 지정한 월/일 사용, 아니면 1월 1일부터
        sy, sm, sd = (y, start_month, start_day) if y == start_year else (y, 1, 1)
        
        # 반기 단위로 쪼개기 (기존 로직 유지하되 시작일만 보정)
        if y < today.year:
            # 상반기 (기점일이 6월 30일 이전인 경우만)
            if (sm, sd) <= (6, 30):
                out.append(((sy, sm, sd), (y, 6, 30)))
                out.append(((y, 7, 1), (y, 12, 31)))
            else:
                out.append(((sy, sm, sd), (y, 12, 31)))
        else:
            # 올해 데이터
            ty, tm, td = today.year, today.month, today.day
            if (tm, td) <= (6, 30):
                out.append(((sy, sm, sd), (ty, tm, td)))
            else:
                if (sm, sd) <= (6, 30):
                    out.append(((sy, sm, sd), (y, 6, 30)))
                    out.append(((y, 7, 1), (ty, tm, td)))
                else:
                    out.append(((sy, sm, sd), (ty, tm, td)))

    return out


# =========================
# Driver / navigation
# =========================
def build_driver(cfg: CrawlConfig):
    profile_dir = tempfile.mkdtemp(prefix="selenium-infocare-")

    options = Options()
    options.add_argument("--start-maximized")
    options.add_argument("--disable-gpu")
    options.add_argument(f"--user-data-dir={profile_dir}")

    driver = webdriver.Chrome(options=options)
    try:
        # Chrome 실행 직후 캐시를 1회 삭제
        driver.execute_cdp_cmd("Network.enable", {})
        driver.execute_cdp_cmd("Network.clearBrowserCache", {})
        log("브라우저 캐시 초기화 완료")
    except Exception as e:
        log(f"브라우저 캐시 초기화 실패(무시): {e}")
    wait = WebDriverWait(driver, cfg.wait_sec)
    return driver, wait, profile_dir


def login_and_go_to_total_search(driver, wait, cfg: CrawlConfig):
    driver.get(cfg.base_url)
    log("메인 접속 완료")

    ensure_info_main(driver, wait)
    log("info_main 프레임 진입 완료(초기)")

    try:
        safe_click(driver, wait, By.CSS_SELECTOR, "ul.hd_login li.login a", "로그인 메뉴")
    except Exception:
        log("로그인 메뉴 없음/이미 로그인 화면일 수 있어 스킵")

    wait.until(EC.visibility_of_element_located((By.CSS_SELECTOR, "div.pop-up-background.login-pane")))
    userid = wait.until(EC.visibility_of_element_located(
        (By.CSS_SELECTOR, "div.pop-up-background.login-pane form.login input.userid")
    ))
    passwd = wait.until(EC.visibility_of_element_located(
        (By.CSS_SELECTOR, "div.pop-up-background.login-pane form.login input.passwd")
    ))

    userid.clear()
    userid.send_keys(cfg.userid)
    passwd.clear()
    passwd.send_keys(cfg.passwd)
    log("ID/PW 입력 완료")

    login_btn = wait.until(EC.element_to_be_clickable(
        (By.XPATH, "//div[contains(@class,'login-pane') and contains(@class,'pop-up-background')]//button[normalize-space()='로그인']")
    ))
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
    log("✅ 통합검색 진입 완료: 여기부터 크롤링 시작")


# =========================
# Result parsing & pagination
# =========================
TABLE_SELECTOR = "table.sub_table_wr.area_table_wr.table_list_wr.mulgun-list"
TBODY_SELECTOR = "tbody.mulgun-list"
ROWS_PER_PAGE_SELECTOR = "select[name='number'].rows-per-page"
PAGINATION_UL_SELECTOR = "ul.clearfix.pagenation"
TOTAL_COUNT_SELECTOR = "span.total-count"

CSV_COLUMNS = ["사건번호", "용도", "소재지", "감정가", "최저가", "결과", "낙찰가", "낙찰율", "매각일"]


def wait_for_results_table(driver, wait):
    ensure_info_main(driver, wait)
    try:
        wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, TABLE_SELECTOR)))
        wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, TBODY_SELECTOR)))
    except UnexpectedAlertPresentException:
        handle_unexpected_alert(driver, accept=True, timeout=2)
        # alert 처리 후 다시 대기
        wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, TABLE_SELECTOR)))
        wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, TBODY_SELECTOR)))


def set_rows_per_page(driver, wait, cfg: CrawlConfig):
    """
    ✅ 강화버전: ElementNotInteractable 방지
    - scrollIntoView 후 Select 시도
    - 실패하면 JS로 value 세팅 + change/input 이벤트 강제
    - total-count=0이면 호출하지 않는 것이 원칙(아래 crawl 함수에서 처리)
    """
    ensure_info_main(driver, wait)

    el = wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, ROWS_PER_PAGE_SELECTOR)))

    # 1) 화면에 보이도록 스크롤
    try:
        driver.execute_script("arguments[0].scrollIntoView({block:'center', inline:'nearest'});", el)
        time.sleep(0.2)
    except Exception:
        pass

    # 2) 일반 Select 시도
    try:
        Select(el).select_by_value(cfg.rows_per_page)
        log(f"페이지당 건수(rows-per-page) 선택 완료: {cfg.rows_per_page}")
    except Exception as e:
        log(f"Select로 rows-per-page 변경 실패 → JS로 재시도: {type(e).__name__}")

        # 3) JS fallback
        driver.execute_script(
            """
            const sel = arguments[0];
            const val = arguments[1];
            sel.value = val;
            sel.dispatchEvent(new Event('change', {bubbles:true}));
            sel.dispatchEvent(new Event('input', {bubbles:true}));
            """,
            el,
            cfg.rows_per_page
        )
        log(f"JS로 rows-per-page 변경 완료: {cfg.rows_per_page}")

    handle_unexpected_alert(driver, accept=True, timeout=1)

    # 변경 후 재로딩 대기
    wait_for_results_table(driver, wait)
    jitter_sleep(cfg)


def get_total_count(driver, wait) -> int:
    ensure_info_main(driver, wait)
    try:
        el = wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, TOTAL_COUNT_SELECTOR)))
    except UnexpectedAlertPresentException:
        handle_unexpected_alert(driver, accept=True, timeout=2)
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
    out = []
    for li in lis:
        dp = li.get_attribute("data-page")
        if dp and dp.isdigit():
            out.append(int(dp))
    return sorted(set(out))


def goto_page(driver, wait, target_page: int, cfg: CrawlConfig):
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

            # 이미지 등 다른 요소가 겹쳐 클릭을 가로채는 경우 JS click으로 폴백
            page_selector = f"{PAGINATION_UL_SELECTOR} li[data-page='{target_page}']"
            try:
                safe_click(driver, wait, By.CSS_SELECTOR, page_selector,
                           f"페이지 이동({target_page})")
            except ElementClickInterceptedException:
                log(f"페이지 이동({target_page}): ElementClickInterceptedException → JS click 폴백")
                el = driver.find_element(By.CSS_SELECTOR, page_selector)
                driver.execute_script("arguments[0].scrollIntoView({block:'center'});", el)
                time.sleep(0.2)
                driver.execute_script("arguments[0].click();", el)
                log(f"페이지 이동({target_page}) JS click 완료")
            handle_unexpected_alert(driver, accept=True, timeout=1)

            try:
                if first_tr is not None:
                    WebDriverWait(driver, 10).until(EC.staleness_of(first_tr))
                else:
                    WebDriverWait(driver, 10).until(lambda d: get_current_on_page(driver, wait) == target_page)
            except Exception:
                WebDriverWait(driver, 10).until(lambda d: get_current_on_page(driver, wait) == target_page)

            wait_for_results_table(driver, wait)
            jitter_sleep(cfg)
            return

        if target_page > vmax:
            safe_click(driver, wait, By.CSS_SELECTOR, f"{PAGINATION_UL_SELECTOR} li.nextpg", "페이지 그룹 다음(nextpg)")
            handle_unexpected_alert(driver, accept=True, timeout=1)
            wait_for_results_table(driver, wait)
            jitter_sleep(cfg)
            continue

        if target_page < vmin:
            safe_click(driver, wait, By.CSS_SELECTOR, f"{PAGINATION_UL_SELECTOR} li.prevpg", "페이지 그룹 이전(prevpg)")
            handle_unexpected_alert(driver, accept=True, timeout=1)
            wait_for_results_table(driver, wait)
            jitter_sleep(cfg)
            continue


def normalize_address(s: str) -> str:
    s = (s or "").strip()
    s = re.sub(r"\s*-\s*", "-", s)
    s = re.sub(r"\s+", " ", s)
    return s


def parse_bid_info_from_title(title_text: str):
    if not title_text:
        return "0", "0%"

    # HTML 태그 제거 및 공백 정규화
    clean_text = re.sub(r'<[^>]+>', ' ', title_text)
    clean_text = re.sub(r'\s+', ' ', clean_text).strip()

    # 낙찰가 추출 (콜론 유무나 공백에 상관없이 숫자+콤마만 추출)
    m_price = re.search(r"낙찰가\s*[:\s]*\s*([0-9,]+)\s*원", clean_text)
    낙찰가 = m_price.group(1) if m_price else "0"

    # 낙찰율 추출
    m_rate = re.search(r"\(\s*([0-9.]+%)\s*\)", clean_text)
    낙찰율 = m_rate.group(1) if m_rate else "0%"

    return 낙찰가, 낙찰율


def parse_one_row(tr):
    tds = tr.find_elements(By.CSS_SELECTOR, "td")
    if len(tds) < 7:
        return None

    # 사건번호: "광주 7계 2023-9687(5)"
    case_text = tds[1].text.strip()
    case_lines = [x.strip() for x in case_text.split("\n") if x.strip()]
    if len(case_lines) >= 2:
        사건번호 = f"{case_lines[0]} {case_lines[1]}"
    elif len(case_lines) == 1:
        사건번호 = case_lines[0]
    else:
        사건번호 = ""

    용도 = tds[2].text.strip()

    addr = ""
    try:
        divs = tds[3].find_elements(By.CSS_SELECTOR, "div")
        if divs:
            addr = divs[-1].text.strip()
    except Exception:
        addr = tds[3].text.strip()
    소재지 = normalize_address(addr)

    감정가, 최저가 = "0", "0"
    try:
        lis = tds[4].find_elements(By.CSS_SELECTOR, "ul li")
        if len(lis) >= 2:
            감정가 = lis[0].text.strip()
            최저가 = lis[1].text.strip()
    except Exception:
        pass

    결과 = tds[5].text.strip()
    매각일 = tds[6].text.strip()

    title_text = tr.get_attribute("title") or ""
    낙찰가, 낙찰율 = parse_bid_info_from_title(title_text)

    if (not any(k in 결과 for k in ["낙찰", "매각"])) or (not title_text.strip()):
        낙찰가, 낙찰율 = "0", "0%"

    return {
        "사건번호": 사건번호,
        "용도": 용도,
        "소재지": 소재지,
        "감정가": 감정가,
        "최저가": 최저가,
        "결과": 결과,
        "낙찰가": 낙찰가,
        "낙찰율": 낙찰율,
        "매각일": 매각일,
    }


def collect_current_page_rows(driver, wait) -> List[dict]:
    ensure_info_main(driver, wait)
    tbody = driver.find_element(By.CSS_SELECTOR, TBODY_SELECTOR)
    trs = tbody.find_elements(By.CSS_SELECTOR, "tr")
    out = []
    for tr in trs:
        item = parse_one_row(tr)
        if item:
            out.append(item)
    return out


def crawl_current_result_pages(driver, wait, cfg: CrawlConfig) -> List[dict]:
    """
    - total-count로 총 페이지 계산
    - total-count==0이면 바로 return []
    - rows-per-page는 강화버전으로 50 세팅
    - 1..total_pages 순회하며 페이지 이동/수집
    """
    wait_for_results_table(driver, wait)

    total_count = get_total_count(driver, wait)
    if total_count <= 0:
        log("total-count=0 → 결과 없음, 페이지 크롤링 스킵")
        return []

    # ✅ total_count가 있을 때만 rows-per-page 변경
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


def set_search_filters_and_search(driver, wait, cfg: CrawlConfig, region: str, start: DateTuple, end: DateTuple):
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


def append_rows_to_csv(csv_path: str, rows: List[dict]):
    file_exists = os.path.exists(csv_path)
    with open(csv_path, "a", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=CSV_COLUMNS)
        if not file_exists:
            w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k, "") for k in CSV_COLUMNS})


# =========================
# Main
# =========================
def main(cfg: Optional[CrawlConfig] = None):
    if cfg is None:
        cfg = CrawlConfig()

    driver = None
    profile_dir = None

    try:
        driver, wait, profile_dir = build_driver(cfg)

        # 1) 로그인 + 통합검색 진입
        login_and_go_to_total_search(driver, wait, cfg)

        # 2) 전체 범위 생성
        ranges = generate_half_year_ranges_upto_today(
            start_year=cfg.start_year,
            end_year=cfg.end_year,
            start_month=cfg.start_month,
            start_day=cfg.start_day,
            today=date.today(),
        )
        log(f"✅ FULL MODE: 총 {len(ranges)}개 구간 실행")

        # 3) 구간별: 검색 → 모든 페이지 크롤링 → CSV 저장(append)
        for start, end in ranges:
            log(f"=== 검색 구간: {start} ~ {end} ===")
            set_search_filters_and_search(driver, wait, cfg, cfg.region, start, end)
            rows = crawl_current_result_pages(driver, wait, cfg)
            log(f"구간 완료: 수집 row={len(rows)}")
            
            if rows:
                append_rows_to_csv(cfg.output_csv, rows)
                log(f"임시 파일 저장 완료: {cfg.output_csv}")

            jitter_sleep(cfg)

        if cfg.hold_browser:
            log("정상 흐름 완료. 브라우저 유지합니다. (엔터 치면 세션 정리 후 종료)")
            input()

    except Exception as e:
        log(f"❗에러 발생: {type(e).__name__}: {e}")
        traceback.print_exc()

        if driver is not None and cfg.hold_browser_on_error:
            handle_unexpected_alert(driver, accept=True, timeout=2)
            log("에러 발생했지만 브라우저 유지합니다. (상태 확인 후 엔터 치면 종료)")
            input()

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
