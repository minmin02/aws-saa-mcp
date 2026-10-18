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


@mcp.custom_route("/admin/api/stats", methods=["GET"])
async def _admin_stats(request) -> "Response":
    from starlette.responses import JSONResponse
    conn = get_connection()
    domains = []
    for d, name in DOMAIN_NAMES.items():
        total = conn.execute("SELECT COUNT(*) FROM questions WHERE domain=?", (d,)).fetchone()[0]
        seen = conn.execute(
            "SELECT COUNT(DISTINCT up.question_id) FROM user_progress up "
            "JOIN questions q ON q.id=up.question_id WHERE q.domain=?", (d,)
        ).fetchone()[0]
        row = conn.execute(
            "SELECT SUM(up.is_correct) c, COUNT(*) t FROM user_progress up "
            "JOIN questions q ON q.id=up.question_id WHERE q.domain=?", (d,)
        ).fetchone()
        c, t = (row["c"] or 0), (row["t"] or 0)
        domains.append({"domain": d, "name": name, "total": total, "seen": seen,
                        "unseen": total - seen, "accuracy": round(c/t*100, 1) if t else 0})
    total_q = conn.execute("SELECT COUNT(*) FROM questions").fetchone()[0]
    seen_q = conn.execute("SELECT COUNT(DISTINCT question_id) FROM user_progress").fetchone()[0]
    row = conn.execute("SELECT SUM(is_correct) c, COUNT(*) t FROM user_progress").fetchone()
    c, t = (row["c"] or 0), (row["t"] or 0)
    conn.close()
    return JSONResponse({"domains": domains, "total": total_q, "seen": seen_q,
                         "accuracy": round(c/t*100, 1) if t else 0})


@mcp.custom_route("/admin/api/questions", methods=["GET"])
async def _admin_questions(request) -> "Response":
    from starlette.responses import JSONResponse
    domain = request.query_params.get("domain")
    page = int(request.query_params.get("page", 1))
    per_page = 20
    offset = (page - 1) * per_page
    conn = get_connection()
    where = "WHERE domain=?" if domain else ""
    params = [int(domain)] if domain else []
    total = conn.execute(f"SELECT COUNT(*) FROM questions {where}", params).fetchone()[0]
    rows = conn.execute(
        f"SELECT id, source_id, domain, domain_name, "
        f"substr(question,1,120) AS q_preview, answer FROM questions {where} "
        f"ORDER BY domain, id LIMIT ? OFFSET ?",
        params + [per_page, offset]
    ).fetchall()
    conn.close()
    return JSONResponse({
        "total": total, "page": page, "per_page": per_page,
        "questions": [dict(r) for r in rows]
    })


_ADMIN_HTML = """<!DOCTYPE html>
<html lang="ko">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>AWS SAA 문제집 어드민</title>
<style>
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;background:#f0f2f5;color:#1a1a2e}
.header{background:linear-gradient(135deg,#1a1a2e,#16213e);color:#fff;padding:24px 32px}
.header h1{font-size:1.4rem;font-weight:700}
.header p{font-size:.85rem;opacity:.7;margin-top:4px}
.container{max-width:1100px;margin:0 auto;padding:24px 16px}
.stats-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(200px,1fr));gap:16px;margin-bottom:28px}
.stat-card{background:#fff;border-radius:12px;padding:20px;box-shadow:0 1px 4px rgba(0,0,0,.08)}
.stat-card .label{font-size:.75rem;color:#888;text-transform:uppercase;letter-spacing:.05em}
.stat-card .value{font-size:2rem;font-weight:700;color:#1a1a2e;margin:4px 0}
.stat-card .sub{font-size:.8rem;color:#aaa}
.domain-table{background:#fff;border-radius:12px;padding:20px;box-shadow:0 1px 4px rgba(0,0,0,.08);margin-bottom:28px}
.domain-table h2{font-size:1rem;font-weight:600;margin-bottom:14px;color:#444}
table{width:100%;border-collapse:collapse;font-size:.85rem}
th{text-align:left;padding:8px 12px;background:#f8f9fb;color:#888;font-weight:600;font-size:.75rem;text-transform:uppercase}
td{padding:10px 12px;border-top:1px solid #f0f0f0}
tr:hover td{background:#fafbff}
.badge{display:inline-block;padding:2px 8px;border-radius:99px;font-size:.72rem;font-weight:600}
.badge-1{background:#e8f4fd;color:#1a73e8}
.badge-2{background:#e6f4ea;color:#188038}
.badge-3{background:#fef7e0;color:#b06000}
.badge-4{background:#fce8e6;color:#c5221f}
.bar-bg{background:#eee;border-radius:99px;height:6px;width:100px;display:inline-block;vertical-align:middle}
.bar-fill{background:#4285f4;border-radius:99px;height:6px}
.q-section{background:#fff;border-radius:12px;padding:20px;box-shadow:0 1px 4px rgba(0,0,0,.08)}
.q-section h2{font-size:1rem;font-weight:600;margin-bottom:14px;color:#444}
.filter-row{display:flex;gap:10px;margin-bottom:14px;flex-wrap:wrap}
select,button{padding:7px 14px;border-radius:8px;border:1px solid #ddd;font-size:.85rem;cursor:pointer}
button{background:#4285f4;color:#fff;border-color:#4285f4}
button:hover{background:#3367d6}
.q-row{padding:10px 0;border-top:1px solid #f0f0f0;font-size:.83rem}
.q-meta{color:#888;font-size:.75rem;margin-bottom:3px}
.pagination{display:flex;gap:8px;margin-top:14px;align-items:center;font-size:.85rem}
.pagination button{padding:5px 12px;font-size:.8rem}
.pagination .page-info{color:#888}
#status{text-align:center;padding:40px;color:#aaa}
</style>
</head>
<body>
<div class="header">
  <h1>AWS SAA-C03 문제집 어드민</h1>
  <p>도메인별 문제 현황 및 학습 진도</p>
</div>
<div class="container">
  <div class="stats-grid" id="top-stats">
    <div class="stat-card"><div class="label">전체 문제</div><div class="value" id="s-total">-</div><div class="sub">총 문제 수</div></div>
    <div class="stat-card"><div class="label">풀이 완료</div><div class="value" id="s-seen">-</div><div class="sub">유니크 문제 기준</div></div>
    <div class="stat-card"><div class="label">전체 정답률</div><div class="value" id="s-acc">-</div><div class="sub">누적 시도 기준</div></div>
    <div class="stat-card"><div class="label">남은 문제</div><div class="value" id="s-remain">-</div><div class="sub">미풀이 문제</div></div>
  </div>
  <div class="domain-table">
    <h2>도메인별 현황</h2>
    <table>
      <thead><tr><th>도메인</th><th>문제 수</th><th>풀이</th><th>미풀이</th><th>정답률</th><th>진도</th></tr></thead>
      <tbody id="domain-tbody"></tbody>
    </table>
  </div>
  <div class="q-section">
    <h2>문제 목록</h2>
    <div class="filter-row">
      <select id="domain-filter">
        <option value="">전체 도메인</option>
        <option value="1">1. 보안 (30%)</option>
        <option value="2">2. 복원력 (26%)</option>
        <option value="3">3. 고성능 (24%)</option>
        <option value="4">4. 비용최적화 (20%)</option>
      </select>
      <button onclick="loadQ(1)">조회</button>
    </div>
    <div id="q-list"><div id="status">로딩 중...</div></div>
    <div class="pagination" id="pagination" style="display:none">
      <button id="btn-prev" onclick="changePage(-1)">◀ 이전</button>
      <span class="page-info" id="page-info"></span>
      <button id="btn-next" onclick="changePage(1)">다음 ▶</button>
    </div>
  </div>
</div>
<script>
let currentPage=1, totalPages=1;
const BADGE=['','badge-1','badge-2','badge-3','badge-4'];
async function loadStats(){
  const r=await fetch('/admin/api/stats');
  const d=await r.json();
  document.getElementById('s-total').textContent=d.total;
  document.getElementById('s-seen').textContent=d.seen;
  document.getElementById('s-acc').textContent=d.accuracy+'%';
  document.getElementById('s-remain').textContent=d.total-d.seen;
  const tb=document.getElementById('domain-tbody');
  tb.innerHTML=d.domains.map(dm=>{
    const pct=dm.total?Math.round(dm.seen/dm.total*100):0;
    return `<tr>
      <td><span class="badge ${BADGE[dm.domain]}">D${dm.domain}</span> ${dm.name}</td>
      <td><b>${dm.total}</b></td><td>${dm.seen}</td><td>${dm.unseen}</td>
      <td>${dm.accuracy}%</td>
      <td><div class="bar-bg"><div class="bar-fill" style="width:${pct}%"></div></div> ${pct}%</td>
    </tr>`;
  }).join('');
}
async function loadQ(page){
  currentPage=page;
  const domain=document.getElementById('domain-filter').value;
  const url='/admin/api/questions?page='+page+(domain?'&domain='+domain:'');
  const r=await fetch(url);
  const d=await r.json();
  totalPages=Math.ceil(d.total/d.per_page)||1;
  const list=document.getElementById('q-list');
  if(!d.questions.length){list.innerHTML='<div id="status">문제가 없습니다.</div>';document.getElementById('pagination').style.display='none';return;}
  list.innerHTML=d.questions.map(q=>`
    <div class="q-row">
      <div class="q-meta"><span class="badge ${BADGE[q.domain]}">D${q.domain}</span> #${q.id} · ${q.source_id} · 정답: ${JSON.parse(q.answer).join(',')}</div>
      <div>${q.q_preview}${q.q_preview.length>=120?'…':''}</div>
    </div>`).join('');
  const pg=document.getElementById('pagination');
  pg.style.display='flex';
  document.getElementById('page-info').textContent=`${currentPage} / ${totalPages} 페이지 (총 ${d.total}문제)`;
  document.getElementById('btn-prev').disabled=currentPage<=1;
  document.getElementById('btn-next').disabled=currentPage>=totalPages;
}
function changePage(delta){if(currentPage+delta>=1&&currentPage+delta<=totalPages)loadQ(currentPage+delta);}
loadStats();loadQ(1);
</script>
</body>
</html>"""


@mcp.custom_route("/admin", methods=["GET"])
async def _admin(request) -> "Response":
    from starlette.responses import HTMLResponse
    return HTMLResponse(_ADMIN_HTML)


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
