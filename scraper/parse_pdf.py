"""SAA-C03_Examtopics KOR PDF → questions_raw.json 파서.

사용법:
    uv run aws-saa-pdf                             # 기본 PDF 경로
    uv run aws-saa-pdf --file path/to/file.pdf     # PDF 경로 지정
    uv run aws-saa-pdf --dry-run                   # 파싱 결과만 출력
"""

import argparse
import json
import re
import sys
from pathlib import Path

RAW_JSON = Path(__file__).parent.parent / "data" / "questions_raw.json"
DEFAULT_PDF = next(
    (p for p in Path(__file__).parent.parent.glob("*.pdf")
     if "SAA" in p.name and "강의" not in p.name),
    Path(__file__).parent.parent / "SAA-C03_Examtopics_V20.35_KOR.pdf",
)


def _clean(text: str) -> str:
    """PDF 추출 시 생기는 과도한 공백 제거."""
    text = re.sub(r"[ \t]{2,}", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _extract_text(pdf_path: Path) -> str:
    try:
        from pdfminer.high_level import extract_text
    except ImportError:
        print("pdfminer.six 가 없습니다. 설치 중...")
        import subprocess, sys
        subprocess.check_call([sys.executable, "-m", "pip", "install", "pdfminer.six"])
        from pdfminer.high_level import extract_text
    return _clean(extract_text(str(pdf_path)))


def _split_blocks(text: str) -> list[tuple[int, str]]:
    """Q1, Q2 … 단위로 분리. (번호, 블록텍스트) 리스트 반환."""
    pattern = re.compile(r"\n(Q(\d+))\s*\n")
    matches = list(pattern.finditer(text))
    blocks = []
    for i, m in enumerate(matches):
        start = m.start()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        num = int(m.group(2))
        block = text[start:end].strip()
        blocks.append((num, block))
    return blocks


def _parse_block(num: int, block: str) -> dict | None:
    lines = [l.rstrip() for l in block.splitlines()]

    # ── 선택지 ──────────────────────────────────────────────────────────────
    choice_pat = re.compile(r"^\s*([A-Ea-e])[\.。]\s+(.+)")
    choices: dict[str, str] = {}
    choice_indices: list[int] = []

    for i, line in enumerate(lines):
        m = choice_pat.match(line)
        if m:
            letter = m.group(1).upper()
            body = m.group(2).strip()
            # 줄이어붙이기 (다음 선택지 전까지, 빈줄 1개는 허용)
            j = i + 1
            empty_streak = 0
            while j < len(lines):
                nxt = lines[j].strip()
                if not nxt:
                    empty_streak += 1
                    if empty_streak >= 2:
                        break
                    j += 1
                    continue
                empty_streak = 0
                if choice_pat.match(lines[j]):
                    break
                if re.match(r"(Answer|정답|https?://)", nxt, re.I):
                    break
                body += " " + nxt
                j += 1
            choices[letter] = body.strip()
            choice_indices.append(i)

    if len(choices) < 2:
        return None

    # ── 문제 본문 ────────────────────────────────────────────────────────────
    first_choice = choice_indices[0] if choice_indices else len(lines)
    q_lines = []
    for line in lines[1:first_choice]:   # lines[0] = "Q{num}"
        s = line.strip()
        if not s:
            continue
        if re.match(r"https?://", s):
            continue
        q_lines.append(s)
    question = " ".join(q_lines).strip()
    if not question:
        return None

    # ── 정답 ────────────────────────────────────────────────────────────────
    answer: list[str] = []
    for line in lines:
        m = re.search(r"[Aa]nswer\s*[:：]\s*([A-Ea-e ,/]+)", line)
        if m:
            answer = sorted(set(re.findall(r"[A-E]", m.group(1).upper())))
            break

    # ── 해설 ────────────────────────────────────────────────────────────────
    exp_lines: list[str] = []
    in_exp = False
    for line in lines:
        s = line.strip()
        if not in_exp:
            if re.search(r"[Aa]nswer\s*[:：]", s):
                in_exp = True
            continue
        # URL 전체 및 URL 연속 파편 제거 (예: "ions-architect-associate-saa-c03/")
        if re.match(r"https?://", s):
            continue
        if re.match(r"^[a-z0-9\-]+(?:/[a-z0-9\-]*)*/?$", s):
            continue
        # "설명:・", "설명:" 같은 마커 제거
        s = re.sub(r"^설명\s*[:：・]\s*", "", s).strip()
        if not s:
            continue
        if re.match(r"Q\d+\s*$", s):
            break
        exp_lines.append(s)
    explanation = " ".join(exp_lines).strip()

    return {
        "source_id": f"q{num}",
        "question": question,
        "choices": choices,
        "answer": answer,
        "explanation": explanation,
        "votes": [],
    }


def parse_pdf(pdf_path: Path) -> list[dict]:
    print(f"PDF 텍스트 추출 중: {pdf_path.name}")
    text = _extract_text(pdf_path)
    blocks = _split_blocks(text)
    print(f"문제 블록 감지: {len(blocks)}개")

    results = []
    skipped = 0
    for num, block in blocks:
        parsed = _parse_block(num, block)
        if parsed:
            results.append(parsed)
        else:
            skipped += 1

    if skipped:
        print(f"파싱 실패 (선택지 없음 등): {skipped}개 스킵")
    return results


def merge(new_qs: list[dict]) -> tuple[list[dict], int, int]:
    existing: list[dict] = []
    if RAW_JSON.exists():
        existing = json.loads(RAW_JSON.read_text(encoding="utf-8"))
    existing_map = {q["source_id"]: i for i, q in enumerate(existing)}

    added = updated = 0
    for q in new_qs:
        if q["source_id"] not in existing_map:
            existing.append(q)
            existing_map[q["source_id"]] = len(existing) - 1
            added += 1
        else:
            idx = existing_map[q["source_id"]]
            changed = False
            # answer/explanation/choices 보강
            if q["answer"] and not existing[idx].get("answer"):
                existing[idx]["answer"] = q["answer"]
                changed = True
            if q["explanation"] and not existing[idx].get("explanation"):
                existing[idx]["explanation"] = q["explanation"]
                changed = True
            # 새 파싱 결과가 더 완전하면(평균 길이가 길면) 항상 덮어씀
            if q["choices"]:
                old_avg = sum(len(v) for v in existing[idx].get("choices", {}).values()) / max(len(existing[idx].get("choices", {})), 1)
                new_avg = sum(len(v) for v in q["choices"].values()) / len(q["choices"])
                if not existing[idx].get("choices") or new_avg > old_avg:
                    existing[idx]["choices"] = q["choices"]
                    changed = True
            if changed:
                updated += 1

    RAW_JSON.parent.mkdir(exist_ok=True)
    RAW_JSON.write_text(json.dumps(existing, ensure_ascii=False, indent=2), encoding="utf-8")
    return existing, added, updated


def main() -> None:
    parser = argparse.ArgumentParser(description="SAA PDF → questions_raw.json")
    parser.add_argument("--file", type=Path, default=DEFAULT_PDF)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    if not args.file.exists():
        print(f"파일 없음: {args.file}")
        sys.exit(1)

    questions = parse_pdf(args.file)
    print(f"파싱 완료: {len(questions)}개 문제")

    # 정답 있는 문제 비율 확인
    has_answer = sum(1 for q in questions if q["answer"])
    print(f"정답 포함: {has_answer}개 ({has_answer/len(questions)*100:.1f}%)")

    if args.dry_run:
        for q in questions[:5]:
            print(f"\n[{q['source_id']}] {q['question'][:80]}…")
            print(f"  보기: {list(q['choices'].keys())}  정답: {q['answer']}")
            if q["explanation"]:
                print(f"  해설: {q['explanation'][:60]}…")
        print(f"\n... 총 {len(questions)}개")
        return

    all_qs, added, updated = merge(questions)
    print(f"questions_raw.json: +{added}개 추가, {updated}개 업데이트 → 총 {len(all_qs)}개")

    print("로컬 DB 시드 중...")
    from db.seed import seed
    seed(RAW_JSON)
    print("완료! Railway 배포: railway up")


if __name__ == "__main__":
    main()
