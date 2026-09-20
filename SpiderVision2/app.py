from __future__ import annotations

import threading
import time
from typing import Any

import cv2
from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse, Response
from pydantic import BaseModel, Field

from capture import capture_window, list_windows, window_info
from input_win import drag_client
from solver import legal_moves
from typesafe_chooser import TypeSafeError, choose as choose_typesafe, set_key as set_typesafe_key, status as typesafe_status, validate as validate_typesafe
from vision import BoardObservation, RANKS, SpiderVision


app = FastAPI(title="SpiderVision2", version="1.1.0")
vision = SpiderVision()
lock = threading.RLock()

selected_hwnd: int | None = None
last_frame = None
last_annotated = None
last_board: BoardObservation | None = None
current_plan: dict[str, Any] | None = None


class WindowRequest(BaseModel):
    hwnd: int


class ApiKeyRequest(BaseModel):
    api_key: str = ""


class LearnRequest(BaseModel):
    column: int = Field(ge=0, le=9)
    card_index: int = Field(ge=0)
    rank: str


def _signature(board: BoardObservation) -> tuple:
    return tuple(
        (
            col.status,
            bool(col.hidden_above),
            bool(col.empty_confident),
            tuple(card.rank for card in col.cards),
        )
        for col in board.columns
    )


def _require_window() -> int:
    if selected_hwnd is None:
        raise HTTPException(status_code=409, detail="Zuerst Solitaire-Fenster auswählen.")
    return selected_hwnd


def _analyze_locked() -> dict[str, Any]:
    global last_frame, last_annotated, last_board, current_plan
    hwnd = _require_window()
    frame = capture_window(hwnd)
    board, annotated = vision.detect(frame)
    last_frame = frame
    last_board = board
    last_annotated = annotated
    current_plan = None
    return {
        "window": window_info(hwnd).to_dict(),
        "board": board.to_dict(),
        "templates": vision.templates.summary(),
        "typesafe": typesafe_status(),
    }


def _draw_plan(frame, plan: dict[str, Any]):
    out = frame.copy()
    start = plan["start"]
    end = plan["end"]
    sx, sy = int(round(start[0])), int(round(start[1]))
    ex, ey = int(round(end[0])), int(round(end[1]))

    cv2.line(out, (sx, sy), (ex, ey), (0, 220, 255), 4, cv2.LINE_AA)
    cv2.circle(out, (sx, sy), 18, (20, 20, 255), 4, cv2.LINE_AA)
    cv2.circle(out, (ex, ey), 22, (40, 255, 40), 4, cv2.LINE_AA)
    cv2.putText(
        out,
        "START",
        (sx + 22, max(28, sy - 8)),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.68,
        (20, 20, 255),
        2,
        cv2.LINE_AA,
    )
    cv2.putText(
        out,
        "ZIEL",
        (ex + 26, max(28, ey - 8)),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.68,
        (40, 255, 40),
        2,
        cv2.LINE_AA,
    )
    cv2.rectangle(out, (12, 12), (min(out.shape[1] - 12, 720), 58), (10, 10, 10), -1)
    cv2.putText(
        out,
        "GEPLANT: " + plan["move"]["notation"],
        (24, 43),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.72,
        (0, 220, 255),
        2,
        cv2.LINE_AA,
    )
    return out


def _plan_locked() -> dict[str, Any]:
    global current_plan, last_annotated
    if last_board is None or last_frame is None:
        _analyze_locked()

    assert last_board is not None and last_frame is not None
    board = last_board

    if not board.complete:
        raise HTTPException(
            status_code=409,
            detail=(
                "Brett ist nicht sicher erkannt. Keine Mausaktion. "
                + " | ".join(board.diagnostics)
            ),
        )

    moves = legal_moves(board)
    if not moves:
        raise HTTPException(status_code=409, detail="Kein legaler Tableau-Zug gefunden.")

    try:
        move, model_debug = choose_typesafe(board, moves)
    except TypeSafeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    src_col = board.columns[move.source]
    dst_col = board.columns[move.destination]
    src_card = src_col.cards[move.start_index]

    start = (
        float(src_col.x),
        float(src_card.y + max(18.0, board.height * 0.030)),
    )

    if dst_col.cards:
        dst_card = dst_col.cards[-1]
        end = (
            float(dst_col.x),
            float(dst_card.y + max(28.0, board.height * 0.050)),
        )
    else:
        end = (
            float(dst_col.x),
            float(board.height * 0.225),
        )

    current_plan = {
        "move": move.to_dict(),
        "start": [round(start[0], 1), round(start[1], 1)],
        "end": [round(end[0], 1), round(end[1], 1)],
        "board_signature": _signature(board),
        "planned_at": time.time(),
        "legal_move_count": len(moves),
        "chooser": "TypeSafe/Jev",
        "model_debug": model_debug,
    }
    last_annotated = _draw_plan(vision.annotate(last_frame, board), current_plan)
    return current_plan


@app.get("/", response_class=HTMLResponse)
def index():
    return HTMLResponse(HTML)


@app.get("/api/windows")
def windows():
    return {"windows": [w.to_dict() for w in list_windows()]}


@app.post("/api/window")
def select_window(req: WindowRequest):
    global selected_hwnd, last_frame, last_annotated, last_board, current_plan
    with lock:
        selected_hwnd = int(req.hwnd)
        # Validate immediately.
        info = window_info(selected_hwnd)
        last_frame = None
        last_annotated = None
        last_board = None
        current_plan = None
        return {"ok": True, "window": info.to_dict()}


@app.post("/api/analyze")
def analyze():
    with lock:
        try:
            return _analyze_locked()
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(status_code=409, detail=f"{type(exc).__name__}: {exc}")


@app.post("/api/learn")
def learn(req: LearnRequest):
    with lock:
        rank = req.rank.upper().strip()
        if rank not in RANKS:
            raise HTTPException(status_code=400, detail="Rang muss A, 2-10, J, Q oder K sein.")
        try:
            path = vision.learn_last_patch(req.column, req.card_index, rank)
            result = _analyze_locked()
            result["learned"] = {
                "column": req.column + 1,
                "card_index": req.card_index,
                "rank": rank,
                "saved": path,
            }
            return result
        except Exception as exc:
            raise HTTPException(status_code=409, detail=f"{type(exc).__name__}: {exc}")


@app.post("/api/typesafe/key")
def typesafe_key(req: ApiKeyRequest):
    try:
        set_typesafe_key(req.api_key)
        result = validate_typesafe()
        return {
            "ok": True,
            "typesafe": result,
            "message": "TypeSafe/Jev API geprüft und bereit.",
        }
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"{type(exc).__name__}: {exc}")


@app.post("/api/plan")
def plan():
    with lock:
        try:
            # Always re-read immediately before planning.
            _analyze_locked()
            p = _plan_locked()
            return {"ok": True, "plan": p, "board": last_board.to_dict() if last_board else None}
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(status_code=409, detail=f"{type(exc).__name__}: {exc}")


@app.post("/api/execute")
def execute():
    global current_plan, last_frame, last_annotated, last_board
    with lock:
        hwnd = _require_window()
        if current_plan is None:
            raise HTTPException(status_code=409, detail="Kein bestätigter Plan vorhanden.")

        plan = dict(current_plan)

        # Fail closed: take a fresh screenshot and require exactly the same
        # recognized board before touching the mouse.
        fresh_frame = capture_window(hwnd)
        fresh_board, fresh_annotated = vision.detect(fresh_frame)

        if not fresh_board.complete:
            current_plan = None
            last_frame = fresh_frame
            last_board = fresh_board
            last_annotated = fresh_annotated
            raise HTTPException(
                status_code=409,
                detail="Brett vor Ausführung nicht mehr sicher erkannt. Maus bleibt stehen.",
            )

        if _signature(fresh_board) != plan["board_signature"]:
            current_plan = None
            last_frame = fresh_frame
            last_board = fresh_board
            last_annotated = fresh_annotated
            raise HTTPException(
                status_code=409,
                detail="Brett hat sich seit der Planung geändert. Alter Zug wurde verworfen.",
            )

        move = plan["move"]
        legal = {m.notation(): m for m in legal_moves(fresh_board)}
        if move["notation"] not in legal:
            current_plan = None
            raise HTTPException(
                status_code=409,
                detail="Geplanter Zug ist in der frisch gelesenen Stellung nicht mehr legal.",
            )

        drag_client(
            hwnd,
            tuple(plan["start"]),
            tuple(plan["end"]),
            duration=0.82,
            steps=48,
        )

        time.sleep(0.55)
        current_plan = None
        result = _analyze_locked()
        result["executed"] = move
        return result


@app.get("/api/status")
def status():
    info = None
    if selected_hwnd is not None:
        try:
            info = window_info(selected_hwnd).to_dict()
        except Exception:
            info = None
    return {
        "version": "1.1.0",
        "window": info,
        "board": last_board.to_dict() if last_board else None,
        "plan": current_plan,
        "templates": vision.templates.summary(),
        "typesafe": typesafe_status(),
        "rules": {
            "uia": False,
            "ocr": False,
            "llm_for_vision": False,
            "decision_model": "TypeSafe/Jev",
            "mouse_requires_complete_board": True,
            "mouse_requires_fresh_identical_board": True,
        },
    }


@app.get("/api/frame.jpg")
def frame_jpg():
    with lock:
        if last_annotated is None:
            _analyze_locked()
        assert last_annotated is not None
        ok, encoded = cv2.imencode(
            ".jpg",
            last_annotated,
            [int(cv2.IMWRITE_JPEG_QUALITY), 90],
        )
        if not ok:
            raise HTTPException(status_code=500, detail="JPEG konnte nicht erzeugt werden.")
        return Response(
            content=encoded.tobytes(),
            media_type="image/jpeg",
            headers={"Cache-Control": "no-store, max-age=0"},
        )


HTML = r"""<!doctype html>
<html lang="de">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>SpiderVision2</title>
<style>
:root{color-scheme:dark;--bg:#090b10;--panel:#121722;--line:#293246;--text:#eef3fb;--muted:#9ba9bf;--cyan:#47c9ef;--green:#62dc9a;--bad:#ff7186;--warn:#ffc65f}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--text);font:14px/1.45 Segoe UI,Arial,sans-serif}
header{max-width:1500px;margin:auto;padding:20px 22px 10px;display:flex;justify-content:space-between;align-items:end;gap:15px}
h1{margin:0;font-size:28px}h2{font-size:15px;margin:0 0 10px}.muted{color:var(--muted)}.ok{color:var(--green)}.bad{color:var(--bad)}.warn{color:var(--warn)}
main{max-width:1500px;margin:auto;padding:12px 22px 35px;display:grid;grid-template-columns:minmax(650px,1.55fr) minmax(400px,.85fr);gap:16px}
.card{background:var(--panel);border:1px solid var(--line);border-radius:14px;padding:14px}.stack{display:grid;gap:14px}
img{width:100%;display:block;border-radius:10px;background:#050607;border:1px solid #000;min-height:360px;object-fit:contain}
.row{display:flex;gap:8px;align-items:center;flex-wrap:wrap}button,select,input{background:#242d40;color:#fff;border:1px solid #39445c;border-radius:9px;padding:9px 11px;min-height:39px}input{min-width:220px;flex:1}
button{font-weight:700;cursor:pointer}.primary{background:#2184a0}.danger{background:#743140}select{min-width:130px}
pre{background:#090c12;border:1px solid var(--line);border-radius:9px;padding:10px;max-height:320px;overflow:auto;white-space:pre-wrap}
table{width:100%;border-collapse:collapse;font-size:12px}th,td{padding:6px;border-bottom:1px solid #263044;text-align:left}td select{min-height:31px;padding:4px 7px}
.badge{display:inline-block;border-radius:999px;background:#273147;padding:4px 8px;font-size:12px}
.notice{padding:10px;border:1px solid #5c4b20;background:#211c0e;border-radius:9px;color:#ffd885}
@media(max-width:980px){main{grid-template-columns:1fr}}
</style>
</head>
<body>
<header>
<div>
<h1>SpiderVision2</h1>
<div class="muted">Screenshot → OpenCV → 13 Rang-Templates → legale Züge → TypeSafe/Jev → Maus</div>
</div>
<div class="badge">v1.1 · Vision ohne UIA/OCR/LLM · Entscheidung mit TypeSafe/Jev</div>
</header>
<main>
<section class="stack">
<div class="card">
<div class="row" style="justify-content:space-between;margin-bottom:10px">
<div id="boardBadge" class="badge">Noch nicht analysiert</div>
<div class="muted">10 Spalten werden immer separat dargestellt</div>
</div>
<img id="frame" alt="Analysiertes Solitaire-Fenster">
</div>
<div class="card">
<h2>Erkanntes Brett</h2>
<pre id="boardDump">Noch keine Analyse.</pre>
</div>
</section>

<aside class="stack">
<div class="card">
<h2>1 · Solitaire-Fenster</h2>
<div class="row">
<select id="windows" style="flex:1"></select>
<button id="refresh">Aktualisieren</button>
<button id="select" class="primary">Auswählen</button>
</div>
</div>

<div class="card">
<h2>2 · TypeSafe / Jev</h2>
<div class="muted" style="margin-bottom:8px">
Die Bild-Erkennung läuft lokal ohne LLM. TypeSafe/Jev bekommt danach ausschließlich die bereits regelgeprüften legalen Züge und wählt einen davon aus.
</div>
<div class="row">
<input id="apiKey" type="password" placeholder="TypeSafe API-Key">
<button id="connectTypeSafe" class="primary">API verbinden</button>
</div>
<div id="typesafeStatus" class="muted" style="margin-top:8px">Nicht verbunden.</div>
</div>

<div class="card">
<h2>3 · Erst lesen, dann bewegen</h2>
<div class="notice">
SpiderVision2 bewegt die Maus nur, wenn alle 10 Spalten eindeutig als erkannt oder leer gelten. Direkt vor der Mausbewegung wird das Brett erneut gelesen und muss exakt mit dem geplanten Brett übereinstimmen.
</div>
<div class="row" style="margin-top:10px">
<button id="analyze">Brett analysieren</button>
<button id="plan">Zug planen</button>
<button id="safeStep" class="primary">Sicherer Schritt</button>
</div>
<div id="message" style="margin-top:9px" class="muted"></div>
</div>

<div class="card">
<h2>4 · Karten-Erkennung anlernen</h2>
<div class="muted" style="margin-bottom:8px">
Wenn ein Rang mit ? oder falsch angezeigt wird, wähle hier den echten Rang. Der Bildausschnitt wird lokal als Template gespeichert. Keine Cloud, kein OCR.
</div>
<div style="max-height:330px;overflow:auto">
<table>
<thead><tr><th>Spalte</th><th>Karte</th><th>Erkannt</th><th>Korrektur</th><th></th></tr></thead>
<tbody id="cards"></tbody>
</table>
</div>
</div>

<div class="card">
<h2>Gelernte Templates</h2>
<pre id="templates"></pre>
</div>
</aside>
</main>
<script>
const $=id=>document.getElementById(id);
let statusData=null;
const ranks=["A","2","3","4","5","6","7","8","9","10","J","Q","K"];

async function api(url,opts={}){
  const r=await fetch(url,{headers:{'Content-Type':'application/json'},...opts});
  let data={}; try{data=await r.json()}catch{}
  if(!r.ok) throw new Error(data.detail||('HTTP '+r.status));
  return data;
}
function refreshFrame(){ $('frame').src='/api/frame.jpg?t='+Date.now(); }

async function loadWindows(){
  try{
    const d=await api('/api/windows');
    $('windows').innerHTML='';
    d.windows.forEach(w=>{
      const o=document.createElement('option');
      o.value=w.hwnd;o.textContent=w.title+' · '+w.width+'×'+w.height;
      $('windows').appendChild(o);
    });
  }catch(e){$('message').textContent=e.message}
}

async function selectWindow(){
  try{
    await api('/api/window',{method:'POST',body:JSON.stringify({hwnd:Number($('windows').value)})});
    $('message').textContent='Fenster ausgewählt.';
    await analyze();
  }catch(e){$('message').textContent=e.message}
}

function render(d){
  statusData=d;
  const ts=d.typesafe||{};
  if(ts.validated){
    $('typesafeStatus').innerHTML='<span class="ok">API geprüft & bereit · '+(ts.model||'Jev')+'</span>';
  }else if(ts.configured && ts.validation_error){
    $('typesafeStatus').innerHTML='<span class="bad">API-Fehler: '+ts.validation_error+'</span>';
  }else if(ts.configured){
    $('typesafeStatus').innerHTML='<span class="warn">API-Key gespeichert, noch nicht geprüft</span>';
  }else{
    $('typesafeStatus').textContent='Nicht verbunden.';
  }
  const b=d.board;
  $('templates').textContent=JSON.stringify(d.templates||{},null,2);
  if(!b) return;
  $('boardBadge').textContent=b.complete?'BOARD OK · Maus freigegeben':'BOARD NICHT SICHER · Maus gesperrt';
  $('boardBadge').className='badge '+(b.complete?'ok':'bad');

  const concise={
    complete:b.complete,
    uncertain_cards:b.uncertain_cards,
    columns:b.columns.map(c=>({
      column:c.index+1,
      status:c.status,
      hidden_above:c.hidden_above,
      cards:c.cards.map(x=>x.rank+' ('+Number(x.confidence).toFixed(2)+')')
    })),
    diagnostics:b.diagnostics,
    plan:d.plan||null,
    typesafe:d.typesafe||null
  };
  $('boardDump').textContent=JSON.stringify(concise,null,2);

  const tbody=$('cards');tbody.innerHTML='';
  b.columns.forEach(c=>{
    c.cards.forEach((card,idx)=>{
      const tr=document.createElement('tr');
      const options=ranks.map(r=>'<option value="'+r+'">'+r+'</option>').join('');
      tr.innerHTML='<td>C'+(c.index+1)+'</td><td>'+(idx+1)+'</td><td>'+card.rank+' · '+Number(card.confidence).toFixed(2)+'</td>'+
        '<td><select>'+options+'</select></td><td><button>Lernen</button></td>';
      const sel=tr.querySelector('select');
      if(card.rank!=='?') sel.value=card.rank;
      tr.querySelector('button').onclick=()=>learn(c.index,idx,sel.value);
      tbody.appendChild(tr);
    });
  });
}

async function connectTypeSafe(){
  try{
    const key=$('apiKey').value.trim();
    $('typesafeStatus').textContent='TypeSafe wird geprüft…';
    const d=await api('/api/typesafe/key',{method:'POST',body:JSON.stringify({api_key:key})});
    $('typesafeStatus').innerHTML='<span class="ok">API geprüft & bereit · '+(d.typesafe.model||'Jev')+'</span>';
    $('message').textContent='TypeSafe/Jev verbunden.';
  }catch(e){
    $('typesafeStatus').innerHTML='<span class="bad">'+e.message+'</span>';
    $('message').textContent=e.message;
  }
}

async function analyze(){
  try{
    $('message').textContent='Screenshot wird analysiert…';
    const d=await api('/api/analyze',{method:'POST'});
    render(d);refreshFrame();
    $('message').textContent=d.board.complete?'Brett vollständig erkannt.':'Noch unsicher – Maus bleibt gesperrt.';
  }catch(e){$('message').textContent=e.message}
}

async function learn(column,card_index,rank){
  try{
    const d=await api('/api/learn',{method:'POST',body:JSON.stringify({column,card_index,rank})});
    render(d);refreshFrame();
    $('message').textContent='Template für '+rank+' gespeichert und Brett neu gelesen.';
  }catch(e){$('message').textContent=e.message}
}

async function plan(){
  try{
    const d=await api('/api/plan',{method:'POST'});
    const st=await api('/api/status');
    render(st);refreshFrame();
    $('message').textContent='TypeSafe/Jev gewählt: '+d.plan.move.notation+' · noch keine Mausbewegung.';
    return d.plan;
  }catch(e){$('message').textContent=e.message;throw e}
}

async function safeStep(){
  try{
    const p=await plan();
    $('message').textContent='Prüfe Plan '+p.move.notation+' · Ausführung in 1,5 s…';
    await new Promise(r=>setTimeout(r,1500));
    const d=await api('/api/execute',{method:'POST'});
    render(d);refreshFrame();
    $('message').textContent='Ausgeführt: '+d.executed.notation;
  }catch(e){$('message').textContent=e.message}
}

$('refresh').onclick=loadWindows;
$('select').onclick=selectWindow;
$('connectTypeSafe').onclick=connectTypeSafe;
$('analyze').onclick=analyze;
$('plan').onclick=plan;
$('safeStep').onclick=safeStep;

loadWindows();
setInterval(async()=>{
  try{
    const s=await api('/api/status');
    render(s); if(s.window){refreshFrame()}
  }catch{}
},1000);
</script>
</body>
</html>"""
