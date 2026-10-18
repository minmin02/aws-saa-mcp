"""Q.md → questions_raw.json 마이그레이션.

ExamTopics에서 복사한 텍스트를 Q.md에 붙여넣으면
questions_raw.json에 병합 후 로컬 DB를 재시드합니다.

사용법:
    uv run aws-saa-qmd              # Q.md (기본 경로) 처리
    uv run aws-saa-qmd --file Q.md  # 파일 경로 지정
    uv run aws-saa-qmd --dry-run    # 파싱 결과만 출력 (DB 반영 안 함)
"""

import argparse
import json
import re
import sys
from pathlib import Path

RAW_JSON = Path(__file__).parent.parent / "data" / "questions_raw.json"
DEFAULT_QMD = Path(__file__).parent.parent / "Q.md"


# ── 파서 ──────────────────────────────────────────────────────────────────────

def _split_blocks(text: str) -> list[str]:
    """문제 단위로 텍스트를 분리한다."""
    # 구분자: --- 또는 빈줄 후 Question # 패턴
    blocks = re.split(r"\n---+\n|\n===+\n", text)
    result = []
    for block in blocks:
        block = block.strip()
        if block:
            result.append(block)
    return result


def _parse_block(block: str) -> dict | None:
    lines = [l.rstrip() for l in block.splitlines()]

    # ── source_id ────────────────────────────────────────────────────────────
    source_id = None
    for line in lines:
        m = re.search(r"[Qq]uestion\s*#?(\d+)", line)
        if m:
            source_id = f"q{m.group(1)}"
            break
    if not source_id:
        return None  # 번호 없으면 스킵

    # ── 선택지 파싱 ──────────────────────────────────────────────────────────
    # "A. ...", "A) ...", "(A) ..." 형태 모두 허용
    choice_pattern = re.compile(r"^\s*[\(\[]?([A-Ea-e])[\)\]\.]\s+(.+)")
    choices: dict[str, str] = {}
    choice_indices: list[int] = []

    for i, line in enumerate(lines):
        m = choice_pattern.match(line)
        if m:
            letter = m.group(1).upper()
            text = m.group(2).strip()
            # 연속된 다음 줄이 선택지 아니면 이어붙이기
            j = i + 1
            while j < len(lines):
                next_line = lines[j].strip()
                if not next_line:
                    break
                if choice_pattern.match(lines[j]):
                    break
                # 정답/커뮤니티 줄이면 중단
                if re.match(r"(Correct Answer|Most Voted|Community|Highly Voted)", next_line, re.I):
                    break
                text += " " + next_line
                j += 1
            choices[letter] = text.strip()
            choice_indices.append(i)

    if len(choices) < 2:
        return None

    # ── 문제 본문 ────────────────────────────────────────────────────────────
    first_choice_line = choice_indices[0] if choice_indices else len(lines)
    q_lines = []
    for line in lines[:first_choice_line]:
        stripped = line.strip()
        # 헤더(Question #N Topic X) 줄 스킵
        if re.search(r"[Qq]uestion\s*#?\d+", stripped):
            continue
        if re.match(r"^Topic\s+\d+", stripped, re.I):
            continue
        if stripped:
            q_lines.append(stripped)
    question = " ".join(q_lines).strip()
    if not question:
        return None

    # ── 정답 ─────────────────────────────────────────────────────────────────
    answer: list[str] = []
    for line in lines:
        m = re.search(r"[Cc]orrect\s+[Aa]nswer\s*[:：]\s*([A-Ea-e ,/]+)", line)
        if not m:
            m = re.search(r"[Aa]nswer\s*[:：]\s*([A-Ea-e ,/]+)", line)
        if m:
            raw = m.group(1)
            answer = sorted(set(re.findall(r"[A-Ea-e]", raw.upper())))
            break

    # ── 커뮤니티 투표 ─────────────────────────────────────────────────────────
    votes: list[dict] = []
    for line in lines:
        for opt, pct in re.findall(r"\b([A-E])\s+\((\d+)%\)", line):
            votes.append({"option": opt.upper(), "pct": int(pct), "votes": 0})

    # ── 해설 ─────────────────────────────────────────────────────────────────
    explanation = ""
    in_exp = False
    exp_lines: list[str] = []
    for line in lines:
        if re.match(r"\s*(Explanation|해설|풀이)\s*[:：]?", line, re.I):
            in_exp = True
            tail = re.sub(r"(Explanation|해설|풀이)\s*[:：]?", "", line, flags=re.I).strip()
            if tail:
                exp_lines.append(tail)
            continue
        if in_exp:
            if re.match(r"^\s*---", line):
                break
            if line.strip():
                exp_lines.append(line.strip())
    explanation = " ".join(exp_lines).strip()

    return {
        "source_id": source_id,
        "question": question,
        "choices": choices,
        "answer": answer,
        "explanation": explanation,
        "votes": votes,
    }


def parse_qmd(path: Path) -> list[dict]:
    text = path.read_text(encoding="utf-8")
    blocks = _split_blocks(text)
    results = []
    skipped = 0
    for block in blocks:
        parsed = _parse_block(block)
        if parsed:
            results.append(parsed)
        else:
            skipped += 1
    if skipped:
        print(f"  파싱 실패 (번호·선택지 없음): {skipped}개 블록 스킵")
    return results


# ── 병합 ──────────────────────────────────────────────────────────────────────

def merge(new_qs: list[dict]) -> tuple[list[dict], int]:
    existing: list[dict] = []
    if RAW_JSON.exists():
        existing = json.loads(RAW_JSON.read_text(encoding="utf-8"))
    existing_ids = {q["source_id"] for q in existing}

    added = 0
    for q in new_qs:
        if q["source_id"] not in existing_ids:
            existing.append(q)
            existing_ids.add(q["source_id"])
            added += 1
        else:
            # 기존 항목 업데이트 (answer/explanation 보강)
            for i, e in enumerate(existing):
                if e["source_id"] == q["source_id"]:
                    if q["answer"] and not e.get("answer"):
                        existing[i]["answer"] = q["answer"]
                    if q["explanation"] and not e.get("explanation"):
                        existing[i]["explanation"] = q["explanation"]
                    break

    RAW_JSON.parent.mkdir(exist_ok=True)
    RAW_JSON.write_text(json.dumps(existing, ensure_ascii=False, indent=2), encoding="utf-8")
    return existing, added


# ── 진입점 ────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="Q.md → questions_raw.json 마이그레이션")
    parser.add_argument("--file", type=Path, default=DEFAULT_QMD, help="입력 Q.md 파일")
    parser.add_argument("--dry-run", action="store_true", help="파싱 결과만 출력 (저장 안 함)")
    args = parser.parse_args()

    if not args.file.exists():
        print(f"파일 없음: {args.file}")
        print("Q.md 파일을 프로젝트 루트에 만들고 ExamTopics 문제를 붙여넣으세요.")
        sys.exit(1)

    print(f"파싱 중: {args.file}")
    questions = parse_qmd(args.file)
    print(f"파싱 완료: {len(questions)}개 문제 인식")

    if args.dry_run:
        for q in questions:
            print(f"  [{q['source_id']}] {q['question'][:80]}…")
            print(f"    보기: {list(q['choices'].keys())}  정답: {q['answer']}")
        return

    all_qs, added = merge(questions)
    print(f"questions_raw.json 업데이트: +{added}개 추가 (총 {len(all_qs)}개)")

    # 로컬 DB 재시드
    print("로컬 DB 재시드 중...")
    from db.seed import seed
    seed(RAW_JSON)
    print("완료! Railway 반영: railway up")


if __name__ == "__main__":
    main()
