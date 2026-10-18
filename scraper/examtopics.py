"""ExamTopics AWS-SAA-C03 스크래퍼 (개인 로컬 전용 — 배포 금지).

사용법:
    uv run aws-saa-scrape                  # 전체 수집
    uv run aws-saa-scrape --start 1 --end 50  # 페이지 범위 지정
"""

import argparse
import asyncio
import json
import time
from pathlib import Path

from playwright.async_api import async_playwright, BrowserContext, Page

from scraper.parser import parse_question_block

BASE_URL = (
    "https://www.examtopics.com/exams/amazon"
    "/aws-certified-solutions-architect-associate-saa-c03/view/"
)
OUTPUT_DIR = Path(__file__).parent.parent / "data" / "raw"
MERGED_FILE = Path(__file__).parent.parent / "data" / "questions_raw.json"
PROFILE_DIR = Path(__file__).parent.parent / "data" / "browser_profile"
REQUEST_DELAY = 3.0  # 서버 부하 방지 (초)


async def _accept_cookies(page: Page) -> None:
    try:
        btn = page.locator("button:has-text('Accept'), button:has-text('동의')")
        if await btn.count() > 0:
            await btn.first.click()
            await page.wait_for_timeout(1000)
    except Exception:
        pass


async def scrape_page(page: Page, page_num: int, debug: bool = False) -> list[dict]:
    url = f"{BASE_URL}{page_num}/"
    await page.goto(url, wait_until="networkidle", timeout=60_000)
    await page.wait_for_timeout(2000)
    await _accept_cookies(page)

    if debug:
        html_dump = OUTPUT_DIR / f"debug_page_{page_num}.html"
        html_dump.write_text(await page.content(), encoding="utf-8")
        print(f"\n[DEBUG] HTML 저장: {html_dump}")

    # 전체 카드(.card.exam-question-card)를 파싱 — 헤더(번호) + 바디(문제) 포함
    CARD_SELECTORS = [
        ".card.exam-question-card",
        ".exam-question-card",
        ".question-body",           # fallback
        "[class*='question-card']",
    ]

    cards = []
    for sel in CARD_SELECTORS:
        cards = await page.query_selector_all(sel)
        if cards:
            if debug:
                print(f"[DEBUG] 매칭 셀렉터: {sel!r} ({len(cards)}개)")
            break

    results = []
    for idx, card in enumerate(cards, start=1):
        html = await card.inner_html()
        parsed = parse_question_block(html, page_num, idx)
        if parsed:
            results.append(parsed)
    return results


async def run(start_page: int = 1, end_page: int = 100) -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    existing: list[dict] = []
    if MERGED_FILE.exists():
        existing = json.loads(MERGED_FILE.read_text(encoding="utf-8"))
        existing_ids = {q["source_id"] for q in existing}
        print(f"기존 데이터 {len(existing)}개 로드됨")
    else:
        existing_ids: set[str] = set()

    PROFILE_DIR.mkdir(parents=True, exist_ok=True)

    async with async_playwright() as p:
        # persistent_context: 쿠키/로컬스토리지가 PROFILE_DIR에 저장되어 재실행 시 로그인 유지
        context: BrowserContext = await p.chromium.launch_persistent_context(
            str(PROFILE_DIR),
            headless=False,
            user_agent=(
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
        )
        page = await context.new_page()

        # 로그인 상태 확인
        await page.goto("https://www.examtopics.com/", wait_until="domcontentloaded", timeout=30_000)
        await page.wait_for_timeout(2000)
        logged_in = await page.query_selector("a[href*='logout'], a[href*='sign-out'], .user-menu") is not None
        if not logged_in:
            print("로그인이 필요합니다. 브라우저에서 ExamTopics에 로그인하세요.")
            print("로그인 완료 후 Enter를 누르면 스크래핑이 시작됩니다.")
            input(">>> 로그인 완료 후 Enter: ")
        else:
            print("로그인 상태 감지됨. 바로 스크래핑을 시작합니다.")

        print(f"스크래핑 범위: 페이지 {start_page} ~ {end_page}\n")

        all_new: list[dict] = []
        empty_streak = 0

        for page_num in range(start_page, end_page + 1):
            print(f"  페이지 {page_num:3d} 수집 중...", end=" ", flush=True)
            try:
                questions = await scrape_page(page, page_num)
                if not questions:
                    empty_streak += 1
                    print(f"문제 없음 (연속 {empty_streak}회)")
                    if empty_streak >= 3:
                        print("  → 빈 페이지 3회 연속, 종료합니다.")
                        break
                    await asyncio.sleep(REQUEST_DELAY)
                    continue
                empty_streak = 0

                # 중복 제거
                new_qs = [q for q in questions if q["source_id"] not in existing_ids]
                all_new.extend(new_qs)
                existing_ids.update(q["source_id"] for q in new_qs)
                print(f"{len(questions)}개 (신규 {len(new_qs)}개, 누계 {len(existing) + len(all_new)}개)")

                # 페이지별 저장
                out_file = OUTPUT_DIR / f"page_{page_num:03d}.json"
                out_file.write_text(
                    json.dumps(questions, ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )

            except Exception as e:
                print(f"오류: {e}")

            await asyncio.sleep(REQUEST_DELAY)

        await context.close()

    # 전체 합본 저장
    merged = existing + all_new
    MERGED_FILE.write_text(
        json.dumps(merged, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"\n완료: 신규 {len(all_new)}개 추가 → 총 {len(merged)}개 ({MERGED_FILE})")


def main() -> None:
    parser = argparse.ArgumentParser(description="ExamTopics SAA-C03 스크래퍼")
    parser.add_argument("--start", type=int, default=1, help="시작 페이지 (기본 1)")
    parser.add_argument("--end",   type=int, default=100, help="종료 페이지 (기본 100)")
    args = parser.parse_args()
    asyncio.run(run(args.start, args.end))


if __name__ == "__main__":
    main()
