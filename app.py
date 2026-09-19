from __future__ import annotations

from threading import Lock

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

import engine
import laya_player

app = FastAPI(title="Laya Checkers", version="1.0.0")
lock = Lock()


class Game:
    def __init__(self):
        self.reset()

    def reset(self):
        self.board = engine.initial_board()
        self.turn = engine.BLACK
        self.last_ai = None
        self.last_move = None
        self.game_over = False
        self.winner = None

    def snapshot(self) -> dict:
        moves = engine.legal_moves(self.board, self.turn) if not self.game_over else []
        return {
            "board": self.board,
            "turn": self.turn,
            "human_side": engine.BLACK,
            "ai_side": engine.RED,
            "legal_moves": [m.to_dict() | {"notation": engine.move_notation(m)} for m in moves],
            "last_move": self.last_move,
            "last_ai": self.last_ai,
            "game_over": self.game_over,
            "winner": self.winner,
        }

    def _finish_if_needed(self):
        win = engine.winner(self.board, self.turn)
        if win:
            self.game_over = True
            self.winner = win

    def human_move(self, move_id: str):
        if self.game_over:
            raise ValueError("Game is already over")
        if self.turn != engine.BLACK:
            raise ValueError("It is not the human turn")

        moves = engine.legal_moves(self.board, self.turn)
        move = next((m for m in moves if m.id == move_id), None)
        if move is None:
            raise ValueError("Illegal or outdated move")

        self.board = engine.apply_move(self.board, move)
        self.last_move = {"side": engine.BLACK, "id": move.id, "notation": engine.move_notation(move)}
        self.turn = engine.RED
        self._finish_if_needed()

    def ai_move(self):
        if self.game_over:
            return
        if self.turn != engine.RED:
            raise ValueError("It is not the AI turn")

        ai_moves = engine.legal_moves(self.board, engine.RED)
        if not ai_moves:
            self._finish_if_needed()
            return

        ai_move, debug = laya_player.choose_move(self.board, engine.RED, ai_moves)
        self.board = engine.apply_move(self.board, ai_move)
        self.last_ai = debug
        self.last_move = {"side": engine.RED, "id": ai_move.id, "notation": engine.move_notation(ai_move)}
        self.turn = engine.BLACK
        self._finish_if_needed()


game = Game()


class MoveRequest(BaseModel):
    move_id: str


@app.get("/", response_class=HTMLResponse)
def index():
    return HTMLResponse(HTML)


@app.get("/health")
def health():
    return {"ok": True}


@app.get("/api/state")
def state():
    with lock:
        return game.snapshot()


@app.post("/api/new")
def new_game():
    with lock:
        game.reset()
        return game.snapshot()


@app.post("/api/human-move")
def human_move(req: MoveRequest):
    """Apply only the human move and return immediately.

    Keeping the Laya inference in a separate request lets the browser render
    the human piece movement before the model is downloaded/loaded or thinks.
    """
    with lock:
        try:
            game.human_move(req.move_id)
            return game.snapshot()
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))


@app.post("/api/ai-move")
def ai_move():
    with lock:
        try:
            game.ai_move()
            return game.snapshot()
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))


@app.post("/api/move")
def make_move_compat(req: MoveRequest):
    """Backward-compatible endpoint for older clients."""
    with lock:
        try:
            game.human_move(req.move_id)
            if not game.game_over:
                game.ai_move()
            return game.snapshot()
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))


HTML = r"""<!doctype html>
<html lang="de">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Laya Checkers</title>
<style>
:root{color-scheme:dark;--bg:#0a0b10;--panel:#12141d;--line:#272b3a;--text:#f3f4f8;--muted:#9da4b5;--accent:#ec3bbd;--light:#d8d2c4;--dark:#403b38}
*{box-sizing:border-box} body{margin:0;background:radial-gradient(circle at 20% 0%,#19122a 0,#0a0b10 38%);color:var(--text);font:15px/1.45 Inter,system-ui,Segoe UI,sans-serif}
header{max-width:1180px;margin:auto;padding:28px 24px 12px;display:flex;justify-content:space-between;align-items:end;gap:20px}
h1{margin:0;font-size:30px}.tag{color:var(--muted)} .brand{color:var(--accent)}
main{max-width:1180px;margin:auto;padding:18px 24px 40px;display:grid;grid-template-columns:minmax(400px,680px) minmax(280px,1fr);gap:26px}
.card{background:rgba(18,20,29,.92);border:1px solid var(--line);border-radius:18px;box-shadow:0 24px 70px rgba(0,0,0,.28)}
.board-wrap{padding:16px}.board{aspect-ratio:1;display:grid;grid-template-columns:repeat(8,1fr);overflow:hidden;border-radius:12px;border:1px solid #000}
.sq{position:relative;display:grid;place-items:center;cursor:default;user-select:none}.sq.light{background:var(--light)}.sq.dark{background:var(--dark)}.sq.clickable{cursor:pointer}
.sq.selected{outline:5px solid var(--accent);outline-offset:-5px}.sq.target:after{content:"";pointer-events:none;width:22%;height:22%;border-radius:50%;background:rgba(255,255,255,.55);box-shadow:0 0 0 5px rgba(236,59,189,.28)}
.piece{width:72%;height:72%;border-radius:50%;display:grid;place-items:center;box-shadow:inset 0 0 0 4px rgba(255,255,255,.12),0 8px 16px rgba(0,0,0,.35);font-size:28px;font-weight:800}.piece[draggable="true"]{cursor:grab}.piece[draggable="true"]:active{cursor:grabbing}
.piece.black{background:#111;color:#eee;border:2px solid #666}.piece.red{background:#bf2541;color:#fff;border:2px solid #f08396}.king:after{content:"♛";font-size:.8em}
.side{padding:20px}.status{font-size:18px;font-weight:700;margin-bottom:4px}.muted{color:var(--muted)}
button{background:var(--accent);border:0;color:white;font-weight:750;border-radius:11px;padding:10px 14px;cursor:pointer}button.secondary{background:#252938}
.row{display:flex;gap:10px;align-items:center;flex-wrap:wrap}.panel{padding:18px;margin-top:16px}.panel h2{font-size:16px;margin:0 0 10px}
pre{margin:0;white-space:pre-wrap;word-break:break-word;background:#0b0d13;border:1px solid var(--line);padding:12px;border-radius:10px;color:#cbd2df;max-height:360px;overflow:auto;font-size:12px}
.badge{display:inline-flex;padding:4px 8px;border-radius:999px;background:#24283a;color:#dfe3ee;font-size:12px}.badge.laya{background:#4d123f;color:#ffb9ec}
@media(max-width:850px){main{grid-template-columns:1fr}header{align-items:start;flex-direction:column}.board-wrap{padding:10px}}
</style>
</head>
<body>
<header>
  <div><h1><span class="brand">Laya</span> Checkers</h1><div class="tag">Du spielst Schwarz. Laya spielt Rot.</div></div>
  <div class="row"><span id="engineBadge" class="badge">Laya wartet</span><button onclick="newGame()">Neues Spiel</button></div>
</header>
<main>
  <section class="card board-wrap"><div id="board" class="board"></div></section>
  <aside>
    <section class="card side">
      <div id="status" class="status">Lade Spiel…</div>
      <div id="substatus" class="muted">Schlagen ist Pflicht.</div>
      <div class="panel" style="padding:18px 0 0;margin-top:12px;border-top:1px solid var(--line)">
        <h2>Bedienung</h2>
        <div class="muted">Klicke zuerst deinen schwarzen Stein und danach das markierte Zielfeld. Du kannst die schwarzen Steine jetzt auch per Drag & Drop ziehen. Beim ersten KI-Zug kann das Laden von Laya länger dauern.</div>
      </div>
    </section>
    <section class="card panel">
      <h2>Laya-Entscheidung</h2>
      <pre id="debug">Noch kein KI-Zug.</pre>
    </section>
  </aside>
</main>
<script>
let state=null, selected=null, busy=false, draggedFrom=null;

const pieceClass = p => p.toLowerCase()==='b' ? 'black' : 'red';

function humanMoves(){
  if(!state || state.turn!=='black' || state.game_over || busy) return [];
  return state.legal_moves || [];
}

function movesFrom(r,c){ return humanMoves().filter(m=>m.start[0]===r && m.start[1]===c); }

function findMoveTo(r,c){
  if(!selected) return null;
  return movesFrom(selected[0],selected[1]).find(m=>m.end[0]===r && m.end[1]===c) || null;
}

function render(){
  const board=document.getElementById('board'); board.innerHTML='';
  const legal=humanMoves();
  const selectedMoves=selected ? movesFrom(selected[0],selected[1]) : [];

  for(let r=0;r<8;r++) for(let c=0;c<8;c++){
    const sq=document.createElement('div');
    sq.className='sq '+(((r+c)%2)?'dark':'light');
    const hasOwn=legal.some(m=>m.start[0]===r && m.start[1]===c);
    const isTarget=selectedMoves.some(m=>m.end[0]===r && m.end[1]===c);
    if(hasOwn||isTarget) sq.classList.add('clickable');
    if(selected && selected[0]===r && selected[1]===c) sq.classList.add('selected');
    if(isTarget) sq.classList.add('target');

    const p=state.board[r][c];
    if(p!=='.'){
      const el=document.createElement('div');
      el.className='piece '+pieceClass(p)+(p===p.toUpperCase()?' king':'');
      if(hasOwn){
        el.draggable=true;
        el.addEventListener('dragstart', ev=>{
          draggedFrom=[r,c];
          selected=[r,c];
          if(ev.dataTransfer) ev.dataTransfer.setData('text/plain', r+','+c);
          setTimeout(render,0);
        });
        el.addEventListener('dragend', ()=>{ draggedFrom=null; });
      }
      sq.appendChild(el);
    }

    sq.addEventListener('click', ()=>clickSquare(r,c));
    sq.addEventListener('dragover', ev=>{
      if(selected && selectedMoves.some(m=>m.end[0]===r && m.end[1]===c)) ev.preventDefault();
    });
    sq.addEventListener('drop', ev=>{
      ev.preventDefault();
      const move=findMoveTo(r,c);
      draggedFrom=null;
      if(move) executeMove(move);
    });
    board.appendChild(sq);
  }

  const st=document.getElementById('status');
  if(state.game_over) st.textContent = state.winner==='black' ? 'Du hast gewonnen.' : 'Laya hat gewonnen.';
  else if(busy) st.textContent='Laya lädt / denkt…';
  else st.textContent = state.turn==='black' ? 'Du bist am Zug.' : 'Laya ist am Zug.';

  if(state.last_move) document.getElementById('substatus').textContent='Letzter Zug: '+state.last_move.notation;
  if(state.last_ai){
    const d=state.last_ai;
    document.getElementById('engineBadge').className='badge '+(d.source==='laya'?'laya':'');
    document.getElementById('engineBadge').textContent=d.source==='laya'?'Laya aktiv':'Fallback aktiv';
    document.getElementById('debug').textContent=JSON.stringify({
      source:d.source, model:d.model, selected:d.selected, notation:d.notation,
      confidence:d.confidence, probabilities:d.probabilities, error:d.error||null
    },null,2);
  }
}

async function clickSquare(r,c){
  if(!state || state.game_over || state.turn!=='black' || busy) return;

  const from=movesFrom(r,c);
  if(from.length){
    selected=[r,c];
    render();
    return;
  }

  const move=findMoveTo(r,c);
  if(move){
    await executeMove(move);
    return;
  }

  selected=null;
  render();
}

async function executeMove(move){
  if(busy) return;
  busy=true;
  selected=null;

  try{
    // 1) Human move is committed separately so it becomes visible immediately.
    const humanResp=await fetch('/api/human-move',{
      method:'POST',
      headers:{'Content-Type':'application/json'},
      body:JSON.stringify({move_id:move.id})
    });
    const humanData=await humanResp.json();
    if(!humanResp.ok) throw new Error(humanData.detail||'Menschlicher Zug fehlgeschlagen');

    state=humanData;
    busy=false;
    render();

    if(state.game_over) return;

    // Give the browser a frame to paint the moved piece before Laya starts.
    await new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)));

    busy=true;
    render();
    document.getElementById('substatus').textContent='Dein Zug wurde ausgeführt. Laya lädt / denkt jetzt…';

    const aiResp=await fetch('/api/ai-move',{method:'POST'});
    const aiData=await aiResp.json();
    if(!aiResp.ok) throw new Error(aiData.detail||'Laya-Zug fehlgeschlagen');

    state=aiData;
  }catch(err){
    document.getElementById('debug').textContent='Fehler: '+(err && err.message ? err.message : String(err));
    try{
      state=await (await fetch('/api/state')).json();
    }catch(_){}
  }finally{
    busy=false;
    selected=null;
    render();
  }
}

async function load(){
  try{
    state=await (await fetch('/api/state')).json();
    selected=null;
    render();
  }catch(err){
    document.getElementById('status').textContent='Server nicht erreichbar.';
    document.getElementById('debug').textContent=String(err);
  }
}

async function newGame(){
  if(busy) return;
  state=await (await fetch('/api/new',{method:'POST'})).json();
  selected=null;
  busy=false;
  document.getElementById('debug').textContent='Noch kein KI-Zug.';
  document.getElementById('engineBadge').textContent='Laya wartet';
  render();
}

load();
</script>
</body>
</html>"""
