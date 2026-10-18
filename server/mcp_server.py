"""AWS SAA-C03 문제집 MCP 서버 (FastMCP).

5개 tool:
  - get_question    : 문제 출제 (랜덤 / 도메인별 / 미학습 우선)
  - check_answer    : 채점 + 해설 반환 + 학습 기록 저장
  - list_domains    : 4개 도메인별 문제 수 및 정답률 통계
  - search_similar  : 임베딩 기반 유사 문제 검색
  - get_progress    : 전체 학습 진척도 요약

실행 모드:
  stdio (기본 / Claude Desktop):  python -m server.mcp_server
  HTTP  (원격 배포):               MCP_TRANSPORT=http python -m server.mcp_server
"""

import json
import os
import random
from typing import Optional

from mcp.server.mcpserver import MCPServer

from db.schema import get_connection, init_db
from domain_mapping.classifier import DOMAIN_NAMES

mcp = MCPServer(
    "aws-saa-question-bank",
    instructions=(
        "AWS SAA-C03 시험 대비 문제집입니다. "
        "도메인: 1=보안(30%), 2=복원력(26%), 3=고성능(24%), 4=비용최적화(20%). "
        "get_question으로 문제를 받고 check_answer로 채점하세요."
    ),
)


@mcp.custom_route("/health", methods=["GET"])
async def _health(request) -> "Response":
    from starlette.responses import JSONResponse
    return JSONResponse({"status": "ok"})


# ── 내부 헬퍼 ──────────────────────────────────────────────────────────────

def _row_to_question(row) -> dict:
    return {
        "id": row["id"],
        "source_id": row["source_id"],
        "question": row["question"],
        "choices": json.loads(row["choices"]),
        "domain": row["domain"],
        "domain_name": row["domain_name"],
    }


# ── MCP Tools ─────────────────────────────────────────────────────────────

@mcp.tool()
def get_question(
    domain: Optional[int] = None,
    exclude_seen: bool = False,
    count: int = 1,
) -> list[dict]:
    """SAA-C03 시험 문제를 출제합니다.

    Args:
        domain: 도메인 번호. 1=보안, 2=복원력, 3=고성능, 4=비용최적화. None이면 전체 랜덤.
        exclude_seen: True면 아직 한 번도 풀지 않은 문제만 출제합니다.
        count: 반환할 문제 수 (기본 1, 최대 10).
    """
    count = min(count, 10)
    conn = get_connection()

    where_parts: list[str] = []
    params: list = []

    if domain is not None:
        where_parts.append("q.domain = ?")
        params.append(domain)

    if exclude_seen:
        where_parts.append(
            "q.id NOT IN (SELECT DISTINCT question_id FROM user_progress)"
        )

    where = ("WHERE " + " AND ".join(where_parts)) if where_parts else ""
    rows = conn.execute(
        f"SELECT id, source_id, question, choices, domain, domain_name "
        f"FROM questions q {where}",
        params,
    ).fetchall()
    conn.close()

    if not rows:
        return []

    sample = random.sample(list(rows), min(count, len(rows)))
    return [_row_to_question(r) for r in sample]


@mcp.tool()
def check_answer(question_id: int, answers: list[str]) -> dict:
    """답안을 채점하고 정답·해설을 반환합니다. 결과는 학습 기록에 자동 저장됩니다.

    Args:
        question_id: get_question이 반환한 문제의 id.
        answers: 선택한 보기 목록 (예: ["A"] 또는 ["A", "C"]).
    """
    conn = get_connection()
    row = conn.execute(
        "SELECT id, answer, explanation, choices, votes FROM questions WHERE id = ?",
        (question_id,),
    ).fetchone()

    if not row:
        conn.close()
        return {"error": f"문제 ID {question_id}를 찾을 수 없습니다."}

    correct_answers = sorted(json.loads(row["answer"]))
    user_answers = sorted(a.strip().upper() for a in answers if a.strip())
    is_correct = correct_answers == user_answers

    conn.execute(
        "INSERT INTO user_progress (question_id, user_answer, is_correct) VALUES (?, ?, ?)",
        (question_id, json.dumps(user_answers), int(is_correct)),
    )
    conn.commit()
    conn.close()

    return {
        "is_correct": is_correct,
        "user_answers": user_answers,
        "correct_answers": correct_answers,
        "explanation": row["explanation"] or "해설이 없습니다.",
        "community_votes": json.loads(row["votes"] or "[]"),
    }


@mcp.tool()
def list_domains() -> list[dict]:
    """SAA-C03 4개 도메인별 문제 수, 풀이 수, 정답률을 반환합니다."""
    conn = get_connection()
    stats = []

    for domain_num, name in DOMAIN_NAMES.items():
        total = conn.execute(
            "SELECT COUNT(*) FROM questions WHERE domain = ?", (domain_num,)
        ).fetchone()[0]

        seen = conn.execute(
            """
            SELECT COUNT(DISTINCT up.question_id)
            FROM user_progress up
            JOIN questions q ON q.id = up.question_id
            WHERE q.domain = ?
            """,
            (domain_num,),
        ).fetchone()[0]

        row_acc = conn.execute(
            """
            SELECT
                SUM(up.is_correct)  AS correct,
                COUNT(*)            AS attempts
            FROM user_progress up
            JOIN questions q ON q.id = up.question_id
            WHERE q.domain = ?
            """,
            (domain_num,),
        ).fetchone()
        correct  = row_acc["correct"]  or 0
        attempts = row_acc["attempts"] or 0

        stats.append({
            "domain": domain_num,
            "domain_name": name,
            "total_questions": total,
            "seen": seen,
            "unseen": total - seen,
            "accuracy_pct": round(correct / attempts * 100, 1) if attempts else 0.0,
        })

    conn.close()
    return stats


@mcp.tool()
def search_similar(
    query: str,
    domain: Optional[int] = None,
    n: int = 5,
) -> list[dict]:
    """키워드나 문장으로 의미적으로 유사한 문제를 검색합니다 (임베딩 기반).

    Args:
        query: 검색할 키워드 또는 문장 (예: "S3 버킷 암호화 설정").
        domain: 특정 도메인(1-4) 내에서만 검색. None이면 전체 검색.
        n: 반환할 최대 문제 수 (기본 5).
    """
    from embeddings.search import search_similar as _search
    results = _search(query, n=n, domain=domain)
    if not results:
        return [{"message": "임베딩 인덱스가 없습니다. `uv run aws-saa-embed`를 먼저 실행하세요."}]
    return results


@mcp.tool()
def get_progress() -> dict:
    """전체 학습 진척도 요약을 반환합니다."""
    conn = get_connection()

    total_q = conn.execute("SELECT COUNT(*) FROM questions").fetchone()[0]
    seen = conn.execute(
        "SELECT COUNT(DISTINCT question_id) FROM user_progress"
    ).fetchone()[0]
    row = conn.execute(
        "SELECT SUM(is_correct) AS c, COUNT(*) AS t FROM user_progress"
    ).fetchone()
    correct  = row["c"] or 0
    attempts = row["t"] or 0
    conn.close()

    return {
        "total_questions": total_q,
        "attempted_unique": seen,
        "remaining": total_q - seen,
        "total_attempts": attempts,
        "overall_accuracy_pct": round(correct / attempts * 100, 1) if attempts else 0.0,
        "completion_pct": round(seen / total_q * 100, 1) if total_q else 0.0,
    }


# ── 진입점 ────────────────────────────────────────────────────────────────

def _build_http_app():
    """Bearer 토큰 인증이 적용된 MCP Starlette 앱을 반환한다.
    /health 는 custom_route로 등록되어 인증 없이 접근 가능.
    """
    from starlette.middleware.base import BaseHTTPMiddleware
    from starlette.requests import Request
    from starlette.responses import Response

    secret = os.environ.get("MCP_SECRET_TOKEN", "")

    class _BearerAuth(BaseHTTPMiddleware):
        async def dispatch(self, request: Request, call_next):
            if secret and request.url.path != "/health":
                auth = request.headers.get("Authorization", "")
                if auth != f"Bearer {secret}":
                    return Response("Unauthorized", status_code=401)
            return await call_next(request)

    app = mcp.streamable_http_app(host="0.0.0.0", stateless_http=True)
    if secret:
        app.add_middleware(_BearerAuth)
    return app


def main() -> None:
    """stdio 모드 — Claude Desktop 로컬 연결용."""
    init_db()
    mcp.run()


def main_http() -> None:
    """HTTP 모드 — Railway/Render 원격 배포용."""
    import uvicorn
    from pathlib import Path

    init_db()

    # DB가 비어 있고 questions_raw.json이 있으면 자동 시드
    from db.schema import DB_PATH, get_connection as _gc
    from db.seed import DEFAULT_RAW
    conn = _gc()
    count = conn.execute("SELECT COUNT(*) FROM questions").fetchone()[0]
    conn.close()
    if count == 0:
        raw = DB_PATH.parent / "questions_raw.json"
        if not raw.exists():
            raw = DEFAULT_RAW
        if raw.exists():
            print(f"DB가 비어 있습니다. {raw} 에서 자동 시드합니다...")
            from db.seed import seed
            seed(raw)

    port = int(os.environ.get("PORT", 8000))
    app = _build_http_app()
    uvicorn.run(app, host="0.0.0.0", port=port)


if __name__ == "__main__":
    transport = os.environ.get("MCP_TRANSPORT", "stdio")
    if transport == "http":
        main_http()
    else:
        main()
