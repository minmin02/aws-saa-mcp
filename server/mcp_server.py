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
    per_page = min(int(request.query_params.get("per_page", 20)), 500)
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
<title>AWS SAA-C03 연습</title>
<style>
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;background:#f0f2f5;color:#1a1a2e}
.hdr{background:#1a1a2e;color:#fff;padding:16px 28px;display:flex;align-items:center;gap:16px;position:sticky;top:0;z-index:100}
.hdr h1{font-size:1.15rem;font-weight:700;white-space:nowrap}
.tabs{display:flex;gap:4px;margin-left:auto}
.tab{padding:6px 16px;border-radius:8px;font-size:.82rem;cursor:pointer;border:1px solid rgba(255,255,255,.2);background:transparent;color:rgba(255,255,255,.8);transition:.15s}
.tab.on,.tab:hover{background:rgba(255,255,255,.18);color:#fff}
.wrap{max-width:860px;margin:0 auto;padding:24px 16px}
.panel{display:none}.panel.on{display:block}
.card{background:#fff;border-radius:14px;padding:22px;box-shadow:0 1px 6px rgba(0,0,0,.09);margin-bottom:18px}
.sg{display:grid;grid-template-columns:repeat(auto-fit,minmax(160px,1fr));gap:12px;margin-bottom:18px}
.sc{background:#fff;border-radius:12px;padding:16px;box-shadow:0 1px 4px rgba(0,0,0,.08);text-align:center}
.sc .lbl{font-size:.7rem;color:#999;text-transform:uppercase;letter-spacing:.05em}
.sc .val{font-size:1.8rem;font-weight:700;color:#1a1a2e;margin:2px 0}
table{width:100%;border-collapse:collapse;font-size:.83rem}
th{text-align:left;padding:7px 10px;background:#f8f9fb;color:#888;font-weight:600;font-size:.71rem;text-transform:uppercase}
td{padding:9px 10px;border-top:1px solid #f0f0f0}
.bar{background:#eee;border-radius:99px;height:5px;width:80px;display:inline-block;vertical-align:middle}
.bar-f{background:#4285f4;border-radius:99px;height:5px;transition:width .3s}
.bd{display:inline-block;padding:2px 8px;border-radius:99px;font-size:.7rem;font-weight:700}
.d1{background:#e8f4fd;color:#1a73e8}.d2{background:#e6f4ea;color:#188038}
.d3{background:#fef7e0;color:#b06000}.d4{background:#fce8e6;color:#c5221f}
.frow{display:flex;gap:8px;margin-bottom:14px;flex-wrap:wrap;align-items:center}
select{padding:7px 10px;border-radius:8px;border:1px solid #ddd;font-size:.83rem;background:#fff}
.btn{padding:7px 16px;border-radius:8px;border:none;font-size:.83rem;cursor:pointer;background:#4285f4;color:#fff;transition:.15s;font-weight:500}
.btn:hover{background:#3367d6}.btn:disabled{opacity:.4;cursor:default}
.btn-o{background:#f1f3f4;color:#444;border:1px solid #ddd}.btn-o:hover{background:#e8eaed}
.btn-sm{padding:4px 11px;font-size:.76rem}
.btn-red{background:#ea4335}.btn-red:hover{background:#c5221f}
.qrow{padding:12px 0;border-top:1px solid #f0f0f0}
.qmeta{font-size:.72rem;color:#999;margin-bottom:5px;display:flex;align-items:center;gap:8px}
.qtxt{font-size:.84rem;line-height:1.55;color:#333}
.pg{display:flex;gap:8px;margin-top:14px;align-items:center}
.pgi{color:#888;font-size:.79rem}
.dsel{display:flex;gap:8px;flex-wrap:wrap;margin:12px 0 20px}
.ds{padding:8px 18px;border-radius:99px;border:2px solid #e8eaed;font-size:.82rem;cursor:pointer;background:#fff;transition:.15s;font-weight:500}
.ds.on{border-color:#4285f4;background:#e8f0fe;color:#4285f4}
.qz-wrap{max-width:760px;margin:0 auto}
.prog-bg{background:#e8eaed;border-radius:99px;height:6px;margin-bottom:18px}
.prog-fill{background:#4285f4;border-radius:99px;height:6px;transition:width .4s}
.score-row{display:flex;justify-content:space-between;align-items:center;margin-bottom:6px;font-size:.8rem;color:#888}
.score-v{font-weight:700;color:#1a1a2e}
.qz-card{background:#fff;border-radius:16px;padding:28px 32px;box-shadow:0 2px 10px rgba(0,0,0,.1)}
.qz-meta{font-size:.73rem;color:#999;display:flex;gap:10px;align-items:center;margin-bottom:14px}
.qz-q{font-size:.97rem;line-height:1.75;color:#1a1a2e;margin-bottom:22px;font-weight:500}
.choices{display:flex;flex-direction:column;gap:9px;margin-bottom:22px}
.ch{display:flex;align-items:flex-start;gap:12px;padding:13px 16px;border-radius:10px;border:2px solid #e8eaed;cursor:pointer;transition:.15s;font-size:.87rem;line-height:1.5;user-select:none}
.ch:hover{border-color:#4285f4;background:#f0f4ff}
.ch.sel{border-color:#4285f4;background:#e8f0fe}
.ch.ok{border-color:#34a853!important;background:#e6f4ea!important}
.ch.ng{border-color:#ea4335!important;background:#fce8e6!important}
.ch-letter{font-weight:700;min-width:22px;color:#4285f4;font-size:.88rem;flex-shrink:0}
.ch.ok .ch-letter{color:#34a853}.ch.ng .ch-letter{color:#ea4335}
.rb{padding:14px 18px;border-radius:10px;margin-bottom:14px;font-size:.87rem;font-weight:600}
.rb-ok{background:#e6f4ea;border:1.5px solid #34a853;color:#1e6e34}
.rb-ng{background:#fce8e6;border:1.5px solid #ea4335;color:#7b1c14}
.exp{background:#f8f9fa;border-radius:10px;padding:14px 16px;font-size:.82rem;color:#555;line-height:1.65;margin-bottom:14px}
.votes{display:flex;gap:6px;flex-wrap:wrap;margin-bottom:18px}
.vc{font-size:.73rem;padding:3px 10px;border-radius:99px;background:#e8eaed;color:#555;font-weight:600}
.qz-btns{display:flex;gap:8px;justify-content:flex-end;flex-wrap:wrap}
.hint{font-size:.72rem;color:#bbb;margin-top:10px;text-align:right}
.end-card{background:#fff;border-radius:16px;padding:40px;box-shadow:0 2px 10px rgba(0,0,0,.1);text-align:center}
.end-pct{font-size:3.5rem;font-weight:800;color:#4285f4;margin:16px 0 4px}
.end-sub{font-size:1rem;color:#888;margin-bottom:28px}
.end-grid{display:grid;grid-template-columns:1fr 1fr 1fr;gap:14px;margin-bottom:28px}
.eg{background:#f8f9fa;border-radius:10px;padding:16px}
.eg .ev{font-size:1.6rem;font-weight:700;color:#1a1a2e}
.eg .el{font-size:.72rem;color:#888;margin-top:3px}
.end-btns{display:flex;gap:10px;justify-content:center;flex-wrap:wrap}
.retry-badge{background:#ea4335;color:#fff;border-radius:99px;font-size:.7rem;padding:1px 7px;margin-left:4px;font-weight:700}
</style>
</head>
<body>
<div class="hdr">
  <h1>AWS SAA-C03</h1>
  <div class="tabs">
    <button class="tab on" onclick="gotoTab('dash',this)">대시보드</button>
    <button class="tab" onclick="gotoTab('list',this)">문제 목록</button>
    <button class="tab" onclick="gotoTab('quiz',this)" id="tab-quiz-btn">연습 모드</button>
  </div>
</div>
<div class="wrap">

<!-- 대시보드 -->
<div id="tab-dash" class="panel on">
  <div class="sg">
    <div class="sc"><div class="lbl">전체 문제</div><div class="val" id="s-tot">-</div></div>
    <div class="sc"><div class="lbl">풀이 완료</div><div class="val" id="s-seen">-</div></div>
    <div class="sc"><div class="lbl">전체 정답률</div><div class="val" id="s-acc">-</div></div>
    <div class="sc"><div class="lbl">남은 문제</div><div class="val" id="s-rem">-</div></div>
  </div>
  <div class="card">
    <table>
      <thead><tr><th>도메인</th><th>문제</th><th>풀이</th><th>미풀이</th><th>정답률</th><th>진도</th></tr></thead>
      <tbody id="dtbody"></tbody>
    </table>
  </div>
</div>

<!-- 문제 목록 -->
<div id="tab-list" class="panel">
  <div class="card">
    <div class="frow">
      <select id="lf-dom">
        <option value="">전체</option>
        <option value="1">D1 보안</option>
        <option value="2">D2 복원력</option>
        <option value="3">D3 고성능</option>
        <option value="4">D4 비용최적화</option>
      </select>
      <button class="btn" onclick="loadList(1)">조회</button>
    </div>
    <div id="qlist"></div>
    <div class="pg" id="lpg" style="display:none">
      <button class="btn btn-o btn-sm" id="lp-prev" onclick="lpage(-1)">◀ 이전</button>
      <span class="pgi" id="lp-info"></span>
      <button class="btn btn-o btn-sm" id="lp-next" onclick="lpage(1)">다음 ▶</button>
    </div>
  </div>
</div>

<!-- 연습 모드 -->
<div id="tab-quiz" class="panel">
  <div class="qz-wrap">

    <!-- 설정 화면 -->
    <div class="card" id="qz-setup">
      <h2 style="margin-bottom:6px">도메인 선택</h2>
      <p style="font-size:.82rem;color:#999;margin-bottom:4px">풀 문제 범위를 선택하세요.</p>
      <div class="dsel">
        <button class="ds on" onclick="pickDom(0,this)">전체</button>
        <button class="ds" onclick="pickDom(1,this)">D1 보안</button>
        <button class="ds" onclick="pickDom(2,this)">D2 복원력</button>
        <button class="ds" onclick="pickDom(3,this)">D3 고성능</button>
        <button class="ds" onclick="pickDom(4,this)">D4 비용최적화</button>
      </div>
      <div style="display:flex;gap:10px;align-items:center;flex-wrap:wrap">
        <button class="btn" onclick="startQuiz(false)">▶ 시작</button>
        <div id="retry-wrap" style="display:none">
          <button class="btn btn-red" onclick="startQuiz(true)">오답 복습 <span class="retry-badge" id="retry-cnt">0</span></button>
        </div>
        <span style="font-size:.78rem;color:#bbb">A/B/C/D 키 선택 · Enter 제출/다음</span>
      </div>
    </div>

    <!-- 퀴즈 진행 -->
    <div id="qz-run" style="display:none">
      <div class="score-row">
        <span id="qz-prog-txt"></span>
        <span>맞음 <span class="score-v" id="sc-ok">0</span> / 시도 <span class="score-v" id="sc-try">0</span></span>
      </div>
      <div class="prog-bg"><div class="prog-fill" id="prog-fill" style="width:0%"></div></div>
      <div class="qz-card">
        <div class="qz-meta" id="qz-meta"></div>
        <div class="qz-q" id="qz-q"></div>
        <div class="choices" id="qz-choices"></div>
        <div id="qz-feedback" style="display:none">
          <div class="rb" id="qz-rb"></div>
          <div class="exp" id="qz-exp" style="display:none"></div>
          <div class="votes" id="qz-votes"></div>
        </div>
        <div class="qz-btns">
          <button class="btn btn-o" onclick="backToSetup()">← 설정</button>
          <button class="btn" id="btn-sub" onclick="submitAns()">제출</button>
          <button class="btn" id="btn-nxt" style="display:none" onclick="nextQ()">다음 →</button>
        </div>
        <div class="hint" id="qz-hint"></div>
      </div>
    </div>

    <!-- 종료 화면 -->
    <div id="qz-end" style="display:none">
      <div class="end-card">
        <div style="font-size:2.5rem">🎉</div>
        <div class="end-pct" id="end-pct">--%</div>
        <div class="end-sub" id="end-sub"></div>
        <div class="end-grid">
          <div class="eg"><div class="ev" id="end-tot">-</div><div class="el">총 문제</div></div>
          <div class="eg"><div class="ev" id="end-ok" style="color:#34a853">-</div><div class="el">정답</div></div>
          <div class="eg"><div class="ev" id="end-ng" style="color:#ea4335">-</div><div class="el">오답</div></div>
        </div>
        <div class="end-btns">
          <button class="btn btn-o" onclick="backToSetup()">← 설정으로</button>
          <button class="btn" onclick="startQuiz(false)">다시 풀기</button>
          <div id="end-retry-wrap" style="display:none">
            <button class="btn btn-red" onclick="startQuiz(true)">오답만 복습</button>
          </div>
        </div>
      </div>
    </div>

  </div>
</div>

</div>
<script>
const DC=['','d1','d2','d3','d4'];
let lCurP=1,lTotalP=1;
let qzDom=0,qzPool=[],qzIdx=0,qzCur=null,qzMulti=false,qzSel=new Set();
let scOk=0,scTry=0,wrongIds=[];

function gotoTab(t,el){
  document.querySelectorAll('.panel').forEach(p=>p.classList.remove('on'));
  document.querySelectorAll('.tab').forEach(b=>b.classList.remove('on'));
  document.getElementById('tab-'+t).classList.add('on');el.classList.add('on');
  if(t==='dash')loadDash();
  if(t==='list')loadList(1);
}
function switchToQuiz(){
  document.querySelectorAll('.panel').forEach(p=>p.classList.remove('on'));
  document.querySelectorAll('.tab').forEach(b=>b.classList.remove('on'));
  document.getElementById('tab-quiz').classList.add('on');
  document.getElementById('tab-quiz-btn').classList.add('on');
}

async function loadDash(){
  const d=await(await fetch('/admin/api/stats')).json();
  document.getElementById('s-tot').textContent=d.total;
  document.getElementById('s-seen').textContent=d.seen;
  document.getElementById('s-acc').textContent=d.accuracy+'%';
  document.getElementById('s-rem').textContent=d.total-d.seen;
  document.getElementById('dtbody').innerHTML=d.domains.map(dm=>{
    const pct=dm.total?Math.round(dm.seen/dm.total*100):0;
    return`<tr>
      <td><span class="bd ${DC[dm.domain]}">D${dm.domain}</span> ${dm.name}</td>
      <td><b>${dm.total}</b></td><td>${dm.seen}</td><td>${dm.unseen}</td><td>${dm.accuracy}%</td>
      <td><div class="bar"><div class="bar-f" style="width:${pct}%"></div></div> ${pct}%</td>
    </tr>`;
  }).join('');
}

async function loadList(page){
  lCurP=page;
  const dom=document.getElementById('lf-dom').value;
  const d=await(await fetch('/admin/api/questions?page='+page+(dom?'&domain='+dom:''))).json();
  lTotalP=Math.ceil(d.total/d.per_page)||1;
  const el=document.getElementById('qlist');
  if(!d.questions.length){el.innerHTML='<p style="padding:30px;text-align:center;color:#aaa">문제 없음</p>';document.getElementById('lpg').style.display='none';return;}
  el.innerHTML=d.questions.map(q=>`
    <div class="qrow">
      <div class="qmeta">
        <span class="bd ${DC[q.domain]}">D${q.domain}</span>
        <span>#${q.id}\xb7${q.source_id}</span>
        <span style="margin-left:auto"><button class="btn btn-o btn-sm" onclick="quizById(${q.id})">풀기</button></span>
      </div>
      <div class="qtxt">${q.question}</div>
    </div>`).join('');
  const pg=document.getElementById('lpg');pg.style.display='flex';
  document.getElementById('lp-info').textContent=lCurP+'/'+lTotalP+' (총 '+d.total+'문제)';
  document.getElementById('lp-prev').disabled=lCurP<=1;
  document.getElementById('lp-next').disabled=lCurP>=lTotalP;
}
function lpage(d){if(lCurP+d>=1&&lCurP+d<=lTotalP)loadList(lCurP+d);}
async function quizById(id){
  switchToQuiz();
  document.getElementById('qz-setup').style.display='none';
  document.getElementById('qz-end').style.display='none';
  document.getElementById('qz-run').style.display='';
  qzPool=[{id}];qzIdx=0;scOk=0;scTry=0;
  updateSc();await loadAndRender(id);
}

function pickDom(d,el){
  qzDom=d;
  document.querySelectorAll('.ds').forEach(b=>b.classList.remove('on'));
  el.classList.add('on');
}
async function startQuiz(retryOnly){
  let ids;
  if(retryOnly){
    if(!wrongIds.length){alert('오답이 없습니다.');return;}
    ids=wrongIds.slice();
  }else{
    const url='/admin/api/questions?page=1&per_page=500'+(qzDom?'&domain='+qzDom:'');
    const d=await(await fetch(url)).json();
    if(!d.questions.length){alert('문제가 없습니다.');return;}
    ids=d.questions.map(q=>q.id);
  }
  for(let i=ids.length-1;i>0;i--){const j=Math.floor(Math.random()*(i+1));[ids[i],ids[j]]=[ids[j],ids[i]];}
  qzPool=ids.map(id=>({id}));qzIdx=0;scOk=0;scTry=0;
  if(!retryOnly)wrongIds=[];
  document.getElementById('qz-setup').style.display='none';
  document.getElementById('qz-end').style.display='none';
  document.getElementById('qz-run').style.display='';
  updateSc();await loadAndRender(qzPool[0].id);
}
function backToSetup(){
  document.getElementById('qz-setup').style.display='';
  document.getElementById('qz-run').style.display='none';
  document.getElementById('qz-end').style.display='none';
  updateRetry();loadDash();
}
function updateRetry(){
  const w=document.getElementById('retry-wrap');
  const e=document.getElementById('end-retry-wrap');
  if(wrongIds.length){
    w.style.display='';document.getElementById('retry-cnt').textContent=wrongIds.length;
    if(e)e.style.display='';
  }else{w.style.display='none';if(e)e.style.display='none';}
}

async function loadAndRender(id){
  const q=await(await fetch('/admin/api/question/'+id)).json();
  qzCur=q;qzSel=new Set();
  qzMulti=Array.isArray(q.answer)&&q.answer.length>1;
  const idx=qzIdx+1,tot=qzPool.length;
  document.getElementById('qz-prog-txt').textContent='문제 '+idx+' / '+tot;
  document.getElementById('prog-fill').style.width=Math.round((idx-1)/tot*100)+'%';
  document.getElementById('qz-meta').innerHTML=
    '<span class="bd '+DC[q.domain]+'">D'+q.domain+' '+q.domain_name+'</span>'+
    '<span>#'+q.id+'\xb7'+q.source_id+'</span>'+
    (qzMulti?'<span style="color:#e67700;font-weight:700">복수 정답</span>':'');
  document.getElementById('qz-q').textContent=q.question;
  document.getElementById('qz-choices').innerHTML=
    Object.entries(q.choices).map(([k,v])=>
      '<div class="ch" id="c-'+k+'" data-k="'+k+'"><span class="ch-letter">'+k+'</span><span>'+v+'</span></div>'
    ).join('');
  document.getElementById('qz-choices').onclick=function(e){
    const ch=e.target.closest('.ch');
    if(ch&&ch.dataset.k)toggleCh(ch.dataset.k);
  };
  document.getElementById('qz-feedback').style.display='none';
  document.getElementById('btn-sub').style.display='';
  document.getElementById('btn-nxt').style.display='none';
  document.getElementById('qz-exp').style.display='none';
  document.getElementById('qz-votes').innerHTML='';
  document.getElementById('qz-hint').textContent=qzMulti?'복수 선택 후 제출':'A/B/C/D 키로 선택 \xb7 Enter로 제출';
}
function toggleCh(k){
  if(document.getElementById('btn-sub').style.display==='none')return;
  if(qzMulti){
    if(qzSel.has(k)){qzSel.delete(k);document.getElementById('c-'+k).classList.remove('sel');}
    else{qzSel.add(k);document.getElementById('c-'+k).classList.add('sel');}
  }else{
    qzSel.forEach(s=>{const e=document.getElementById('c-'+s);if(e)e.classList.remove('sel');});
    qzSel=new Set([k]);document.getElementById('c-'+k).classList.add('sel');
  }
}
async function submitAns(){
  if(!qzSel.size){document.getElementById('qz-hint').textContent='보기를 선택하세요.';return;}
  const res=await(await fetch('/admin/api/check',{
    method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({question_id:qzCur.id,answers:[...qzSel]})
  })).json();
  scTry++;if(res.is_correct)scOk++;
  else if(!wrongIds.includes(qzCur.id))wrongIds.push(qzCur.id);
  updateSc();
  Object.keys(qzCur.choices).forEach(k=>{
    const e=document.getElementById('c-'+k);
    if(res.correct_answers.includes(k))e.classList.add('ok');
    else if(qzSel.has(k))e.classList.add('ng');
  });
  const rb=document.getElementById('qz-rb');
  rb.className='rb '+(res.is_correct?'rb-ok':'rb-ng');
  rb.textContent=res.is_correct?'✓ 정답!':'✗ 오답. 정답: '+res.correct_answers.join(', ');
  if(res.explanation){const eb=document.getElementById('qz-exp');eb.textContent=res.explanation;eb.style.display='';}
  if(res.votes&&res.votes.length){
    document.getElementById('qz-votes').innerHTML=
      '<span style="font-size:.72rem;color:#aaa;margin-right:4px">커뮤니티</span>'+
      res.votes.map(v=>'<span class="vc">'+v.option+' '+v.pct+'%</span>').join('');
  }
  document.getElementById('qz-feedback').style.display='';
  document.getElementById('btn-sub').style.display='none';
  document.getElementById('btn-nxt').style.display='';
  document.getElementById('btn-nxt').textContent=qzIdx>=qzPool.length-1?'결과 보기':'다음 →';
  document.getElementById('qz-hint').textContent='Enter → 다음 문제';
}
async function nextQ(){
  qzIdx++;
  if(qzIdx>=qzPool.length){showEnd();return;}
  document.getElementById('prog-fill').style.width=Math.round(qzIdx/qzPool.length*100)+'%';
  await loadAndRender(qzPool[qzIdx].id);
}
function updateSc(){
  document.getElementById('sc-ok').textContent=scOk;
  document.getElementById('sc-try').textContent=scTry;
}
function showEnd(){
  document.getElementById('qz-run').style.display='none';
  document.getElementById('qz-end').style.display='';
  const pct=scTry?Math.round(scOk/scTry*100):0;
  document.getElementById('end-pct').textContent=pct+'%';
  document.getElementById('end-sub').textContent=
    pct>=80?'🚀 훌륭합니다! 시험 준비가 잘 되고 있네요.':
    pct>=60?'💪 좋습니다. 틀린 문제를 복습해 보세요.':
    '📚 조금 더 연습이 필요합니다. 오답을 집중 복습하세요.';
  document.getElementById('end-tot').textContent=scTry;
  document.getElementById('end-ok').textContent=scOk;
  document.getElementById('end-ng').textContent=scTry-scOk;
  updateRetry();loadDash();
}

document.addEventListener('keydown',e=>{
  if(document.getElementById('qz-run').style.display==='none'||!qzCur)return;
  const k=e.key.toUpperCase();
  if(['A','B','C','D','E'].includes(k)&&document.getElementById('c-'+k)){
    e.preventDefault();toggleCh(k);return;
  }
  if(e.key==='Enter'){
    e.preventDefault();
    if(document.getElementById('btn-sub').style.display!=='none')submitAns();
    else if(document.getElementById('btn-nxt').style.display!=='none')nextQ();
  }
});

loadDash();loadList(1);

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
