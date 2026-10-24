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
        f"SELECT id, source_id, domain, domain_name, question, answer FROM questions {where} "
        f"ORDER BY domain, id LIMIT ? OFFSET ?",
        params + [per_page, offset]
    ).fetchall()
    conn.close()
    return JSONResponse({
        "total": total, "page": page, "per_page": per_page,
        "questions": [dict(r) for r in rows]
    })


@mcp.custom_route("/admin/api/question/{qid}", methods=["GET"])
async def _admin_question_detail(request) -> "Response":
    from starlette.responses import JSONResponse
    qid = request.path_params["qid"]
    conn = get_connection()
    row = conn.execute(
        "SELECT id, source_id, domain, domain_name, question, choices, answer, explanation, votes "
        "FROM questions WHERE id=?", (qid,)
    ).fetchone()
    conn.close()
    if not row:
        return JSONResponse({"error": "not found"}, status_code=404)
    d = dict(row)
    d["choices"] = json.loads(d["choices"])
    d["answer"] = json.loads(d["answer"])
    d["votes"] = json.loads(d["votes"] or "[]")
    return JSONResponse(d)


@mcp.custom_route("/admin/api/check", methods=["POST"])
async def _admin_check(request) -> "Response":
    from starlette.responses import JSONResponse
    body = await request.json()
    qid = body.get("question_id")
    answers = body.get("answers", [])
    conn = get_connection()
    row = conn.execute(
        "SELECT id, answer, explanation, choices, votes FROM questions WHERE id=?", (qid,)
    ).fetchone()
    if not row:
        conn.close()
        return JSONResponse({"error": "not found"}, status_code=404)
    correct = sorted(json.loads(row["answer"]))
    user = sorted(a.strip().upper() for a in answers if a.strip())
    is_correct = correct == user
    conn.execute(
        "INSERT INTO user_progress (question_id, user_answer, is_correct) VALUES (?,?,?)",
        (qid, json.dumps(user), int(is_correct)),
    )
    conn.commit()
    conn.close()
    return JSONResponse({
        "is_correct": is_correct,
        "correct_answers": correct,
        "user_answers": user,
        "explanation": row["explanation"] or "",
        "votes": json.loads(row["votes"] or "[]"),
    })


_ADMIN_HTML = """<!DOCTYPE html>
<html lang="ko">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>AWS SAA-C03 문제집</title>
<style>
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;background:#f0f2f5;color:#1a1a2e}
.header{background:linear-gradient(135deg,#1a1a2e,#16213e);color:#fff;padding:20px 32px;display:flex;align-items:center;gap:20px}
.header h1{font-size:1.3rem;font-weight:700}
.tabs{display:flex;gap:4px;margin-left:auto}
.tab{padding:7px 18px;border-radius:8px;font-size:.85rem;cursor:pointer;border:1px solid rgba(255,255,255,.2);background:transparent;color:#fff;transition:.15s}
.tab.active,.tab:hover{background:rgba(255,255,255,.15)}
.container{max-width:1100px;margin:0 auto;padding:24px 16px}
.panel{display:none}.panel.active{display:block}
/* 통계 */
.stats-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:14px;margin-bottom:24px}
.stat-card{background:#fff;border-radius:12px;padding:18px;box-shadow:0 1px 4px rgba(0,0,0,.08)}
.stat-card .lbl{font-size:.72rem;color:#888;text-transform:uppercase;letter-spacing:.05em}
.stat-card .val{font-size:1.9rem;font-weight:700;color:#1a1a2e;margin:4px 0}
.card-box{background:#fff;border-radius:12px;padding:20px;box-shadow:0 1px 4px rgba(0,0,0,.08);margin-bottom:20px}
.card-box h2{font-size:.95rem;font-weight:600;margin-bottom:14px;color:#444}
table{width:100%;border-collapse:collapse;font-size:.84rem}
th{text-align:left;padding:8px 12px;background:#f8f9fb;color:#888;font-weight:600;font-size:.73rem;text-transform:uppercase}
td{padding:10px 12px;border-top:1px solid #f0f0f0}
/* 배지 */
.badge{display:inline-block;padding:2px 8px;border-radius:99px;font-size:.72rem;font-weight:600}
.d1{background:#e8f4fd;color:#1a73e8}.d2{background:#e6f4ea;color:#188038}
.d3{background:#fef7e0;color:#b06000}.d4{background:#fce8e6;color:#c5221f}
.bar-bg{background:#eee;border-radius:99px;height:6px;width:90px;display:inline-block;vertical-align:middle}
.bar-fill{background:#4285f4;border-radius:99px;height:6px}
/* 목록 */
.filter-row{display:flex;gap:8px;margin-bottom:14px;flex-wrap:wrap;align-items:center}
select{padding:7px 12px;border-radius:8px;border:1px solid #ddd;font-size:.84rem}
.btn{padding:7px 16px;border-radius:8px;border:none;font-size:.84rem;cursor:pointer;background:#4285f4;color:#fff;transition:.15s}
.btn:hover{background:#3367d6}
.btn-ghost{background:#f1f3f4;color:#444;border:1px solid #ddd}
.btn-ghost:hover{background:#e8eaed}
.btn-sm{padding:4px 12px;font-size:.78rem}
.q-row{padding:12px 0;border-top:1px solid #f0f0f0}
.q-meta{color:#888;font-size:.73rem;margin-bottom:5px;display:flex;gap:8px;align-items:center}
.q-text{font-size:.85rem;line-height:1.5;color:#333}
.pg{display:flex;gap:8px;margin-top:14px;align-items:center;font-size:.84rem}
.pg-info{color:#888;font-size:.8rem}
/* 연습 모드 */
.quiz-wrap{max-width:780px;margin:0 auto}
.quiz-card{background:#fff;border-radius:16px;padding:32px;box-shadow:0 2px 8px rgba(0,0,0,.1)}
.quiz-meta{font-size:.75rem;color:#888;margin-bottom:16px;display:flex;gap:10px}
.quiz-q{font-size:1rem;line-height:1.7;color:#1a1a2e;margin-bottom:24px;font-weight:500}
.choice-list{display:flex;flex-direction:column;gap:10px;margin-bottom:24px}
.choice{display:flex;align-items:flex-start;gap:12px;padding:14px 16px;border-radius:10px;border:2px solid #e8eaed;cursor:pointer;transition:.15s;font-size:.88rem;line-height:1.5}
.choice:hover{border-color:#4285f4;background:#f0f4ff}
.choice.selected{border-color:#4285f4;background:#e8f0fe}
.choice.correct{border-color:#34a853;background:#e6f4ea}
.choice.wrong{border-color:#ea4335;background:#fce8e6}
.choice-letter{font-weight:700;min-width:22px;color:#4285f4;font-size:.9rem}
.result-box{padding:16px 20px;border-radius:10px;margin-bottom:20px;font-size:.88rem;line-height:1.6}
.result-box.ok{background:#e6f4ea;border:1px solid #34a853;color:#1e6e34}
.result-box.ng{background:#fce8e6;border:1px solid #ea4335;color:#7b1c14}
.exp-box{background:#f8f9fa;border-radius:10px;padding:16px;font-size:.84rem;color:#555;line-height:1.6;margin-bottom:20px}
.votes-row{display:flex;gap:8px;flex-wrap:wrap;margin-bottom:20px}
.vote-chip{font-size:.75rem;padding:3px 10px;border-radius:99px;background:#e8eaed;color:#555}
.quiz-actions{display:flex;gap:10px;justify-content:flex-end}
.domain-sel{display:flex;gap:8px;flex-wrap:wrap;margin-bottom:20px}
.ds-btn{padding:8px 18px;border-radius:99px;border:2px solid #e8eaed;font-size:.83rem;cursor:pointer;background:#fff;transition:.15s}
.ds-btn.active{border-color:#4285f4;background:#e8f0fe;color:#4285f4;font-weight:600}
#empty{text-align:center;padding:60px;color:#aaa;font-size:.9rem}
</style>
</head>
<body>
<div class="header">
  <h1>AWS SAA-C03 문제집</h1>
  <div class="tabs">
    <button class="tab active" onclick="showTab('dash')">대시보드</button>
    <button class="tab" onclick="showTab('list')">문제 목록</button>
    <button class="tab" onclick="showTab('quiz')">연습 모드</button>
  </div>
</div>
<div class="container">

<!-- ── 대시보드 ── -->
<div id="tab-dash" class="panel active">
  <div class="stats-grid">
    <div class="stat-card"><div class="lbl">전체 문제</div><div class="val" id="s-total">-</div></div>
    <div class="stat-card"><div class="lbl">풀이 완료</div><div class="val" id="s-seen">-</div></div>
    <div class="stat-card"><div class="lbl">전체 정답률</div><div class="val" id="s-acc">-</div></div>
    <div class="stat-card"><div class="lbl">남은 문제</div><div class="val" id="s-remain">-</div></div>
  </div>
  <div class="card-box">
    <h2>도메인별 현황</h2>
    <table>
      <thead><tr><th>도메인</th><th>문제</th><th>풀이</th><th>미풀이</th><th>정답률</th><th>진도</th></tr></thead>
      <tbody id="domain-tbody"></tbody>
    </table>
  </div>
</div>

<!-- ── 문제 목록 ── -->
<div id="tab-list" class="panel">
  <div class="card-box">
    <div class="filter-row">
      <select id="domain-filter">
        <option value="">전체</option>
        <option value="1">D1 보안</option>
        <option value="2">D2 복원력</option>
        <option value="3">D3 고성능</option>
        <option value="4">D4 비용최적화</option>
      </select>
      <button class="btn" onclick="loadQ(1)">조회</button>
    </div>
    <div id="q-list"><div id="empty">로딩 중...</div></div>
    <div class="pg" id="pagination" style="display:none">
      <button class="btn btn-ghost btn-sm" id="btn-prev" onclick="changePage(-1)">◀ 이전</button>
      <span class="pg-info" id="page-info"></span>
      <button class="btn btn-ghost btn-sm" id="btn-next" onclick="changePage(1)">다음 ▶</button>
    </div>
  </div>
</div>

<!-- ── 연습 모드 ── -->
<div id="tab-quiz" class="panel">
  <div class="quiz-wrap">
    <div class="card-box" id="quiz-setup">
      <h2>연습 도메인 선택</h2>
      <div class="domain-sel" style="margin-top:12px">
        <button class="ds-btn active" data-d="0" onclick="selectDomain(0,this)">전체</button>
        <button class="ds-btn" data-d="1" onclick="selectDomain(1,this)">D1 보안</button>
        <button class="ds-btn" data-d="2" onclick="selectDomain(2,this)">D2 복원력</button>
        <button class="ds-btn" data-d="3" onclick="selectDomain(3,this)">D3 고성능</button>
        <button class="ds-btn" data-d="4" onclick="selectDomain(4,this)">D4 비용최적화</button>
      </div>
      <button class="btn" onclick="startQuiz()">시작</button>
    </div>
    <div id="quiz-area" style="display:none">
      <div class="quiz-card">
        <div class="quiz-meta" id="qz-meta"></div>
        <div class="quiz-q" id="qz-q"></div>
        <div class="choice-list" id="qz-choices"></div>
        <div id="qz-result" style="display:none">
          <div class="result-box" id="qz-result-box"></div>
          <div class="exp-box" id="qz-exp" style="display:none"></div>
          <div class="votes-row" id="qz-votes"></div>
        </div>
        <div class="quiz-actions">
          <button class="btn btn-ghost" id="btn-submit" onclick="submitAnswer()">제출</button>
          <button class="btn" id="btn-next-q" style="display:none" onclick="nextQuestion()">다음 문제 →</button>
        </div>
      </div>
    </div>
  </div>
</div>

</div>
<script>
const BADGE_CLS=['','d1','d2','d3','d4'];
let listPage=1,listTotal=1;
let quizDomain=0, quizPool=[], quizIdx=0, quizCurrent=null, quizMulti=false, quizSelected=new Set();

function showTab(t){
  document.querySelectorAll('.panel').forEach(p=>p.classList.remove('active'));
  document.querySelectorAll('.tab').forEach(b=>b.classList.remove('active'));
  document.getElementById('tab-'+t).classList.add('active');
  event.target.classList.add('active');
  if(t==='dash')loadStats();
  if(t==='list')loadQ(1);
}

// ── 대시보드 ──────────────────────────────────────────────────────────────
async function loadStats(){
  const d=await(await fetch('/admin/api/stats')).json();
  document.getElementById('s-total').textContent=d.total;
  document.getElementById('s-seen').textContent=d.seen;
  document.getElementById('s-acc').textContent=d.accuracy+'%';
  document.getElementById('s-remain').textContent=d.total-d.seen;
  document.getElementById('domain-tbody').innerHTML=d.domains.map(dm=>{
    const pct=dm.total?Math.round(dm.seen/dm.total*100):0;
    return`<tr>
      <td><span class="badge ${BADGE_CLS[dm.domain]}">D${dm.domain}</span> ${dm.name}</td>
      <td><b>${dm.total}</b></td><td>${dm.seen}</td><td>${dm.unseen}</td><td>${dm.accuracy}%</td>
      <td><div class="bar-bg"><div class="bar-fill" style="width:${pct}%"></div></div> ${pct}%</td>
    </tr>`;
  }).join('');
}

// ── 목록 ─────────────────────────────────────────────────────────────────
async function loadQ(page){
  listPage=page;
  const dom=document.getElementById('domain-filter').value;
  const d=await(await fetch('/admin/api/questions?page='+page+(dom?'&domain='+dom:''))).json();
  listTotal=Math.ceil(d.total/d.per_page)||1;
  const el=document.getElementById('q-list');
  if(!d.questions.length){el.innerHTML='<div id="empty">문제가 없습니다.</div>';document.getElementById('pagination').style.display='none';return;}
  el.innerHTML=d.questions.map(q=>{
    const ans=JSON.parse(q.answer);
    return`<div class="q-row">
      <div class="q-meta">
        <span class="badge ${BADGE_CLS[q.domain]}">D${q.domain}</span>
        <span>#${q.id} · ${q.source_id}</span>
        <span style="margin-left:auto">
          <button class="btn btn-ghost btn-sm" onclick="openQuizById(${q.id})">풀기</button>
        </span>
      </div>
      <div class="q-text">${q.question}</div>
    </div>`;
  }).join('');
  const pg=document.getElementById('pagination');
  pg.style.display='flex';
  document.getElementById('page-info').textContent=`${listPage} / ${listTotal} 페이지 (총 ${d.total}문제)`;
  document.getElementById('btn-prev').disabled=listPage<=1;
  document.getElementById('btn-next').disabled=listPage>=listTotal;
}
function changePage(d){if(listPage+d>=1&&listPage+d<=listTotal)loadQ(listPage+d);}

// ── 연습 공통 ─────────────────────────────────────────────────────────────
function selectDomain(d,el){
  quizDomain=d;
  document.querySelectorAll('.ds-btn').forEach(b=>b.classList.remove('active'));
  el.classList.add('active');
}

async function renderQuestion(q){
  quizCurrent=q;
  quizSelected=new Set();
  quizMulti=q.answer.length>1;
  document.getElementById('qz-meta').innerHTML=
    `<span class="badge ${BADGE_CLS[q.domain]}">D${q.domain} ${q.domain_name}</span>`+
    `<span>#${q.id} · ${q.source_id}</span>`+
    (quizMulti?'<span style="color:#e67700;font-weight:600">복수 정답</span>':'');
  document.getElementById('qz-q').textContent=q.question;
  document.getElementById('qz-choices').innerHTML=
    Object.entries(q.choices).map(([k,v])=>
      `<div class="choice" id="c-${k}" onclick="toggleChoice('${k}')">
        <span class="choice-letter">${k}</span><span>${v}</span>
      </div>`
    ).join('');
  document.getElementById('qz-result').style.display='none';
  document.getElementById('btn-submit').style.display='';
  document.getElementById('btn-next-q').style.display='none';
  document.getElementById('qz-votes').innerHTML='';
  document.getElementById('qz-exp').style.display='none';
}

function toggleChoice(k){
  if(document.getElementById('btn-submit').style.display==='none')return;
  if(quizMulti){
    if(quizSelected.has(k)){quizSelected.delete(k);document.getElementById('c-'+k).classList.remove('selected');}
    else{quizSelected.add(k);document.getElementById('c-'+k).classList.add('selected');}
  }else{
    quizSelected.forEach(s=>{const el=document.getElementById('c-'+s);if(el)el.classList.remove('selected');});
    quizSelected=new Set([k]);
    document.getElementById('c-'+k).classList.add('selected');
  }
}

async function submitAnswer(){
  if(!quizSelected.size){alert('보기를 선택하세요.');return;}
  const res=await(await fetch('/admin/api/check',{
    method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({question_id:quizCurrent.id,answers:[...quizSelected]})
  })).json();
  // 보기 색칠
  Object.keys(quizCurrent.choices).forEach(k=>{
    const el=document.getElementById('c-'+k);
    if(res.correct_answers.includes(k))el.classList.add('correct');
    else if(quizSelected.has(k))el.classList.add('wrong');
  });
  const rb=document.getElementById('qz-result-box');
  rb.className='result-box '+(res.is_correct?'ok':'ng');
  rb.textContent=res.is_correct?'정답입니다! ✓':'오답입니다. 정답: '+res.correct_answers.join(', ');
  if(res.explanation){
    const eb=document.getElementById('qz-exp');
    eb.textContent=res.explanation;eb.style.display='';
  }
  if(res.votes&&res.votes.length){
    document.getElementById('qz-votes').innerHTML=
      '<span style="font-size:.75rem;color:#888;margin-right:4px">커뮤니티:</span>'+
      res.votes.map(v=>`<span class="vote-chip">${v.option} ${v.pct}%</span>`).join('');
  }
  document.getElementById('qz-result').style.display='';
  document.getElementById('btn-submit').style.display='none';
  document.getElementById('btn-next-q').style.display='';
}

// ── 연습 모드 (랜덤) ──────────────────────────────────────────────────────
async function startQuiz(){
  const url='/admin/api/questions?page=1&per_page=999'+(quizDomain?'&domain='+quizDomain:'');
  const d=await(await fetch('/admin/api/questions?page=1'+(quizDomain?'&domain='+quizDomain:'')+'&per_page=999')).json();
  if(!d.questions.length){alert('문제가 없습니다.');return;}
  quizPool=d.questions.sort(()=>Math.random()-.5);
  quizIdx=0;
  document.getElementById('quiz-setup').style.display='none';
  document.getElementById('quiz-area').style.display='';
  await loadAndRender(quizPool[quizIdx].id);
}

async function loadAndRender(id){
  const q=await(await fetch('/admin/api/question/'+id)).json();
  renderQuestion(q);
}

async function nextQuestion(){
  quizIdx++;
  if(quizIdx>=quizPool.length){
    alert('모든 문제를 풀었습니다! 처음으로 돌아갑니다.');
    quizPool=quizPool.sort(()=>Math.random()-.5);
    quizIdx=0;
  }
  await loadAndRender(quizPool[quizIdx].id);
  loadStats();
}

// ── 목록에서 풀기 ─────────────────────────────────────────────────────────
async function openQuizById(id){
  showTabByName('quiz');
  document.getElementById('quiz-setup').style.display='none';
  document.getElementById('quiz-area').style.display='';
  quizPool=[{id}]; quizIdx=0;
  await loadAndRender(id);
}
function showTabByName(t){
  document.querySelectorAll('.panel').forEach(p=>p.classList.remove('active'));
  document.querySelectorAll('.tab').forEach(b=>b.classList.remove('active'));
  document.getElementById('tab-'+t).classList.add('active');
  document.querySelectorAll('.tab')[['dash','list','quiz'].indexOf(t)].classList.add('active');
}

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

    # DB < JSON 이면 자동 시드 (볼륨 경로 → 앱 번들 경로 순서로 탐색)
    from pathlib import Path as _P
    from db.schema import DB_PATH, get_connection as _gc
    from db.seed import DEFAULT_RAW
    _candidates = [
        DB_PATH.parent / "questions_raw.json",  # 볼륨 경로
        DEFAULT_RAW,                             # 앱 번들 경로 (data/questions_raw.json)
    ]
    _raw = next((p for p in _candidates if p.exists()), None)
    if _raw:
        import json as _json
        conn = _gc()
        db_count = conn.execute("SELECT COUNT(*) FROM questions").fetchone()[0]
        conn.close()
        json_count = len(_json.loads(_raw.read_text(encoding="utf-8")))
        if db_count < json_count:
            print(f"DB({db_count}개) < JSON({json_count}개). {_raw} 에서 시드합니다...")
            from db.seed import seed
            seed(_raw)

    port = int(os.environ.get("PORT", 8000))
    app = _build_http_app()
    uvicorn.run(app, host="0.0.0.0", port=port)


if __name__ == "__main__":
    transport = os.environ.get("MCP_TRANSPORT", "stdio")
    if transport == "http":
        main_http()
    else:
        main()
