"""스크래핑된 JSON → SQLite DB 적재.

사용법:
    uv run aws-saa-seed
    uv run aws-saa-seed --file data/questions_raw.json
"""

import argparse
import json
import sqlite3
from pathlib import Path

from db.schema import get_connection, init_db
from domain_mapping.classifier import classify_domain, domain_name

DEFAULT_RAW = Path(__file__).parent.parent / "data" / "questions_raw.json"


def seed(raw_file: Path = DEFAULT_RAW) -> None:
    if not raw_file.exists():
        raise FileNotFoundError(
            f"스크래핑 결과 파일 없음: {raw_file}\n"
            "먼저 `uv run aws-saa-scrape` 를 실행하세요."
        )

    init_db()
    conn = get_connection()

    data: list[dict] = json.loads(raw_file.read_text(encoding="utf-8"))
    inserted = skipped = 0

    for q in data:
        if not q.get("source_id") or not q.get("question") or not q.get("choices"):
            skipped += 1
            continue

        # 도메인 분류: 문제 + 보기 전체 텍스트로 판단
        full_text = q["question"] + " " + " ".join(q["choices"].values())
        domain = classify_domain(full_text)

        try:
            conn.execute(
                """
                INSERT INTO questions
                    (source_id, question, choices, answer, explanation,
                     votes, domain, domain_name, services)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(source_id) DO UPDATE SET
                    question    = excluded.question,
                    choices     = excluded.choices,
                    answer      = excluded.answer,
                    explanation = excluded.explanation,
                    votes       = excluded.votes,
                    domain      = excluded.domain,
                    domain_name = excluded.domain_name,
                    services    = excluded.services
                """,
                (
                    q["source_id"],
                    q["question"],
                    json.dumps(q["choices"], ensure_ascii=False),
                    json.dumps(q.get("answer", []), ensure_ascii=False),
                    q.get("explanation", ""),
                    json.dumps(q.get("votes", []), ensure_ascii=False),
                    domain,
                    domain_name(domain),
                    json.dumps([]),
                ),
            )
            inserted += 1
        except sqlite3.Error as e:
            print(f"  삽입 오류 ({q['source_id']}): {e}")
            skipped += 1

    conn.commit()
    conn.close()
    print(f"DB 적재 완료: {inserted}개 삽입, {skipped}개 건너뜀")


def main() -> None:
    parser = argparse.ArgumentParser(description="스크래핑 JSON → DB 적재")
    parser.add_argument("--file", type=Path, default=DEFAULT_RAW, help="입력 JSON 파일")
    args = parser.parse_args()
    seed(args.file)


if __name__ == "__main__":
    main()
