from __future__ import annotations

from threading import Lock
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

import arena_agents
import engine
import strategy
from learning import LearningStore

app = FastAPI(title="Laya vs TypeSafe Arena", version="2.0.0")
lock = Lock()
learning_store = LearningStore()
arena_agents.start_laya_loading()


class ArenaGame:
    def __init__(self):
        self.depth = 3
        self.learning_enabled = True
        self.swap_sides = False
        self.reset()

    def reset(self):
        self.board = engine.initial_board()
        self.turn = engine.BLACK
        self.game_over = False
        self.winner_side: str | None = None
        self.winner_agent: str | None = None
        self.draw_reason: str | None = None
        self.ply = 0
        self.max_plies = 160
        self.finalized = False
        self.trajectory: list[dict[str, Any]] = []
        self.history: list[dict[str, Any]] = []
        self.last_decision: dict[str, Any] | None = None
        self.last_candidates: list[dict[str, Any]] = []
        self.position_counts: dict[str, int] = {}
        self.side_agents = (
            {engine.BLACK: "typesafe", engine.RED: "laya"}
            if self.swap_sides
            else {engine.BLACK: "laya", engine.RED: "typesafe"}
        )
        self._count_position()

    def configure(self, depth: int, learning_enabled: bool, swap_sides: bool):
        self.depth = max(1, min(int(depth), 6))
        self.learning_enabled = bool(learning_enabled)
        self.swap_sides = bool(swap_sides)
        self.reset()

    def _count_position(self):
        key = strategy.board_key(self.board, self.turn)
        self.position_counts[key] = self.position_counts.get(key, 0) + 1

    def _finish(self, winner_side: str | None, draw_reason: str | None = None):
        if self.finalized:
            return
        self.game_over = True
        self.winner_side = winner_side
        self.winner_agent = self.side_agents[winner_side] if winner_side else None
        self.draw_reason = draw_reason
        self.finalized = True
        if self.learning_enabled:
            learning_store.record_game(self.trajectory, self.winner_agent)

    def _check_end(self):
        win = engine.winner(self.board, self.turn)
        if win:
            self._finish(win)
            return

        if self.ply >= self.max_plies:
            self._finish(None, "Maximale Zugzahl erreicht")
            return

        key = strategy.board_key(self.board, self.turn)
        if self.position_counts.get(key, 0) >= 3:
            self._finish(None, "Dreifache Stellungswiederholung")

    def step(self) -> dict:
        if self.game_over:
            return self.snapshot()

        current_agent = self.side_agents[self.turn]
        state_key = strategy.board_key(self.board, self.turn)
        analyses = strategy.analyze_moves(self.board, self.turn, self.depth)
        if not analyses:
            self._finish(engine.opponent(self.turn))
            return self.snapshot()

        for item in analyses:
            if self.learning_enabled:
                learned = learning_store.bonus(
                    current_agent,
                    state_key,
                    item["notation"],
                    item,
                )
            else:
                learned = {
                    "learning_bonus": 0.0,
                    "learned_q": 0.0,
                    "visits": 0,
                    "features": strategy.learning_features(item),
                }
            item.update(learned)
            item["combined_score"] = round(
                float(item["lookahead_score"]) + float(item["learning_bonus"]),
                3,
            )

        if current_agent == "laya":
            move, decision = arena_agents.choose_laya(self.board, self.turn, analyses)
        else:
            move, decision = arena_agents.choose_typesafe(self.board, self.turn, analyses)

        selected = next(a for a in analyses if a["id"] == move.id)
        self.trajectory.append(
            {
                "agent": current_agent,
                "state_key": state_key,
                "move": selected["notation"],
                "features": selected["features"],
            }
        )

        decision["side"] = self.turn
        decision["ply"] = self.ply + 1
        decision["learning_enabled"] = self.learning_enabled

        self.board = engine.apply_move(self.board, move)
        self.history.append(decision)
        self.history = self.history[-60:]
        self.last_decision = decision
        self.last_candidates = [
            {
                "id": a["id"],
                "notation": a["notation"],
                "lookahead_score": a["lookahead_score"],
                "learning_bonus": a["learning_bonus"],
                "combined_score": a["combined_score"],
                "learned_q": a["learned_q"],
                "visits": a["visits"],
                "principal_variation": a["principal_variation"],
            }
            for a in sorted(analyses, key=lambda x: x["combined_score"], reverse=True)
        ]

        self.ply += 1
        self.turn = engine.opponent(self.turn)
        self._count_position()
        self._check_end()
        return self.snapshot()

    def snapshot(self) -> dict:
        return {
            "board": self.board,
            "turn": self.turn,
            "current_agent": None if self.game_over else self.side_agents[self.turn],
            "side_agents": self.side_agents,
            "game_over": self.game_over,
            "winner_side": self.winner_side,
            "winner_agent": self.winner_agent,
            "draw_reason": self.draw_reason,
            "ply": self.ply,
            "max_plies": self.max_plies,
            "depth": self.depth,
            "learning_enabled": self.learning_enabled,
            "swap_sides": self.swap_sides,
            "last_decision": self.last_decision,
            "last_candidates": self.last_candidates,
            "history": self.history[-12:],
            "laya": arena_agents.laya_status(),
            "typesafe": arena_agents.typesafe_status(),
            "learning": learning_store.summary(),
        }


game = ArenaGame()


class ApiKeyRequest(BaseModel):
    api_key: str = ""


class ArenaSettings(BaseModel):
    depth: int = Field(default=3, ge=1, le=6)
    learning_enabled: bool = True
    swap_sides: bool = False


@app.get("/", response_class=HTMLResponse)
def index():
    return HTMLResponse(HTML)


@app.get("/health")
def health():
    return {"ok": True, "version": "2.0.0"}


@app.get("/api/state")
def state():
    with lock:
        return game.snapshot()


@app.post("/api/typesafe/key")
def set_typesafe_key(req: ApiKeyRequest):
    arena_agents.set_typesafe_api_key(req.api_key)
    return {
        "ok": True,
        "typesafe": arena_agents.typesafe_status(),
        "message": "API-Key ist nur im RAM dieses lokalen Prozesses gespeichert.",
    }


@app.post("/api/arena/new")
def new_match(settings: ArenaSettings):
    with lock:
        game.configure(settings.depth, settings.learning_enabled, settings.swap_sides)
        return game.snapshot()


@app.post("/api/arena/step")
def arena_step():
    with lock:
        try:
            return game.step()
        except arena_agents.AgentUnavailable as exc:
            raise HTTPException(status_code=409, detail=str(exc))
        except Exception as exc:
            raise HTTPException(status_code=500, detail=f"{type(exc).__name__}: {exc}")


@app.post("/api/learning/reset")
def reset_learning():
    with lock:
        learning_store.reset()
        return {"ok": True, "learning": learning_store.summary()}


HTML = r"""<!doctype html>
<html lang="de">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Laya vs TypeSafe Arena</title>
<style>
:root{color-scheme:dark;--bg:#090a0f;--panel:#12141d;--panel2:#0e1017;--line:#282c3b;--text:#f4f5f8;--muted:#9da5b8;--pink:#ed3dbd;--cyan:#43c8e8;--light:#d9d3c4;--dark:#403b38;--green:#5bd394;--orange:#f4b860}
*{box-sizing:border-box} body{margin:0;background:radial-gradient(circle at 18% 0%,#191125 0,#090a0f 38%);color:var(--text);font:15px/1.45 Inter,system-ui,Segoe UI,sans-serif}
header{max-width:1320px;margin:auto;padding:24px 22px 10px;display:flex;justify-content:space-between;align-items:end;gap:16px;flex-wrap:wrap}
h1{margin:0;font-size:28px}.laya{color:var(--pink)}.typesafe{color:var(--cyan)}.muted{color:var(--muted)}.build{font-size:12px;color:var(--muted)}
main{max-width:1320px;margin:auto;padding:14px 22px 40px;display:grid;grid-template-columns:minmax(440px,720px) minmax(330px,1fr);gap:22px}
.card{background:rgba(18,20,29,.95);border:1px solid var(--line);border-radius:16px}
.board-card{padding:14px}.board{aspect-ratio:1;display:grid;grid-template-columns:repeat(8,1fr);overflow:hidden;border-radius:11px;border:1px solid #000}
.sq{display:grid;place-items:center;position:relative}.sq.light{background:var(--light)}.sq.dark{background:var(--dark)}
.piece{width:72%;height:72%;border-radius:50%;display:grid;place-items:center;box-shadow:inset 0 0 0 4px rgba(255,255,255,.13),0 7px 14px rgba(0,0,0,.35);font-weight:800}
.piece.black{background:#111;border:2px solid #666}.piece.red{background:#c92749;border:2px solid #f07b94}.piece.king:after{content:"♛";font-size:28px}
.last-from{box-shadow:inset 0 0 0 5px rgba(237,61,189,.5)}.last-to{box-shadow:inset 0 0 0 5px rgba(67,200,232,.6)}
.stack{display:grid;gap:14px}.panel{padding:16px}.panel h2{margin:0 0 10px;font-size:16px}.panel h3{margin:14px 0 8px;font-size:13px;color:var(--muted);text-transform:uppercase;letter-spacing:.07em}
.row{display:flex;gap:9px;align-items:center;flex-wrap:wrap}.grid2{display:grid;grid-template-columns:1fr 1fr;gap:10px}
input,select{background:#0b0d13;color:var(--text);border:1px solid var(--line);border-radius:9px;padding:10px 11px;min-height:40px}input[type=password]{flex:1;min-width:170px}
button{border:0;border-radius:10px;padding:10px 13px;font-weight:750;cursor:pointer;background:#262b3b;color:white}button.primary{background:var(--pink)}button.secondary{background:#1d7388}button.danger{background:#6c2734}button:disabled{opacity:.45;cursor:not-allowed}
.badge{display:inline-flex;align-items:center;gap:6px;padding:5px 9px;border-radius:999px;background:#252a3a;font-size:12px}.ok{color:var(--green)}.warn{color:var(--orange)}.bad{color:#ff7a8a}
.score{display:grid;grid-template-columns:1fr 1fr;gap:10px}.agentbox{background:var(--panel2);border:1px solid var(--line);border-radius:12px;padding:12px}.big{font-size:22px;font-weight:800}
.status{font-size:18px;font-weight:800;margin-bottom:5px}
pre{margin:0;background:#0b0d13;border:1px solid var(--line);border-radius:10px;padding:11px;color:#cbd2df;white-space:pre-wrap;word-break:break-word;max-height:270px;overflow:auto;font-size:12px}
.history{display:grid;gap:6px;max-height:230px;overflow:auto}.hist{display:grid;grid-template-columns:44px 78px 1fr;gap:8px;padding:7px 8px;border-bottom:1px solid #232635;font-size:12px}
label{font-size:13px;color:var(--muted)}.check{display:flex;align-items:center;gap:7px}.check input{min-height:auto}
.small{font-size:12px}.separator{height:1px;background:var(--line);margin:13px 0}
@media(max-width:900px){main{grid-template-columns:1fr}.grid2,.score{grid-template-columns:1fr}header{align-items:start}.board-card{padding:8px}}
</style>
</head>
<body>
<header>
  <div>
    <h1><span class="laya">Laya</span> vs <span class="typesafe">TypeSafe / Jev</span></h1>
    <div class="muted">8×8 Dame · adversariale Vorschau · persistentes Self-Play-Lernen</div>
  </div>
  <div class="build">Arena Build 2.0</div>
</header>

<main>
  <section class="card board-card">
    <div id="board" class="board" aria-label="Damebrett"></div>
  </section>

  <aside class="stack">
    <section class="card panel">
      <div id="status" class="status">Arena wird geladen…</div>
      <div id="turnInfo" class="muted"></div>
      <div class="separator"></div>
      <div class="score">
        <div class="agentbox"><div class="laya"><strong>Laya</strong></div><div id="layaStatus" class="small muted">lädt…</div><div id="layaScore" class="big">0 Siege</div></div>
        <div class="agentbox"><div class="typesafe"><strong>TypeSafe / Jev</strong></div><div id="typeStatus" class="small muted">API fehlt</div><div id="typeScore" class="big">0 Siege</div></div>
      </div>
    </section>

    <section class="card panel">
      <h2>TypeSafe API</h2>
      <div class="row">
        <input id="apiKey" type="password" autocomplete="off" placeholder="TYPESAFE_API_KEY hier einfügen">
        <button id="saveKey" class="secondary">API verbinden</button>
      </div>
      <div id="apiHint" class="small muted" style="margin-top:8px">Der Key wird nur im RAM des lokalen Python-Prozesses gehalten und nicht in GitHub oder der Lern-Datei gespeichert.</div>
    </section>

    <section class="card panel">
      <h2>Arena-Steuerung</h2>
      <div class="grid2">
        <div><label for="depth">Vorausschau (Halbzüge)</label><select id="depth"><option>1</option><option>2</option><option selected>3</option><option>4</option><option>5</option><option>6</option></select></div>
        <div><label for="delay">Pause zwischen Zügen</label><select id="delay"><option value="0">keine</option><option value="250" selected>0,25 s</option><option value="750">0,75 s</option><option value="1500">1,5 s</option></select></div>
      </div>
      <div class="row" style="margin-top:11px">
        <label class="check"><input id="learning" type="checkbox" checked> Selbstlernen aktiv</label>
        <label class="check"><input id="swap" type="checkbox"> Seiten tauschen</label>
      </div>
      <div class="row" style="margin-top:12px">
        <button id="newMatch">Neues Match</button>
        <button id="startStop" class="primary">Start</button>
        <button id="stepBtn">1 Zug</button>
        <button id="resetLearning" class="danger">Lernen zurücksetzen</button>
      </div>
    </section>

    <section class="card panel">
      <h2>Letzte Entscheidung</h2>
      <pre id="decision">Noch kein Zug.</pre>
    </section>

    <section class="card panel">
      <h2>Partieverlauf</h2>
      <div id="history" class="history"><div class="muted small">Noch keine Züge.</div></div>
    </section>
  </aside>
</main>

<script>
let state=null;
let running=false;
let stepping=false;

const $=id=>document.getElementById(id);

function pieceClass(p){return p.toLowerCase()==='b'?'black':'red'}

function renderBoard(){
  const root=$('board');
  root.innerHTML='';
  if(!state) return;
  for(let r=0;r<8;r++){
    for(let c=0;c<8;c++){
      const sq=document.createElement('div');
      sq.className='sq '+(((r+c)%2)?'dark':'light');
      const p=state.board[r][c];
      if(p!=='.'){
        const piece=document.createElement('div');
        piece.className='piece '+pieceClass(p)+(p===p.toUpperCase()?' king':'');
        sq.appendChild(piece);
      }
      root.appendChild(sq);
    }
  }
}

function sideLabel(side){
  if(!state) return side;
  const a=state.side_agents[side];
  return (side==='black'?'Schwarz':'Rot')+' = '+(a==='laya'?'Laya':'TypeSafe/Jev');
}

function render(){
  if(!state) return;
  renderBoard();

  const ls=state.laya;
  $('layaStatus').innerHTML=ls.status==='ready'
    ? '<span class="ok">bereit</span> · '+ls.model
    : ls.status==='error'
      ? '<span class="bad">Fehler</span> · '+(ls.error||'')
      : '<span class="warn">lädt… '+(ls.elapsed_seconds||0)+' s</span>';

  $('typeStatus').innerHTML=state.typesafe.configured
    ? '<span class="ok">API konfiguriert</span> · '+state.typesafe.model
    : '<span class="warn">API-Key fehlt</span>';

  const learn=state.learning.agents;
  $('layaScore').textContent=learn.laya.wins+' Siege';
  $('typeScore').textContent=learn.typesafe.wins+' Siege';

  if(state.game_over){
    if(state.winner_agent){
      $('status').textContent=(state.winner_agent==='laya'?'Laya':'TypeSafe/Jev')+' gewinnt.';
    }else{
      $('status').textContent='Remis.';
    }
    $('turnInfo').textContent=(state.draw_reason||'Partie beendet')+' · '+state.ply+' Halbzüge';
    running=false;
    $('startStop').textContent='Start';
  }else{
    const name=state.current_agent==='laya'?'Laya':'TypeSafe/Jev';
    $('status').textContent=(stepping?name+' denkt voraus…':name+' ist am Zug.');
    $('turnInfo').textContent=sideLabel('black')+' · '+sideLabel('red')+' · Tiefe '+state.depth+' · Halbzug '+(state.ply+1);
  }

  if(state.last_decision){
    const d=state.last_decision;
    $('decision').textContent=JSON.stringify({
      agent:d.agent,
      side:d.side,
      move:d.notation,
      model:d.model,
      confidence:d.confidence,
      lookahead_score:d.lookahead_score,
      learning_bonus:d.learning_bonus,
      combined_score:d.combined_score,
      learned_q:d.learned_q,
      visits:d.visits,
      principal_variation:d.principal_variation,
      search_depth:d.search_depth
    },null,2);
  }

  const hist=$('history');
  if(!state.history.length){
    hist.innerHTML='<div class="muted small">Noch keine Züge.</div>';
  }else{
    hist.innerHTML='';
    for(const h of [...state.history].reverse()){
      const row=document.createElement('div');
      row.className='hist';
      const agent=h.agent==='laya'?'Laya':'TypeSafe';
      row.innerHTML='<span>#'+h.ply+'</span><strong class="'+(h.agent==='laya'?'laya':'typesafe')+'">'+agent+'</strong><span>'+h.notation+' · Suche '+h.lookahead_score+' · Lernen '+h.learning_bonus+'</span>';
      hist.appendChild(row);
    }
  }

  $('stepBtn').disabled=stepping||state.game_over;
  $('newMatch').disabled=stepping;
}

async function load(){
  try{
    const r=await fetch('/api/state',{cache:'no-store'});
    state=await r.json();
    render();
  }catch(e){
    $('status').textContent='Server nicht erreichbar.';
    $('decision').textContent=String(e);
  }
}

async function saveApiKey(){
  const key=$('apiKey').value.trim();
  const r=await fetch('/api/typesafe/key',{
    method:'POST',
    headers:{'Content-Type':'application/json'},
    body:JSON.stringify({api_key:key})
  });
  const data=await r.json();
  if(!r.ok){$('apiHint').textContent=data.detail||'Fehler';return}
  $('apiKey').value='';
  $('apiHint').textContent=data.message;
  await load();
}

async function newMatch(){
  running=false;
  $('startStop').textContent='Start';
  const settings={
    depth:parseInt($('depth').value,10),
    learning_enabled:$('learning').checked,
    swap_sides:$('swap').checked
  };
  const r=await fetch('/api/arena/new',{
    method:'POST',
    headers:{'Content-Type':'application/json'},
    body:JSON.stringify(settings)
  });
  state=await r.json();
  render();
}

async function oneStep(){
  if(stepping||!state||state.game_over) return false;
  stepping=true;
  render();
  try{
    const r=await fetch('/api/arena/step',{method:'POST'});
    const data=await r.json();
    if(!r.ok){
      running=false;
      $('startStop').textContent='Start';
      $('decision').textContent='Agent gestoppt:\n'+(data.detail||('HTTP '+r.status));
      return false;
    }
    state=data;
    render();
    return !state.game_over;
  }catch(e){
    running=false;
    $('startStop').textContent='Start';
    $('decision').textContent='Fehler:\n'+String(e);
    return false;
  }finally{
    stepping=false;
    render();
  }
}

async function autoLoop(){
  while(running && state && !state.game_over){
    const ok=await oneStep();
    if(!ok) break;
    const delay=parseInt($('delay').value,10)||0;
    if(delay) await new Promise(r=>setTimeout(r,delay));
  }
  running=false;
  $('startStop').textContent='Start';
}

function toggleRun(){
  if(running){
    running=false;
    $('startStop').textContent='Start';
    return;
  }
  if(!state||state.game_over) return;
  running=true;
  $('startStop').textContent='Pause';
  autoLoop();
}

async function resetLearning(){
  if(!confirm('Alle bisher gelernten Self-Play-Werte wirklich löschen?')) return;
  const r=await fetch('/api/learning/reset',{method:'POST'});
  const data=await r.json();
  if(r.ok){await load()}else{$('decision').textContent=data.detail||'Fehler beim Zurücksetzen'}
}

$('saveKey').addEventListener('click',saveApiKey);
$('newMatch').addEventListener('click',newMatch);
$('startStop').addEventListener('click',toggleRun);
$('stepBtn').addEventListener('click',oneStep);
$('resetLearning').addEventListener('click',resetLearning);
$('apiKey').addEventListener('keydown',e=>{if(e.key==='Enter')saveApiKey()});

load();
setInterval(()=>{if(!stepping)load()},2500);
</script>
</body>
</html>"""
