import fs from 'node:fs';
import path from 'node:path';
import mineflayer from 'mineflayer';
import pf from 'mineflayer-pathfinder';
import { config } from './config.mjs';
import { makePlan } from './ollama-planner.mjs';
import { chooseAction } from './jev-controller.mjs';
import { observe } from './state.mjs';
import { buildCandidates } from './actions.mjs';

const { pathfinder, Movements } = pf;

const runId = new Date().toISOString().replace(/[:.]/g, '-');
const runDir = path.resolve(config.logDir, runId);
fs.mkdirSync(runDir, {recursive: true});
const eventsFile = path.join(runDir, 'events.jsonl');

function log(type, data={}) {
  fs.appendFileSync(eventsFile, JSON.stringify({
    time: new Date().toISOString(),
    type,
    ...data
  }) + '\n');
}

const bot = mineflayer.createBot({
  host: config.minecraft.host,
  port: config.minecraft.port,
  username: config.minecraft.username,
  version: config.minecraft.version,
  auth: config.minecraft.auth,
  checkTimeoutInterval: 120000
});

bot.loadPlugin(pathfinder);

let stopped = false;
let step = 0;
let plan = null;
let recent = [];

function numericTargetsSatisfied(currentState, currentPlan) {
  const entries = Object.entries(currentPlan?.targets || {})
    .filter(([, raw]) => Number.isFinite(Number(raw)));
  if (!entries.length) return false;
  return entries.every(([name, raw]) => Number(currentState.inventory?.[name] || 0) >= Number(raw));
}

function repeatedSameAction(rows, count=5) {
  if (rows.length < count) return false;
  const tail = rows.slice(-count).map(r => r.action);
  return tail.every(a => a === tail[0]);
}

function sanitizePlanAgainstState(nextPlan, currentState) {
  const completed = [];
  const remainingTargets = {};

  for (const [name, raw] of Object.entries(nextPlan?.targets || {})) {
    const minimum = Number(raw);
    if (Number.isFinite(minimum) && Number(currentState.inventory?.[name] || 0) >= minimum) {
      completed.push({name, have:Number(currentState.inventory?.[name] || 0), target:minimum});
    } else {
      remainingTargets[name] = raw;
    }
  }

  const completedNames = new Set(completed.map(x => x.name));
  const sanitized = {
    ...nextPlan,
    targets: remainingTargets,
    desiredBlocks: (nextPlan?.desiredBlocks || []).filter(name => !completedNames.has(name))
  };

  return {plan:sanitized, completed};
}

bot.on('error', error => {
  console.error('Minecraft connection error:', error);
  log('minecraft_error', {error: error.message, code: error.code, errno: error.errno, syscall: error.syscall});
});
bot.on('kicked', reason => {
  console.error('Minecraft kicked the bot:', reason);
  log('kicked', {reason});
  stopped = true;
});
bot.on('end', reason => {
  log('end', {reason});
  stopped = true;
});
bot.on('death', () => {
  log('death', {state: observe(bot, {plan, recent, step})});
});

async function refreshPlan() {
  const plannerState = observe(bot, {plan, recent, step});
  console.log(`\n[OLLAMA] Frage Planer ${config.ollama.model}...`);
  log('planner_request', {model: config.ollama.model, state: plannerState});
  const next = await makePlan(plannerState);
  const checked = sanitizePlanAgainstState(next, plannerState);
  plan = checked.plan;
  if (checked.completed.length) {
    console.log('[PLAN-GUARD] Bereits erreichte Targets entfernt:', checked.completed);
    log('planner_targets_removed', {completed:checked.completed, original:next, sanitized:plan});
  }
  log('planner_response', {plan});
  console.log('\nPLAN:', JSON.stringify(plan, null, 2));
}

async function main() {
  console.log('[MINECRAFT] Bot ist gespawnt. Warte auf Chunks...');
  await bot.waitForChunksToLoad();
  console.log('[MINECRAFT] Chunks geladen.');

  const movements = new Movements(bot);
  movements.allowParkour = false;
  movements.maxDropDown = 3;
  bot.pathfinder.setMovements(movements);

  log('start', {
    minecraft: config.minecraft,
    ollamaModel: config.ollama.model,
    jevModel: config.jev.model
  });

  await refreshPlan();

  let consecutiveJevErrors = 0;

  while (!stopped) {
    if (fs.existsSync(path.join(runDir, 'stop'))) {
      log('stop_file', {});
      break;
    }

    if (step > 0 && step % config.plannerEvery === 0) {
      try {
        await refreshPlan();
      } catch (error) {
        log('planner_error', {error: error.message});
        console.error('Planner error:', error.message);
      }
    }

    let state = observe(bot, {plan, recent, step});

    if (numericTargetsSatisfied(state, plan)) {
      console.log('[OLLAMA] Planner targets erreicht. Plane sofort neu...');
      await refreshPlan();
      state = observe(bot, {plan, recent, step});
    }

    if (repeatedSameAction(recent, 5)) {
      console.log('[OLLAMA] Gleiche Aktion 5x hintereinander. Erzwinge Neuplanung...');
      try {
        await refreshPlan();
        state = observe(bot, {plan, recent, step});
      } catch (error) {
        console.error('[OLLAMA] Neuplanung fehlgeschlagen:', error.message);
      }
    }

    const candidates = buildCandidates(bot, state, plan);

    let decision;
    try {
      console.log(`[JEV] Frage ${config.jev.model} mit ${candidates.length} Aktionen...`);
      decision = await chooseAction(state, candidates);
      consecutiveJevErrors = 0;
    } catch (error) {
      consecutiveJevErrors += 1;
      log('jev_error', {error: error.message, consecutive: consecutiveJevErrors});
      console.error('[JEV] Fehler:', error.message);
      console.error('[JEV] Bot bleibt verbunden. Neuer Versuch in 10 Sekunden. Bei Konfigurationsaenderungen npm start neu starten.');
      await new Promise(resolve => setTimeout(resolve, 10000));
      continue;
    }

    console.log(
      `\nSTEP ${step} | ${decision.candidate.key}\n${decision.candidate.description}`
    );
    log('decision', {
      step,
      selected: decision.candidate.key,
      description: decision.candidate.description,
      choice: decision.choice,
      confidence: decision.confidence,
      probabilities: decision.probabilities,
      request: decision.request,
      response: decision.raw
    });

    let result;
    try {
      result = await decision.candidate.run();
    } catch (error) {
      result = 'FAILED: ' + error.message;
    }

    step += 1;
    const row = {
      step,
      action: decision.candidate.key,
      result,
      position: bot.entity?.position ? {
        x: Number(bot.entity.position.x.toFixed(2)),
        y: Number(bot.entity.position.y.toFixed(2)),
        z: Number(bot.entity.position.z.toFixed(2))
      } : null
    };
    recent.push(row);
    recent = recent.slice(-12);
    log('result', row);
    console.log('RESULT:', result);
  }

  log('stopped', {step});
  try { bot.quit(); } catch {}
}

bot.once('spawn', () => {
  console.log(`[MINECRAFT] Verbunden mit ${config.minecraft.host}:${config.minecraft.port} als ${config.minecraft.username} (${config.minecraft.version})`);
  main().catch(error => {
    console.error(error);
    log('fatal', {error: error.stack || error.message});
    try { bot.quit(); } catch {}
  });
});

process.on('SIGINT', () => {
  stopped = true;
  try { bot.pathfinder.setGoal(null); } catch {}
  try { bot.clearControlStates(); } catch {}
  try { bot.quit(); } catch {}
  process.exit(0);
});
