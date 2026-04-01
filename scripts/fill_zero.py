import os
import sys
import re
import time
import random
import shutil
import tempfile
import traceback
from dataclasses import dataclass

import pandas as pd

from selenium import webdriver
from selenium.webdriver.common.by import By
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
# Config
# =========================
@dataclass
class FillConfig:
    base_url: str = "https://www.infocare.co.kr/"
    userid: str = "광주은행"
    passwd: str = "1234"

    wait_sec: int = 25
    hold_browser_on_error: bool = False
    cleanup_profile_dir_on_exit: bool = True

    min_delay: float = 0.2
    max_delay: float = 0.6

    rows_per_page: str = "50"

    input_csv: str = "data/경북.csv"
    output_csv: str = "data/경북.csv"


# =========================
# Logging / timing
# =========================
def log(msg: str):
    print(f"[LOG] {msg}")


def jitter_sleep(cfg: FillConfig):
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
            try:
                el = driver.find_element(by, selector)
                driver.execute_script("arguments[0].scrollIntoView({block:'center'});", el)
                time.sleep(0.15)
                driver.execute_script("arguments[0].click();", el)
                log(f"{desc} JS click 완료")
                return el
            except Exception as js_err:
                log(f"{desc}: JS click 실패({attempt}/{retries+1}) - {js_err}")

        except (StaleElementReferenceException, WebDriverException, TimeoutException) as e:
            last_err = e
            handle_unexpected_alert(driver, accept=True, timeout=1)
            log(f"{desc}: 재시도({attempt}/{retries+1}) - {type(e).__name__}")

    raise last_err


def safe_select_by_value(driver, wait, by, selector, value, desc, retries=2):
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
            log(f"{desc}: 재시도({attempt}/{retries+1}) - {type(e).__name__}")

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
# Driver / navigation
# =========================
def build_driver(cfg: FillConfig):
    profile_dir = tempfile.mkdtemp(prefix="selenium-infocare-fill-")

    options = webdriver.ChromeOptions()
    options.add_argument("--start-maximized")
    options.add_argument("--disable-gpu")
    options.add_argument(f"--user-data-dir={profile_dir}")

    driver = webdriver.Chrome(options=options)
    try:
        driver.execute_cdp_cmd("Network.enable", {})
        driver.execute_cdp_cmd("Network.clearBrowserCache", {})
        log("브라우저 캐시 초기화 완료")
    except Exception as e:
        log(f"브라우저 캐시 초기화 실패(무시): {e}")

    wait = WebDriverWait(driver, cfg.wait_sec)
    return driver, wait, profile_dir


def login_and_go_to_total_search(driver, wait, cfg: FillConfig):
    driver.get(cfg.base_url)
    log("메인 접속 완료")

    ensure_info_main(driver, wait)

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
    login_btn.click()
    log("로그인 버튼 클릭 완료")

    handle_unexpected_alert(driver, accept=True, timeout=2)

    ensure_info_main(driver, wait)

    safe_click(driver, wait, By.CSS_SELECTOR, "li.main_nav_li02 > a", "법원경매")
    handle_unexpected_alert(driver, accept=True, timeout=2)

    safe_click(driver, wait, By.CSS_SELECTOR, "a[href='/bubwon/search/search_total.asp']", "통합검색")

    driver.switch_to.default_content()
    ensure_info_main(driver, wait)
    log("통합검색 진입 완료")


# =========================
# Result parsing & pagination
# =========================
TABLE_SELECTOR = "table.sub_table_wr.area_table_wr.table_list_wr.mulgun-list"
TBODY_SELECTOR = "tbody.mulgun-list"
ROWS_PER_PAGE_SELECTOR = "select[name='number'].rows-per-page"
PAGINATION_UL_SELECTOR = "ul.clearfix.pagenation"
TOTAL_COUNT_SELECTOR = "span.total-count"


def wait_for_results_table(driver, wait):
    ensure_info_main(driver, wait)
    wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, TABLE_SELECTOR)))
    wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, TBODY_SELECTOR)))


def set_rows_per_page(driver, wait, cfg: FillConfig):
    ensure_info_main(driver, wait)
    el = wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, ROWS_PER_PAGE_SELECTOR)))

    try:
        driver.execute_script("arguments[0].scrollIntoView({block:'center'});", el)
        time.sleep(0.2)
    except Exception:
        pass

    try:
        Select(el).select_by_value(cfg.rows_per_page)
        log(f"페이지당 건수 선택 완료: {cfg.rows_per_page}")
    except Exception:
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
        log(f"JS로 페이지당 건수 변경 완료: {cfg.rows_per_page}")

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


def get_visible_page_numbers(driver, wait):
    ensure_info_main(driver, wait)
    ul = driver.find_element(By.CSS_SELECTOR, PAGINATION_UL_SELECTOR)
    lis = ul.find_elements(By.CSS_SELECTOR, "li[data-page]")
    out = []
    for li in lis:
        dp = li.get_attribute("data-page")
        if dp and dp.isdigit():
            out.append(int(dp))
    return sorted(set(out))


def goto_page(driver, wait, target_page: int, cfg: FillConfig):
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

            page_selector = f"{PAGINATION_UL_SELECTOR} li[data-page='{target_page}']"
            safe_click(driver, wait, By.CSS_SELECTOR, page_selector, f"페이지 이동({target_page})")
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


# =========================
# Parsing helpers
# =========================
def normalize_address(s: str) -> str:
    s = (s or "").strip()
    s = re.sub(r"\s*-\s*", "-", s)
    s = re.sub(r"\s+", " ", s)
    return s


def normalize_case_no(s: str) -> str:
    s = (s or "").strip()
    s = re.sub(r"\s+", " ", s)
    return s


def normalize_result_date(s: str) -> str:
    s = (s or "").strip()
    s = s.replace("-", ".")
    return s


def extract_case_parts(case_text: str):
    """
    예:
    '광주 5계 2021-1320(10)' -> ('2021', '1320', '10')
    '광주 5계 2021-1320'     -> ('2021', '1320', None)
    """
    s = str(case_text).strip()
    m = re.search(r"(\d{4})-(\d+)(?:\((\d+)\))?", s)
    if not m:
        return None, None, None
    year = m.group(1)
    sano = m.group(2)
    mul_no = m.group(3) if m.group(3) else None
    return year, sano, mul_no


def parse_bid_info_from_title(title_text: str):
    if not title_text:
        return "0", "0%"

    # HTML 태그 제거 및 공백 정규화
    clean_text = re.sub(r'<[^>]+>', ' ', title_text)
    clean_text = re.sub(r'\s+', ' ', clean_text).strip()

    # 낙찰가 추출 (콜론 유무나 공백에 상관없이 숫자+콤마 추출)
    m_price = re.search(r"낙찰가\s*[:\s]*\s*([0-9,]+)\s*원", clean_text)
    낙찰가 = m_price.group(1) if m_price else "0"

    # 낙찰율 추출 (괄호 안의 % 형태 추출)
    m_rate = re.search(r"\(\s*([0-9.]+%)\s*\)", clean_text)
    낙찰율 = m_rate.group(1) if m_rate else "0%"

    return 낙찰가, 낙찰율


def parse_one_row(tr):
    tds = tr.find_elements(By.CSS_SELECTOR, "td")
    if len(tds) < 7:
        return None

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
    매각일 = normalize_result_date(tds[6].text.strip())

    title_text = tr.get_attribute("title") or ""
    낙찰가, 낙찰율 = parse_bid_info_from_title(title_text)

    if "낙찰" not in 결과:
        낙찰가, 낙찰율 = "0", "0%"

    return {
        "사건번호": normalize_case_no(사건번호),
        "용도": 용도.strip(),
        "소재지": 소재지,
        "감정가": 감정가,
        "최저가": 최저가,
        "결과": 결과,
        "낙찰가": 낙찰가,
        "낙찰율": 낙찰율,
        "매각일": 매각일,
    }


def collect_current_page_rows(driver, wait):
    ensure_info_main(driver, wait)
    tbody = driver.find_element(By.CSS_SELECTOR, TBODY_SELECTOR)
    trs = tbody.find_elements(By.CSS_SELECTOR, "tr")
    out = []
    for tr in trs:
        item = parse_one_row(tr)
        if item:
            out.append(item)
    return out


# =========================
# Search by case number
# =========================
def search_by_case_no(driver, wait, case_year: str, sano: str):
    ensure_info_main(driver, wait)

    year_el = wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, "select[name='year']")))
    Select(year_el).select_by_value(str(case_year))
    log(f"사건년도 선택 완료: {case_year}")

    sano_el = wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, "input[name='sano']")))
    sano_el.clear()
    sano_el.send_keys(str(sano))
    log(f"사건번호 입력 완료: {sano}")

    safe_click(driver, wait, By.CSS_SELECTOR, "button.sch_btn.btn_submit", "사건번호 검색 버튼")
    handle_unexpected_alert(driver, accept=True, timeout=2)

    wait_for_results_table(driver, wait)
    time.sleep(0.5)


# =========================
# Matching logic
# =========================
def find_matching_row(rows, target_case_no, target_usage, target_addr, target_sale_date):
    target_case_no = normalize_case_no(target_case_no)
    target_usage = (target_usage or "").strip()
    target_addr = normalize_address(target_addr)
    target_sale_date = normalize_result_date(target_sale_date)

    _, _, target_mul_no = extract_case_parts(target_case_no)

    exact_candidates = []

    for r in rows:
        row_case = normalize_case_no(r["사건번호"])
        row_usage = (r["용도"] or "").strip()
        row_addr = normalize_address(r["소재지"])
        row_date = normalize_result_date(r["매각일"])

        _, _, row_mul_no = extract_case_parts(row_case)

        date_ok = row_date == target_sale_date
        usage_ok = row_usage == target_usage
        addr_ok = row_addr == target_addr
        mul_ok = (target_mul_no == row_mul_no) if target_mul_no else True

        if date_ok and usage_ok and addr_ok and mul_ok:
            return r

        if date_ok and mul_ok:
            exact_candidates.append(r)

    if len(exact_candidates) == 1:
        return exact_candidates[0]

    return None


def crawl_and_fill_one(driver, wait, cfg: FillConfig, target_row):
    case_text = str(target_row["사건번호"]).strip()
    case_year, sano, mul_no = extract_case_parts(case_text)

    if not case_year or not sano:
        log(f"사건번호 파싱 실패: {case_text}")
        return None

    log(f"재조회 시작: 사건번호={case_text} / 연도={case_year} / 사건번호={sano} / 물건번호={mul_no}")

    search_by_case_no(driver, wait, case_year, sano)

    total_count = get_total_count(driver, wait)
    if total_count <= 0:
        log("검색 결과 없음")
        return None

    set_rows_per_page(driver, wait, cfg)

    per_page = int(cfg.rows_per_page)
    total_pages = (total_count + per_page - 1) // per_page

    all_rows = []
    for p in range(1, total_pages + 1):
        if p > 1:
            goto_page(driver, wait, p, cfg)
        rows = collect_current_page_rows(driver, wait)
        all_rows.extend(rows)

    matched = find_matching_row(
        all_rows,
        target_case_no=target_row["사건번호"],
        target_usage=target_row["용도"],
        target_addr=target_row["소재지"],
        target_sale_date=target_row["매각일"],
    )

    return matched


# =========================
# CSV helpers
# =========================
def is_zero_price(x) -> bool:
    s = str(x).strip().replace(",", "")
    return s in {"0", "0.0", "", "nan", "None"}


def is_zero_rate(x) -> bool:
    s = str(x).strip().replace("%", "")
    return s in {"0", "0.0", "", "nan", "None"}


# =========================
# Main
# =========================
import sys

def main():
    cfg = FillConfig()

    if len(sys.argv) > 1:
        cfg.input_csv = sys.argv[1]
        cfg.output_csv = sys.argv[1]

    if not os.path.exists(cfg.input_csv):
        raise FileNotFoundError(f"입력 CSV가 없습니다: {cfg.input_csv}")

    df = pd.read_csv(cfg.input_csv, encoding="utf-8-sig")

    # --- 2000년 이하 데이터 삭제 로직 ---
    def check_year_valid(case_no):
        y, _, _ = extract_case_parts(case_no)
        try:
            return int(y) > 2000 if y else True # 연도 추출 안 되면 유지(또는 False로 삭제 가능)
        except:
            return True

    initial_count = len(df)
    df = df[df["사건번호"].apply(check_year_valid)].copy()
    dropped_count = initial_count - len(df)
    if dropped_count > 0:
        log(f"2000년 이하(오래된 사건) 데이터 {dropped_count}건 삭제 완료.")
    # ------------------------------

    required_cols = ["사건번호", "용도", "소재지", "감정가", "최저가", "결과", "낙찰가", "낙찰율", "매각일"]
    missing = [c for c in required_cols if c not in df.columns]
    if missing:
        raise ValueError(f"CSV에 필요한 컬럼이 없습니다: {missing}")

    # 결과가 낙찰인 행만, 그리고 낙찰가/낙찰율이 0인 경우만 보정
    target_mask = df.apply(
        lambda row: (
            "낙찰" in str(row["결과"]).strip()
            and (
                is_zero_price(row["낙찰가"])
                or is_zero_rate(row["낙찰율"])
            )
        ),
        axis=1
    )

    target_df = df[target_mask].copy()

    log(f"전체 행 수: {len(df)}")
    log(f"보정 대상 행 수(결과=낙찰 & 낙찰가/낙찰율 0): {len(target_df)}")

    if target_df.empty:
        log("보정 대상이 없습니다.")
        df.to_csv(cfg.output_csv, index=False, encoding="utf-8-sig")
        log(f"저장 완료: {cfg.output_csv}")
        return

    driver = None
    profile_dir = None

    try:
        driver, wait, profile_dir = build_driver(cfg)
        login_and_go_to_total_search(driver, wait, cfg)

        success_count = 0
        fail_count = 0
        indices_to_drop = []

        for idx in target_df.index:
            row = df.loc[idx].copy()
            matched = None

            try:
                matched = crawl_and_fill_one(driver, wait, cfg, row)
            except Exception as e:
                log(f"1차 보정 중 오류: idx={idx}, 사건번호={row['사건번호']}, err={e}")
                handle_unexpected_alert(driver, accept=True, timeout=1)

            # 첫 번째 실패 시 한 번 더 재시도
            if not matched or (matched["낙찰가"] == "0" and matched["낙찰율"] == "0%"):
                log(f"1차 검색 실패. 검색만 한 번 더 재시도합니다: idx={idx}, 사건번호={row['사건번호']}")
                jitter_sleep(cfg)
                try:
                    matched = crawl_and_fill_one(driver, wait, cfg, row)
                except Exception as e:
                    log(f"2차 검색 재시도 중 오류: idx={idx}, err={e}")
                    handle_unexpected_alert(driver, accept=True, timeout=1)

            # 최종 평가 및 처리
            if matched and (matched["낙찰가"] != "0" or matched["낙찰율"] != "0%"):
                df.at[idx, "낙찰가"] = matched["낙찰가"]
                df.at[idx, "낙찰율"] = matched["낙찰율"]
                success_count += 1
                log(
                    f"보정 성공: idx={idx}, 사건번호={row['사건번호']}, "
                    f"낙찰가={matched['낙찰가']}, 낙찰율={matched['낙찰율']}"
                )
            else:
                fail_count += 1
                indices_to_drop.append(idx)
                log(f"최종 매칭 실패(삭제 예정): idx={idx}, 사건번호={row['사건번호']}")

            jitter_sleep(cfg)

        # 수집 못 한 실패 행들은 깨끗이 삭제
        if indices_to_drop:
            df.drop(indices_to_drop, inplace=True)
            log(f"최종 실패한 {len(indices_to_drop)}건의 데이터를 원본에서 완전 삭제했습니다.")

        df.to_csv(cfg.output_csv, index=False, encoding="utf-8-sig")
        log(f"저장 완료: {cfg.output_csv}")
        log(f"보정 성공 {success_count}건 / 실패(삭제처리) {fail_count}건")

    except Exception as e:
        log(f"❗에러 발생: {type(e).__name__}: {e}")
        traceback.print_exc()

        if driver is not None and cfg.hold_browser_on_error:
            handle_unexpected_alert(driver, accept=True, timeout=2)
            log("에러 발생했지만 브라우저 유지합니다. 확인 후 엔터를 누르세요.")
            input()
        
        # 시스템 종료 코드 1 반환 -> 부모 프로세스(전처리.py)가 실패를 인지하게 만듦
        sys.exit(1)

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
                log(f"프로필 폴더 삭제 완료: {profile_dir}")
            except Exception as e:
                log(f"프로필 폴더 삭제 실패(무시 가능): {e}")


if __name__ == "__main__":
    main()