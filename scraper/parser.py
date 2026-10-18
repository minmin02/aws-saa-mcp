"""ExamTopics HTML 카드(.card.exam-question-card) → 문제 딕셔너리 변환."""

import json
import re
from bs4 import BeautifulSoup


def parse_question_block(card_html: str, page_num: int, idx: int) -> dict | None:
    soup = BeautifulSoup(card_html, "lxml")

    # ── 문제 번호 ──────────────────────────────────────────────────
    # <div class="card-header">Question #1 (Topic: Exam A)</div>
    source_id = f"p{page_num}q{idx}"
    header = soup.select_one(".card-header")
    if header:
        m = re.search(r"Question\s*#(\d+)", header.get_text(), re.I)
        if m:
            source_id = f"q{m.group(1)}"

    # ── 문제 본문 ──────────────────────────────────────────────────
    # <p class="card-text">...</p>  (question-answer div 바깥)
    q_elem = soup.select_one(".card-body.question-body > p.card-text")
    if not q_elem:
        q_elem = soup.select_one("p.card-text")
    if not q_elem:
        return None
    question = q_elem.get_text(" ", strip=True)

    # ── 보기 ───────────────────────────────────────────────────────
    # <li class="multi-choice-item">
    #   <span class="multi-choice-letter" data-choice-letter="A">A.</span>
    #   텍스트
    # </li>
    choices: dict[str, str] = {}
    for item in soup.select("li.multi-choice-item"):
        letter_span = item.select_one(".multi-choice-letter")
        if not letter_span:
            continue
        letter = letter_span.get("data-choice-letter", "").strip().upper()
        if not re.match(r"^[A-E]$", letter):
            continue
        # 라벨 span 제거 후 텍스트 추출
        letter_span.decompose()
        # most-voted badge도 제거
        for badge in item.select(".most-voted-answer-badge"):
            badge.decompose()
        text = item.get_text(" ", strip=True)
        choices[letter] = text

    if not choices:
        return None

    # ── 정답 ───────────────────────────────────────────────────────
    # <div class="card-text question-answer">
    #   <strong class="ml-1">A</strong>
    # </div>
    answer: list[str] = []
    ans_div = soup.select_one(".question-answer")
    if ans_div:
        strong = ans_div.select_one("strong.ml-1")
        if strong:
            raw = strong.get_text(strip=True)
            # 단일(A) 또는 다중(AC, A, C) 형태 파싱
            answer = [c for c in re.split(r"[^A-E]+", raw.upper()) if re.match(r"^[A-E]$", c)]

    # ── 커뮤니티 투표 ──────────────────────────────────────────────
    # <div class="answer-chart" data-chart='[{"answer":"A","percent":95,"votes":160}]'>
    votes: list[dict] = []
    chart_div = soup.select_one(".answer-chart[data-chart]")
    if chart_div:
        try:
            raw_votes = json.loads(chart_div["data-chart"])
            votes = [
                {"option": v["answer"], "pct": v["percent"], "votes": v.get("votes", 0)}
                for v in raw_votes
                if v.get("answer") not in ("U",)  # "U"(Unknown) 제외
            ]
        except (json.JSONDecodeError, KeyError):
            pass

    # ── 해설 ───────────────────────────────────────────────────────
    # ExamTopics는 "Reveal Solution" 클릭 후 표시 — 기본 숨김
    # 향후 JS 확장 가능, 현재는 빈 문자열
    explanation = ""

    return {
        "source_id": source_id,
        "question": question,
        "choices": choices,
        "answer": answer,
        "explanation": explanation,
        "votes": votes,
    }
