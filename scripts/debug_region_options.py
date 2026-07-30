import os
import sys

sys.path.append(os.path.dirname(__file__))

from full_crawling import CrawlConfig, build_driver, login_and_go_to_total_search, ensure_info_main
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait, Select


def main():
    cfg = CrawlConfig()
    driver, wait, profile_dir = build_driver(cfg)
    try:
        login_and_go_to_total_search(driver, wait, cfg)
        ensure_info_main(driver, wait)

        el = wait.until(lambda d: d.find_element(By.CSS_SELECTOR, "select[name='addr_do']"))
        select = Select(el)

        print("=== addr_do 드롭다운 옵션 목록 (value | text) ===")
        for opt in select.options:
            value = opt.get_attribute("value")
            text = opt.text.strip()
            marker = "  <-- 확인" if ("광주" in text or "전남" in text or "광주" in value or "전남" in value) else ""
            print(f"{value!r:20} | {text}{marker}")

    finally:
        input("\n확인 후 Enter를 누르면 브라우저를 닫습니다...")
        driver.quit()


if __name__ == "__main__":
    main()
