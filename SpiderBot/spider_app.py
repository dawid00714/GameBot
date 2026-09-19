from __future__ import annotations

import ctypes
from ctypes import wintypes
import os
import threading
import time
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse, Response
from pydantic import BaseModel, Field

import spider_models
import spider_ollama
from spider_agent import SpiderAgent, SpiderAgentError
from spider_state import ocr_status, warm_ocr
from spider_windows import WindowAutomationError, list_windows
from vm_guest_input import clear_input_abort, request_input_abort

app = FastAPI(title="Laya / TypeSafe Windows Spider Agent", version="5.0.0")
agent = SpiderAgent()
agent_lock = threading.RLock()
step_lock = threading.Lock()
run_stop = threading.Event()
run_thread: threading.Thread | None = None
runtime = {
    "running": False,
    "stopping": False,
    "last_loop_error": None,
    "steps": 0,
    "started_at": None,
    "hotkey": "Alt+L",
    "hotkey_registered": False,
    "hotkey_error": None,
    "hotkey_last": None,
}


class WindowRequest(BaseModel):
    hwnd: int


class ApiKeyRequest(BaseModel):
    api_key: str = ""


class ConfigRequest(BaseModel):
    model: str = "laya"
    depth: int = Field(default=3, ge=1, le=5)
    learning: bool = True
    action_delay: float = Field(default=0.85, ge=0.15, le=5.0)
    vision_enabled: bool = False
    vision_model: str = ""


class StockPointRequest(BaseModel):
    x: float = Field(ge=0.0, le=1.0)
    y: float = Field(ge=0.0, le=1.0)


def _auto_select_solitaire() -> None:
    try:
        wins = list_windows("Solitaire")
        if not wins:
            wins = list_windows("Casual Games")
        if wins:
            agent.select_window(wins[0].hwnd)
    except Exception:
        pass


_auto_select_solitaire()

# Warm RapidOCR in the background only as a safety fallback. This avoids a
# 10-15 second pause later if Ollama returns an unusable board once.
threading.Thread(target=warm_ocr, name="spider-ocr-warmup", daemon=True).start()


def _runner() -> None:
    try:
        while not run_stop.is_set():
            try:
                with step_lock:
                    agent.step()
                runtime["steps"] += 1
                if agent.game_finished:
                    break
            except Exception as exc:
                runtime["last_loop_error"] = f"{type(exc).__name__}: {exc}"
                try:
                    agent._set_phase("error", runtime["last_loop_error"])
                except Exception:
                    pass
                break
            # The expensive work happens in agent.step(); this tiny pause only
            # yields CPU to the web UI.
            time.sleep(0.08)
    finally:
        runtime["running"] = False
        runtime["stopping"] = False


def _start_runner() -> None:
    global run_thread
    if runtime["running"]:
        return
    clear_input_abort()
    run_stop.clear()
    runtime["last_loop_error"] = None
    runtime["stopping"] = False
    runtime["running"] = True
    runtime["started_at"] = time.time()
    run_thread = threading.Thread(target=_runner, name="spider-agent-runner", daemon=True)
    run_thread.start()


def _stop_runner() -> None:
    request_input_abort()
    run_stop.set()
    if runtime["running"]:
        runtime["stopping"] = True


def _status() -> dict[str, Any]:
    # IMPORTANT: never wait for the long-running move lock here. Status polling
    # must stay responsive while OCR, Ollama, TypeSafe or input verification is
    # running.
    data = agent.status()
    data["runtime"] = dict(runtime)
    return data


def _preflight_start() -> None:
    if agent.controller is None:
        raise SpiderAgentError("Zuerst Spielfenster auswählen.")

    if agent.config.model == "typesafe":
        ts = spider_models.typesafe_status()
        if not ts.get("validated"):
            raise SpiderAgentError(
                ts.get("validation_error")
                or "TypeSafe API-Key wurde noch nicht erfolgreich geprüft."
            )

    if agent.config.vision_enabled and not agent.config.vision_model:
        raise SpiderAgentError(
            "Ollama-Vision ist aktiviert, aber kein Vision-Modell ausgewählt."
        )


def _toggle_from_hotkey() -> None:
    if runtime["running"]:
        _stop_runner()
        runtime["hotkey_last"] = "stop"
        return

    try:
        _preflight_start()
        runtime["last_loop_error"] = None
        _start_runner()
        runtime["hotkey_last"] = "start"
    except Exception as exc:
        runtime["last_loop_error"] = f"{type(exc).__name__}: {exc}"
        runtime["hotkey_last"] = "error"


def _hotkey_loop() -> None:
    """Robust global Alt+L detector.

    RegisterHotKey can fail or be swallowed by other Windows software. Polling
    GetAsyncKeyState reads the physical key state system-wide and therefore
    works even while Solitaire has focus.
    """
    if os.name != "nt":
        runtime["hotkey_error"] = "Globaler Hotkey ist nur unter Windows verfügbar."
        return

    user32 = ctypes.windll.user32
    VK_MENU = 0x12
    VK_L = 0x4C

    runtime["hotkey_registered"] = True
    runtime["hotkey_error"] = None
    was_down = False

    while True:
        try:
            alt_down = bool(user32.GetAsyncKeyState(VK_MENU) & 0x8000)
            l_down = bool(user32.GetAsyncKeyState(VK_L) & 0x8000)
            combo = alt_down and l_down

            if combo and not was_down:
                _toggle_from_hotkey()
                runtime["hotkey_last"] = (
                    "stop" if runtime["stopping"] else "start"
                ) if runtime["hotkey_last"] != "error" else "error"

            was_down = combo
            time.sleep(0.025)
        except Exception as exc:
            runtime["hotkey_registered"] = False
            runtime["hotkey_error"] = f"{type(exc).__name__}: {exc}"
            time.sleep(1.0)


threading.Thread(
    target=_hotkey_loop,
    name="spider-alt-l-poller",
    daemon=True,
).start()


@app.get("/", response_class=HTMLResponse)
def index():
    return HTMLResponse(HTML)


@app.get("/health")
def health():
    return {
        "ok": True,
        "version": "5.0.0",
        "windows_agent": True,
        "ocr_fallback": ocr_status(),
    }


@app.get("/api/ollama/models")
def ollama_models():
    try:
        return spider_ollama.list_vision_models()
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"{type(exc).__name__}: {exc}")


@app.get("/api/windows")
def windows():
    try:
        return {"windows": [w.to_dict() for w in list_windows()]}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"{type(exc).__name__}: {exc}")


@app.post("/api/window")
def select_window(req: WindowRequest):
    _stop_runner()
    try:
        with agent_lock:
            agent.select_window(req.hwnd)
            agent.reset_episode()
            return agent.status()
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"{type(exc).__name__}: {exc}")


@app.post("/api/config")
def configure(req: ConfigRequest):
    try:
        with agent_lock:
            agent.set_model(req.model)
            agent.configure(
                depth=req.depth,
                learning=req.learning,
                action_delay=req.action_delay,
                vision_enabled=req.vision_enabled,
                vision_model=req.vision_model,
            )
            data = agent.status()

        # Lazy-load Laya only when the user actually selects Laya. This avoids
        # spending minutes reconstructing/loading a 322M checkpoint when the
        # user wants TypeSafe/Jev instead.
        if req.model.strip().lower() == "laya":
            spider_models.start_laya()
            data["laya"] = spider_models.laya_status()
        return data
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"{type(exc).__name__}: {exc}")


@app.post("/api/stock-point")
def stock_point(req: StockPointRequest):
    with agent_lock:
        agent.set_stock_point(req.x, req.y)
        return agent.status()


@app.post("/api/typesafe/key")
def typesafe_key(req: ApiKeyRequest):
    try:
        with agent_lock:
            agent.set_typesafe_key(req.api_key)
            status = spider_models.validate_typesafe()
        return {
            "ok": True,
            "typesafe": status,
            "message": "TypeSafe API-Key geprüft und Verbindung erfolgreich.",
        }
    except Exception as exc:
        raise HTTPException(
            status_code=400,
            detail=f"{type(exc).__name__}: {exc}",
        )


@app.post("/api/laya/retry")
def retry_laya():
    spider_models.retry_laya(clear_local=False)
    return {"ok": True, "laya": spider_models.laya_status()}


@app.post("/api/observe")
def observe():
    if runtime["running"]:
        raise HTTPException(status_code=409, detail="Agent läuft bereits automatisch.")
    if not step_lock.acquire(blocking=False):
        raise HTTPException(status_code=409, detail="Eine Analyse/Aktion läuft bereits.")
    try:
        agent.observe(use_vision=True)
        return agent.status()
    except Exception as exc:
        raise HTTPException(status_code=409, detail=f"{type(exc).__name__}: {exc}")
    finally:
        step_lock.release()


@app.post("/api/step")
def step():
    if runtime["running"]:
        raise HTTPException(status_code=409, detail="Agent läuft bereits automatisch.")
    if not step_lock.acquire(blocking=False):
        raise HTTPException(status_code=409, detail="Eine Analyse/Aktion läuft bereits.")
    try:
        agent.step()
        runtime["steps"] += 1
        runtime["last_loop_error"] = None
        return agent.status()
    except Exception as exc:
        runtime["last_loop_error"] = f"{type(exc).__name__}: {exc}"
        raise HTTPException(status_code=409, detail=runtime["last_loop_error"])
    finally:
        step_lock.release()


@app.post("/api/run/start")
def run_start():
    try:
        _preflight_start()
    except Exception as exc:
        raise HTTPException(status_code=409, detail=f"{type(exc).__name__}: {exc}")

    runtime["last_loop_error"] = None
    _start_runner()
    return _status()


@app.post("/api/run/stop")
def run_stop_endpoint():
    _stop_runner()
    return _status()


@app.post("/api/episode/reset")
def episode_reset():
    _stop_runner()
    with agent_lock:
        agent.reset_episode()
        return agent.status()


@app.post("/api/learning/reset")
def learning_reset():
    _stop_runner()
    with agent_lock:
        agent.learning.reset()
        return {"ok": True, "learning": agent.learning.summary()}


@app.get("/api/status")
def status():
    return _status()


@app.get("/api/frame.jpg")
def frame():
    try:
        data = agent.frame_jpeg()
        return Response(
            content=data,
            media_type="image/jpeg",
            headers={"Cache-Control": "no-store, max-age=0"},
        )
    except Exception as exc:
        raise HTTPException(status_code=409, detail=f"{type(exc).__name__}: {exc}")


HTML = r"""<!doctype html>
<html lang="de">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Windows Spider Agent</title>
<style>
:root{color-scheme:dark;--bg:#090a0f;--panel:#12151d;--line:#2a2f3d;--text:#f5f6f8;--muted:#9da7ba;--pink:#ed3dbd;--cyan:#45c7e8;--green:#63d79c;--bad:#ff7e8d;--warn:#f4bc62}
*{box-sizing:border-box}body{margin:0;background:radial-gradient(circle at 16% 0,#181122 0,#090a0f 42%);color:var(--text);font:14px/1.45 Inter,system-ui,Segoe UI,sans-serif}
header{max-width:1500px;margin:auto;padding:22px 20px 10px;display:flex;justify-content:space-between;gap:14px;align-items:end;flex-wrap:wrap}
h1{margin:0;font-size:28px}.pink{color:var(--pink)}.cyan{color:var(--cyan)}.muted{color:var(--muted)}.small{font-size:12px}
main{max-width:1500px;margin:auto;padding:12px 20px 40px;display:grid;grid-template-columns:minmax(560px,1.55fr) minmax(360px,.85fr);gap:18px}
.card{background:rgba(18,21,29,.96);border:1px solid var(--line);border-radius:15px}.panel{padding:15px}.stack{display:grid;gap:12px}
.preview{padding:10px}.preview img{width:100%;display:block;border-radius:10px;background:#050608;border:1px solid #000;cursor:crosshair;min-height:320px;object-fit:contain}
.row{display:flex;align-items:center;gap:8px;flex-wrap:wrap}.grid2{display:grid;grid-template-columns:1fr 1fr;gap:9px}.grid3{display:grid;grid-template-columns:1fr 1fr 1fr;gap:9px}
select,input{background:#0b0d13;color:var(--text);border:1px solid var(--line);border-radius:9px;padding:9px 10px;min-height:40px;min-width:0}select{width:100%}input[type=password]{flex:1;min-width:190px}
button{border:0;border-radius:9px;padding:10px 12px;background:#282d3d;color:#fff;font-weight:750;cursor:pointer}button.primary{background:var(--pink)}button.cyan{background:#227f96;color:#fff}button.danger{background:#7d2d3a}button:disabled{opacity:.45;cursor:not-allowed}
.status{font-weight:800;font-size:17px}.ok{color:var(--green)}.bad{color:var(--bad)}.warn{color:var(--warn)}
h2{font-size:15px;margin:0 0 9px}label{color:var(--muted);font-size:12px}.check{display:flex;align-items:center;gap:7px}.check input{min-height:auto}
pre{margin:0;background:#0b0d13;border:1px solid var(--line);border-radius:9px;padding:10px;white-space:pre-wrap;word-break:break-word;max-height:300px;overflow:auto;color:#cdd4e2;font-size:12px}
.badge{display:inline-flex;padding:4px 8px;border-radius:999px;background:#282d3d;font-size:12px}
hr{border:0;border-top:1px solid var(--line);margin:12px 0}
@media(max-width:950px){main{grid-template-columns:1fr}.grid3{grid-template-columns:1fr}.preview img{min-height:220px}}
</style>
</head>
<body>
<header>
  <div>
    <h1><span class="pink">Laya</span> / <span class="cyan">TypeSafe Jev</span> · Windows Spider Agent</h1>
    <div class="muted">Echte Windows-Maus aktiv · Alt+L startet/stoppt den Agenten sofort · Live-Screenshot</div>
  </div>
  <div class="small muted">Build 5.0</div>
</header>

<main>
  <section class="stack">
    <div class="card preview">
      <div class="row" style="justify-content:space-between;margin:2px 2px 9px">
        <div>
          <span id="runBadge" class="badge">gestoppt</span>
          <span id="modelBadge" class="badge">Laya</span>
          <span id="readerBadge" class="badge">Vision: —</span>
        </div>
        <div class="small muted">Stock und Kartenpositionen werden jetzt automatisch erkannt</div>
      </div>
      <img id="frame" alt="Live-Screenshot des ausgewählten Solitaire-Fensters">
    </div>

    <div class="card panel">
      <h2>Erkannter Spielzustand</h2>
      <pre id="stateDump">Noch kein Fenster beobachtet.</pre>
    </div>
  </section>

  <aside class="stack">
    <section class="card panel">
      <h2>1 · Windows-Spielfenster</h2>
      <div class="row">
        <select id="windowSelect" style="flex:1"><option>Lade Fenster…</option></select>
        <button id="refreshWindows">Aktualisieren</button>
        <button id="selectWindow" class="cyan">Auswählen</button>
      </div>
      <div id="windowHint" class="small muted" style="margin-top:7px">Gesucht wird insbesondere „Solitaire & Casual Games“.</div>
    </section>

    <section class="card panel">
      <h2>2 · Modell</h2>
      <div class="grid2">
        <div><label>Spielendes Modell</label><select id="model"><option value="laya">Laya lokal</option><option value="typesafe">TypeSafe / Jev</option></select></div>
        <div><label>Vorausschau</label><select id="depth"><option>1</option><option>2</option><option selected>3</option><option>4</option><option>5</option></select></div>
      </div>
      <div class="row" style="margin-top:9px">
        <label class="check"><input id="learning" type="checkbox" checked> Selbstlernen aktiv</label>
      </div>
      <hr>
      <div class="row">
        <input id="apiKey" type="password" placeholder="TypeSafe API-Key">
        <button id="saveKey" class="cyan">API verbinden</button>
      </div>
      <div id="modelStatus" class="small muted" style="margin-top:8px"></div>
      <hr>
      <div class="row">
        <label class="check"><input id="visionEnabled" type="checkbox"> Ollama nur bei unsicherer UIA/FastOCR-Erkennung verwenden</label>
      </div>
      <div class="row" style="margin-top:8px">
        <select id="visionModel" style="flex:1"><option value="">Vision-Modelle laden…</option></select>
        <button id="refreshOllama">Ollama aktualisieren</button>
      </div>
      <div id="ollamaStatus" class="small muted" style="margin-top:7px">UIA/FastOCR hat Vorrang. Ollama wird nur aufgerufen, wenn die normale Erkennung nicht ausreicht.</div>
    </section>

    <section class="card panel">
      <h2>3 · Echter Maus-Drag / Stock</h2>
      <div class="grid2">
        <div><label>Wartezeit pro Aktion</label><select id="delay"><option value=".45">0,45 s</option><option value=".85" selected>0,85 s</option><option value="1.2">1,2 s</option><option value="1.8">1,8 s</option></select></div>
        <div>
          <label>Eingabemodus</label>
          <div id="inputModeBadge" class="badge ok" style="margin-top:7px">Eingabemodus wird erkannt…</div>
        </div>
      </div>
      <div id="stockStatus" class="small muted" style="margin-top:8px">Stock wird automatisch gesucht…</div>
      <div class="small muted" style="margin-top:5px">Kartenerkennung läuft standardmäßig über FastOCR. SpiderBot verwendet jetzt deine echte Windows-Maus: Quellkarte anfahren → LEFTDOWN → während der gesamten Bewegung gedrückt halten → am Ziel LEFTUP. Mit Alt+L kannst du den Agenten global starten oder stoppen; bei Stop wird eine laufende Ziehbewegung abgebrochen und die linke Taste freigegeben.</div>
    </section>

    <section class="card panel">
      <h2>4 · Agent</h2>
      <div id="mainStatus" class="status">Bereit zum Verbinden.</div>
      <div id="error" class="small bad" style="margin:6px 0"></div>
      <div class="row" style="margin-top:10px">
        <button id="observe">Diagnose / Karten lesen</button>
        <button id="step">1 Aktion</button>
        <button id="start" class="primary">Start</button>
        <button id="stop">Stop</button>
        <button id="resetEpisode">Neue Partie / Speicher</button>
      </div>
    </section>

    <section class="card panel">
      <h2>Letzte Modellentscheidung</h2>
      <pre id="actionDump">Noch keine Aktion.</pre>
    </section>

    <section class="card panel">
      <h2>Lernen</h2>
      <pre id="learningDump">Noch keine Daten.</pre>
      <div class="row" style="margin-top:8px"><button id="resetLearning" class="danger">Lerndaten löschen</button><button id="retryLaya">Laya neu laden</button></div>
    </section>
  </aside>
</main>

<script>
const $=id=>document.getElementById(id);
let statusData=null;
let frameTimer=null;

function esc(s){return String(s??'')}

async function api(path, options={}){
  const r=await fetch(path,options);
  let data={};
  try{data=await r.json()}catch(_){}
  if(!r.ok) throw new Error(data.detail||('HTTP '+r.status));
  return data;
}

async function refreshWindows(){
  try{
    const data=await api('/api/windows');
    const sel=$('windowSelect');
    sel.innerHTML='';
    for(const w of data.windows){
      const o=document.createElement('option');
      o.value=w.hwnd;
      o.textContent=w.title+' · '+w.width+'×'+w.height+' · HWND '+w.hwnd;
      sel.appendChild(o);
    }
    if(!data.windows.length){
      const o=document.createElement('option');o.textContent='Kein geeignetes Fenster gefunden';sel.appendChild(o);
    }
  }catch(e){$('windowHint').textContent=e.message}
}

async function selectWindow(){
  const hwnd=parseInt($('windowSelect').value,10);
  if(!Number.isFinite(hwnd)) return;
  try{
    await api('/api/window',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({hwnd})});
    $('windowHint').textContent='Fenster verbunden. Die virtuelle Maus sendet Eingaben nur an dieses Fenster.';
    await refreshStatus();
    refreshFrame();
  }catch(e){$('windowHint').textContent=e.message}
}

async function saveConfig(){
  const payload={
    model:$('model').value,
    depth:parseInt($('depth').value,10),
    learning:$('learning').checked,
    action_delay:parseFloat($('delay').value),
    vision_enabled:$('visionEnabled').checked,
    vision_model:$('visionModel').value
  };
  await api('/api/config',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)});
}

async function refreshOllama(){
  const sel=$('visionModel');
  const previous=sel.value;
  sel.innerHTML='<option value="">Suche lokale Vision-Modelle…</option>';
  try{
    const data=await api('/api/ollama/models');
    sel.innerHTML='';
    if(!data.vision_models?.length){
      const o=document.createElement('option');
      o.value='';
      o.textContent='Keine Ollama-Vision-Modelle erkannt';
      sel.appendChild(o);
      $('ollamaStatus').innerHTML='<span class="warn">Ollama läuft, aber kein Vision-Modell wurde erkannt.</span>';
      return;
    }
    for(const m of data.vision_models){
      const o=document.createElement('option');
      o.value=m.name;
      let label=m.name;
      if(m.parameter_size) label+=' · '+m.parameter_size;
      o.textContent=label;
      sel.appendChild(o);
    }
    if(previous && [...sel.options].some(o=>o.value===previous)){
      sel.value=previous;
    }else{
      // Prefer the smallest installed vision model for card reading. Spider
      // needs rank recognition, not long-form visual reasoning.
      const ranked=data.vision_models
        .map(m=>{
          const raw=String(m.parameter_size||'');
          const n=parseFloat(raw.replace(',','.'));
          const mult=/B/i.test(raw)?1000:/M/i.test(raw)?1:10000;
          return {name:m.name, score:Number.isFinite(n)?n*mult:100000};
        })
        .sort((a,b)=>a.score-b.score);
      if(ranked.length) sel.value=ranked[0].name;
    }
    $('ollamaStatus').innerHTML='<span class="ok">'+data.vision_models.length+' Vision-Modell(e) erkannt · optional verfügbar.</span>';
  }catch(e){
    sel.innerHTML='<option value="">Ollama nicht erreichbar</option>';
    $('ollamaStatus').innerHTML='<span class="bad">'+e.message+'</span>';
  }
}

async function saveKey(){
  try{
    const value=$('apiKey').value;
    if(!value.trim()){
      throw new Error('Bitte zuerst den TypeSafe API-Key einfügen.');
    }
    const data=await api('/api/typesafe/key',{
      method:'POST',
      headers:{'Content-Type':'application/json'},
      body:JSON.stringify({api_key:value})
    });
    $('apiKey').value='';
    $('error').textContent='';
    $('modelStatus').innerHTML='<span class="ok">'+data.message+'</span>';
    await refreshStatus();
  }catch(e){
    $('modelStatus').innerHTML='<span class="bad">'+esc(e.message)+'</span>';
    $('error').textContent=e.message;
  }
}

async function observe(){
  try{
    await saveConfig();
    await api('/api/observe',{method:'POST'});
    $('error').textContent='';
    refreshStatus();refreshFrame();
  }catch(e){$('error').textContent=e.message}
}

async function oneStep(){
  try{
    await saveConfig();
    await api('/api/step',{method:'POST'});
    $('error').textContent='';
    refreshStatus();refreshFrame();
  }catch(e){$('error').textContent=e.message;refreshStatus()}
}

async function start(){
  try{
    $('mainStatus').textContent='Agent wird gestartet…';
    $('error').textContent='';
    await saveConfig();
    const data=await api('/api/run/start',{method:'POST'});
    statusData=data;
    await refreshStatus();
  }catch(e){
    $('error').textContent=e.message;
    $('mainStatus').textContent='Start abgebrochen.';
  }
}

async function stop(){
  try{await api('/api/run/stop',{method:'POST'});refreshStatus()}catch(e){$('error').textContent=e.message}
}

async function resetEpisode(){
  try{await api('/api/episode/reset',{method:'POST'});refreshStatus()}catch(e){$('error').textContent=e.message}
}

async function resetLearning(){
  if(!confirm('Alle gelernten Spider-Werte für Laya und TypeSafe löschen?')) return;
  try{await api('/api/learning/reset',{method:'POST'});refreshStatus()}catch(e){$('error').textContent=e.message}
}

async function retryLaya(){
  try{await api('/api/laya/retry',{method:'POST'});refreshStatus()}catch(e){$('error').textContent=e.message}
}

async function refreshStatus(){
  try{
    const d=await api('/api/status');
    statusData=d;
    $('runBadge').textContent=d.runtime.running?'läuft':'gestoppt';
    $('runBadge').className='badge '+(d.runtime.running?'ok':'');
    $('modelBadge').textContent=d.config.model==='laya'?'Laya':'TypeSafe/Jev';
    $('readerBadge').textContent='Kartenleser: '+(d.state?.reader||(
      d.config.vision_enabled && d.config.vision_model ? 'Ollama '+d.config.vision_model : 'FastOCR'
    ));
    const im=d.input_status?.mode||'unbekannt';
    if(im==='host_real_mouse'){
      $('inputModeBadge').textContent='ECHTE HOST-MAUS · Alt+L Start/Stop';
      $('inputModeBadge').className='badge ok';
    }else if(im==='vm_guest_real_drag'){
      $('inputModeBadge').textContent='VM-GAST: ECHTER DRAG';
      $('inputModeBadge').className='badge ok';
    }else{
      $('inputModeBadge').textContent='Eingabemodus: '+im;
      $('inputModeBadge').className='badge warn';
    }

    const ls=d.laya;
    const ts=d.typesafe;
    let tsLabel='<span class="warn">kein Key</span>';
    if(ts.validated){
      tsLabel='<span class="ok">API geprüft & bereit</span>';
    }else if(ts.configured && ts.validation_error){
      tsLabel='<span class="bad">API-Fehler</span>';
    }else if(ts.configured){
      tsLabel='<span class="warn">Key gespeichert, noch nicht geprüft</span>';
    }
    $('modelStatus').innerHTML=
      'Laya: '+(ls.status==='ready'?'<span class="ok">bereit</span>':ls.status==='error'?'<span class="bad">Fehler</span>':ls.status==='loading'?'<span class="warn">lädt im Hintergrund</span>':'<span class="muted">nicht geladen</span>')+
      ' · TypeSafe: '+tsLabel;

    if(d.runtime.last_loop_error){
      $('error').textContent=d.runtime.last_loop_error;
      $('mainStatus').textContent='Agent gestoppt.';
    }else if(d.game_won){
      $('mainStatus').textContent='Gewonnen.';
    }else if(d.runtime.running){
      const phase=d.phase_detail||d.phase||'arbeitet';
      const elapsed=Number(d.phase_elapsed_seconds||0).toFixed(1);
      $('mainStatus').textContent=(d.runtime.stopping?'Stop angefordert · ':'Agent spielt · ')+
        phase+' · '+elapsed+' s · Aktion '+(d.moves+1)+' · Alt+L = Stop';
    }else{
      $('mainStatus').textContent=d.window?'Fenster verbunden · '+d.moves+' Aktionen ausgeführt':'Noch kein Spielfenster ausgewählt.';
    }

    if(d.state){
      const concise={
        reader:d.state.reader,
        input_status:d.input_status,
        stock_available:d.state.stock_available,
        stock_point:d.state.stock_point,
        legal_actions_detected:d.legal_actions_detected,
        columns:d.state.columns.map(c=>({
          column:c.index+1,
          hidden:c.hidden_above,
          cards:c.cards.map(x=>x.rank+x.suit)
        })),
        diagnostics:d.state.diagnostics
      };
      $('stateDump').textContent=JSON.stringify(concise,null,2);
    }
    $('actionDump').textContent=d.last_action?JSON.stringify(d.last_action,null,2):'Noch keine Aktion.';
    $('learningDump').textContent=JSON.stringify(d.learning,null,2);

    if(d.config.vision_enabled!==undefined) $('visionEnabled').checked=!!d.config.vision_enabled;
    if(d.config.vision_model && [...$('visionModel').options].some(o=>o.value===d.config.vision_model)){
      $('visionModel').value=d.config.vision_model;
    }
    if(d.last_vision){
      const timing=d.last_vision.timing||{};
      if(d.last_vision.skipped){
        $('ollamaStatus').textContent='Ollama übersprungen: '+(d.last_vision.reason||'UIA/FastOCR ausreichend');
        $('ollamaStatus').className='small ok';
      }else if(d.last_vision.error){
        $('ollamaStatus').textContent='Ollama-Hilfe fehlgeschlagen, normale Erkennung läuft weiter: '+d.last_vision.error;
        $('ollamaStatus').className='small warn';
      }else{
        $('ollamaStatus').textContent='Ollama-Hilfe: '+d.last_vision.model+
          ' · '+Number(timing.total_ms||0).toFixed(0)+' ms'+
          ' · Spalten: '+JSON.stringify(d.last_vision.applied_columns||[]);
        $('ollamaStatus').className='small ok';
      }
    }

    const sp=d.state?.stock_point;
    $('stockStatus').textContent=sp
      ? 'Stock automatisch erkannt: X '+Number(sp[0]).toFixed(3)+' · Y '+Number(sp[1]).toFixed(3)
      : 'Stock aktuell nicht sicher erkannt; der Agent probiert bei Bedarf den letzten gültigen Punkt.';
  }catch(e){
    $('mainStatus').textContent='Serverfehler';
    $('error').textContent=e.message;
  }
}

function refreshFrame(){
  if(!statusData?.window) return;
  $('frame').src='/api/frame.jpg?t='+Date.now();
}

$('refreshWindows').addEventListener('click',refreshWindows);
$('selectWindow').addEventListener('click',selectWindow);
$('refreshOllama').addEventListener('click',refreshOllama);
$('visionEnabled').addEventListener('change',saveConfig);
$('visionModel').addEventListener('change',saveConfig);
$('saveKey').addEventListener('click',saveKey);
$('observe').addEventListener('click',observe);
$('step').addEventListener('click',oneStep);
$('start').addEventListener('click',start);
$('stop').addEventListener('click',stop);
$('resetEpisode').addEventListener('click',resetEpisode);
$('resetLearning').addEventListener('click',resetLearning);
$('retryLaya').addEventListener('click',retryLaya);
$('model').addEventListener('change',saveConfig);
$('depth').addEventListener('change',saveConfig);
$('delay').addEventListener('change',saveConfig);
$('learning').addEventListener('change',saveConfig);

refreshWindows();
refreshOllama();
refreshStatus();
frameTimer=setInterval(()=>{refreshStatus();refreshFrame()},1200);
</script>
</body>
</html>"""
