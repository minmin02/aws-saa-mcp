"""sentence-transformers 기반 임베딩 생성 및 코사인 유사도 검색.

사용법:
    uv run aws-saa-embed          # DB의 모든 문제에 임베딩 생성
"""

import json
import struct
from typing import Optional

import numpy as np

from db.schema import get_connection

MODEL_NAME = "sentence-transformers/all-MiniLM-L6-v2"
EMBED_DIM = 384

_model = None


def _get_model():
    global _model
    if _model is None:
        from sentence_transformers import SentenceTransformer
        print(f"모델 로딩: {MODEL_NAME}")
        _model = SentenceTransformer(MODEL_NAME)
    return _model


def embed(text: str) -> np.ndarray:
    return _get_model().encode(text, normalize_embeddings=True).astype(np.float32)


def _vec_to_blob(vec: np.ndarray) -> bytes:
    return struct.pack(f"{len(vec)}f", *vec)


def _blob_to_vec(blob: bytes) -> np.ndarray:
    n = len(blob) // 4
    return np.array(struct.unpack(f"{n}f", blob), dtype=np.float32)


def build_index() -> None:
    """임베딩이 없는 모든 문제에 대해 벡터 생성 후 DB 저장."""
    conn = get_connection()
    rows = conn.execute(
        "SELECT id, question, choices FROM questions WHERE embedding IS NULL"
    ).fetchall()

    if not rows:
        print("임베딩이 없는 문제 없음 (이미 완료)")
        conn.close()
        return

    print(f"임베딩 생성 시작: {len(rows)}개 문제")
    for i, row in enumerate(rows):
        choices_text = " ".join(json.loads(row["choices"]).values())
        text = row["question"] + " " + choices_text
        vec = embed(text)
        conn.execute(
            "UPDATE questions SET embedding = ? WHERE id = ?",
            (_vec_to_blob(vec), row["id"]),
        )
        if (i + 1) % 100 == 0:
            conn.commit()
            print(f"  {i + 1}/{len(rows)} 완료")

    conn.commit()
    conn.close()
    print("임베딩 인덱스 완료")


def search_similar(
    query: str,
    n: int = 5,
    domain: Optional[int] = None,
) -> list[dict]:
    """쿼리와 코사인 유사도가 높은 n개 문제를 반환한다."""
    q_vec = embed(query)
    conn = get_connection()

    where = "WHERE embedding IS NOT NULL"
    params: list = []
    if domain:
        where += " AND domain = ?"
        params.append(domain)

    rows = conn.execute(
        f"""
        SELECT id, source_id, question, choices, answer, explanation,
               domain, domain_name, embedding
        FROM questions {where}
        """,
        params,
    ).fetchall()
    conn.close()

    if not rows:
        return []

    scored = []
    for row in rows:
        vec = _blob_to_vec(row["embedding"])
        score = float(np.dot(q_vec, vec))
        scored.append((score, row))

    scored.sort(key=lambda x: x[0], reverse=True)

    return [
        {
            "id": row["id"],
            "source_id": row["source_id"],
            "question": row["question"],
            "choices": json.loads(row["choices"]),
            "answer": json.loads(row["answer"]),
            "explanation": row["explanation"],
            "domain": row["domain"],
            "domain_name": row["domain_name"],
            "similarity": round(score, 4),
        }
        for score, row in scored[:n]
    ]


if __name__ == "__main__":
    build_index()
