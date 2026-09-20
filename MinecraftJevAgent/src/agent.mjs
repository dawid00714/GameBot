import fs from 'node:fs';
import path from 'node:path';
import { mineflayer, pf } from './minecraft-runtime.mjs';
import { config } from './config.mjs';
import { makePlanCandidates } from './ollama-planner.mjs';
import { choosePlan, chooseAction } from './jev-controller.mjs';
import { observe } from './state.mjs';
import { buildCandidates } from './actions.mjs';
import { createHouseDirective, getHouseStatus, chooseHouseMaterial } from './house.mjs';

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
let userDirective = null;
let pausedByUser = false;
let replanRequested = false;
let lastSpokenPlan = '';
let lastStatusAt = 0;

function sayStatus(text, force=false) {
  const now = Date.now();
  if (!force && now - lastStatusAt < 1500) return;
  lastStatusAt = now;
  try {
    bot.chat('[JevOllama] ' + String(text).slice(0, 220));
  } catch {}
}

function directiveSummary() {
  if (!userDirective) return 'Autonomer Modus.';
  if (userDirective.type === 'follow_player') return 'Ich folge ' + userDirective.username + '.';
  if (userDirective.type === 'build_house') return 'Ich baue ein 5x5-Haus fuer ' + userDirective.requestedBy + '.';
  return 'Aktive Aufgabe: ' + userDirective.type;
}

function normalizeChatCommand(message) {
  return String(message || '')
    .trim()
    .replace(/^!?(?:bot|jev|jevollama)[,:]?\s*/i, '')
    .trim();
}

function matches(text, expressions) {
  return expressions.some(re => re.test(text));
}

function onUserChat(username, message) {
  if (!username || username === bot.username) return;
  const command = normalizeChatCommand(message);
  const lower = command.toLowerCase();

  if (matches(lower, [/^(hilfe|help|befehle)$/])) {
    sayStatus('Befehle: "folge mir", "baue hier ein haus", "stopp", "weiter", "autonom", "status".', true);
    return;
  }

  if (matches(lower, [/^(status|was machst du\??|was tust du\??)$/])) {
    const objective = plan?.objective ? ' Plan: ' + plan.objective : '';
    sayStatus(directiveSummary() + objective, true);
    return;
  }

  if (matches(lower, [/^(stopp|stop|pause|warte)$/])) {
    pausedByUser = true;
    try { bot.pathfinder.setGoal(null); } catch {}
    try { bot.clearControlStates(); } catch {}
    sayStatus('Okay, ich stoppe und warte.', true);
    return;
  }

  if (matches(lower, [/^(weiter|resume|mach weiter)$/])) {
    pausedByUser = false;
    replanRequested = true;
    sayStatus('Okay, ich mache weiter.', true);
    return;
  }

  if (matches(lower, [/^(autonom|autonomer modus|mach selbst|entscheide selbst)$/])) {
    userDirective = null;
    pausedByUser = false;
    replanRequested = true;
    sayStatus('Autonomer Modus aktiviert.', true);
    return;
  }

  if (matches(lower, [/^(folge mir|follow me|komm her|come here)$/])) {
    userDirective = {
      type: 'follow_player',
      username,
      requestedBy: username,
      createdAt: Date.now()
    };
    pausedByUser = false;
    replanRequested = true;
    sayStatus('Okay, ich folge dir.', true);
    return;
  }

  if (matches(lower, [/(baue|bau).*\bhaus\b/, /build.*\bhouse\b/])) {
    const player = bot.players?.[username]?.entity;
    if (!player?.position) {
      sayStatus('Ich sehe dich gerade nicht. Komm bitte in meine Sichtweite und versuche es erneut.', true);
      return;
    }

    userDirective = createHouseDirective(username, player.position);
    pausedByUser = false;
    replanRequested = true;
    sayStatus('Okay. Ich baue hier ein kleines 5x5-Haus. Falls Material fehlt, sammle ich zuerst Dirt.', true);
    return;
  }
}

bot.on('chat', onUserChat);

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

function actionAdvancesPlan(actionKey, currentPlan) {
  const key = String(actionKey || '');
  const objective = String(currentPlan?.objective || '').toLowerCase();
  const targets = Object.keys(currentPlan?.targets || {});
  const desired = currentPlan?.desiredBlocks || [];
  const resources = [...new Set([...targets, ...desired])];

  if (key === 'wait') return false;

  if (key === 'build_house_step') {
    return /\b(build|house|haus|building)\b/.test(objective);
  }

  if (key.startsWith('mine_')) {
    return resources.some(name => key.startsWith('mine_' + name + '_'));
  }

  if (key.startsWith('craft_')) {
    return targets.some(name => key === 'craft_' + name);
  }

  if (key === 'place_crafting_table') {
    return targets.includes('crafting_table')
      || objective.includes('crafting table')
      || objective.includes('crafting_table');
  }

  if (key.startsWith('follow_player_')) {
    return /\b(player|follow|approach|meet|nearby player)\b/.test(objective)
      && !/loot\s+(the\s+)?player/.test(objective);
  }

  if (key === 'waypoint') return Boolean(currentPlan?.waypoint);

  if (key.startsWith('loot_')) {
    return /\b(loot|chest|barrel|container)\b/.test(objective)
      && !/loot\s+(the\s+)?player/.test(objective);
  }

  if (key.startsWith('explore_')) {
    return /\b(explore|search|find|scout|observe|reveal|survey|look for)\b/.test(objective);
  }

  if (key.startsWith('collect_')) {
    return /\b(collect|gather|pick up|pickup)\b/.test(objective);
  }

  return false;
}

function assessPlan(planCandidate, actionCandidates) {
  const objective = String(planCandidate?.objective || '').toLowerCase();
  const impossiblePlayerLoot = /loot\s+(the\s+)?(nearby\s+)?player/.test(objective);

  const relevantActions = actionCandidates
    .filter(action => actionAdvancesPlan(action.key, planCandidate))
    .map(action => action.key);

  const feasible = !impossiblePlayerLoot && relevantActions.length > 0;

  return {
    feasible,
    invalidReason: impossiblePlayerLoot
      ? 'Players cannot be looted.'
      : (relevantActions.length ? null : 'No currently executable action advances this plan.'),
    relevantActions
  };
}

function planForConsole(candidate, index) {
  return {
    option: 'p' + index,
    objective: candidate.plan.objective,
    targets: candidate.plan.targets,
    waypoint: candidate.plan.waypoint,
    desiredBlocks: candidate.plan.desiredBlocks,
    feasible: candidate.feasible,
    invalidReason: candidate.invalidReason,
    relevantActions: candidate.relevantActions,
    availableActions: candidate.availableActions
  };
}

bot.on('error', error => {
  console.error('Minecraft connection error:', error);
  log('minecraft_error', {error: error.message, code: error.code, errno: error.errno, syscall: error.syscall});
});
bot.on('kicked', reason => {
  console.error('\n[MINECRAFT] Bot wurde vom Server gekickt.');
  console.error('[MINECRAFT] Vollstaendiger Kick-Grund:');
  console.dir(reason, {depth: null, colors: true});
  try {
    console.error('[MINECRAFT] Kick JSON:\n' + JSON.stringify(reason, null, 2));
  } catch {}
  log('kicked', {reason});
  stopped = true;
});
bot.on('end', reason => {
  console.error('[MINECRAFT] Verbindung beendet:', reason);
  log('end', {reason});
  stopped = true;
});
bot.on('death', () => {
  log('death', {state: observe(bot, {plan, recent, step})});
});

async function refreshPlan() {
  const plannerState = observe(bot, {plan, recent, step, userDirective});

  if (userDirective?.type === 'follow_player') {
    plan = {
      id: 'user-follow',
      objective: 'Follow the visible player ' + userDirective.username,
      targets: {},
      waypoint: null,
      desiredBlocks: [],
      notes: 'Explicit user command. Stay near the requested player.'
    };
    console.log('\n[USER TASK] Follow player:', userDirective.username);
    if (lastSpokenPlan !== plan.objective) {
      lastSpokenPlan = plan.objective;
      sayStatus('Plan: ' + plan.objective, true);
    }
    return;
  }

  if (userDirective?.type === 'build_house') {
    const house = getHouseStatus(bot, userDirective);
    if (house.completed) {
      sayStatus('Haus fertig. Aufgabe abgeschlossen.', true);
      userDirective = null;
      replanRequested = true;
      return refreshPlan();
    }

    if (!userDirective.material) {
      userDirective.material = chooseHouseMaterial(plannerState, house.remaining);
    }

    const material = userDirective.material;
    const have = Number(plannerState.inventory?.[material] || 0);

    if (have < house.remaining) {
      plan = {
        id: 'user-house-gather',
        objective: 'Gather ' + material + ' for the requested house',
        targets: {[material]: house.remaining},
        waypoint: null,
        desiredBlocks: [material],
        notes: 'Explicit user task. Gather enough building material before construction.'
      };
    } else {
      plan = {
        id: 'user-house-build',
        objective: 'Build the requested house here using ' + material,
        targets: {},
        waypoint: null,
        desiredBlocks: [],
        notes: 'Explicit user task. Use build_house_step until the blueprint is complete.'
      };
    }

    console.log('\n[USER TASK] House:', JSON.stringify({
      material,
      inventory: have,
      placed: house.placed,
      remaining: house.remaining,
      total: house.total,
      plan
    }, null, 2));

    const spoken = plan.objective;
    if (lastSpokenPlan !== spoken) {
      lastSpokenPlan = spoken;
      sayStatus('Plan: ' + spoken, true);
    }
    return;
  }

  console.log(`\n[OLLAMA] Erzeuge 3 Plan-Kandidaten mit ${config.ollama.model}...`);
  log('planner_candidates_request', {model: config.ollama.model, state: plannerState});

  const rawCandidates = await makePlanCandidates(plannerState);
  const candidates = rawCandidates.map((rawPlan, index) => {
    const checked = sanitizePlanAgainstState(rawPlan, plannerState);
    const actionCandidates = buildCandidates(bot, plannerState, checked.plan);
    const availableActions = actionCandidates.map(action => action.key);
    const assessment = assessPlan(checked.plan, actionCandidates);
    return {
      index,
      original: rawPlan,
      plan: checked.plan,
      completed: checked.completed,
      availableActions,
      usefulActionCount: availableActions.filter(key => key !== 'wait').length,
      feasible: assessment.feasible,
      invalidReason: assessment.invalidReason,
      relevantActions: assessment.relevantActions,
      relevantActionCount: assessment.relevantActions.length
    };
  });

  log('planner_candidates_response', {candidates});
  console.log('\n[OLLAMA] PLAN-KANDIDATEN:');
  candidates.forEach((candidate, index) => {
    console.log(JSON.stringify(planForConsole(candidate, index), null, 2));
  });

  let feasibleCandidates = candidates.filter(candidate => candidate.feasible);

  if (!feasibleCandidates.length) {
    console.warn('[PLAN-GUARD] Ollama hat keinen sofort ausfuehrbaren Plan geliefert. Erzeuge sicheren Fallback.');

    const neutralPlan = {
      id: 'fallback',
      objective: (plannerState.nearbyPlayers || []).length
        ? 'Approach the visible nearby player.'
        : 'Explore nearby terrain safely to gather new observations.',
      targets: {},
      waypoint: null,
      desiredBlocks: [],
      notes: 'Deterministic fallback because all Ollama plans were infeasible.'
    };

    const fallbackActions = buildCandidates(bot, plannerState, neutralPlan);
    const assessment = assessPlan(neutralPlan, fallbackActions);
    feasibleCandidates = [{
      index: -1,
      original: neutralPlan,
      plan: neutralPlan,
      completed: [],
      availableActions: fallbackActions.map(a => a.key),
      usefulActionCount: fallbackActions.filter(a => a.key !== 'wait').length,
      feasible: assessment.feasible,
      invalidReason: assessment.invalidReason,
      relevantActions: assessment.relevantActions,
      relevantActionCount: assessment.relevantActions.length
    }];
  }

  console.log(`[JEV] Waehle High-Level-Plan aus ${feasibleCandidates.length} AUSFUEHRBAREN Kandidaten...`);

  let planDecision;
  try {
    planDecision = await choosePlan(plannerState, feasibleCandidates);
  } catch (error) {
    // Keep the bot alive if the plan-level JEV request fails.
    console.error('[JEV] Plan-Auswahl fehlgeschlagen:', error.message);
    console.error('[JEV] Fallback auf den ersten ausfuehrbaren Kandidaten.');
    log('plan_choice_error', {error:error.message});
    planDecision = {
      candidate:feasibleCandidates[0],
      choice:'fallback-p0',
      confidence:null,
      probabilities:null,
      raw:null,
      request:null
    };
  }

  plan = planDecision.candidate.plan;

  if (lastSpokenPlan !== plan.objective) {
    lastSpokenPlan = plan.objective;
    sayStatus('Plan: ' + plan.objective, true);
  }

  if (planDecision.candidate.completed.length) {
    console.log('[PLAN-GUARD] Bereits erreichte Targets entfernt:', planDecision.candidate.completed);
  }

  console.log(`\n[JEV] PLAN GEWAEHLT: ${planDecision.choice}`);
  console.log(JSON.stringify(plan, null, 2));

  log('plan_choice', {
    selected: planDecision.choice,
    plan,
    completed: planDecision.candidate.completed,
    availableActions: planDecision.candidate.availableActions,
    confidence: planDecision.confidence,
    probabilities: planDecision.probabilities,
    request: planDecision.request,
    response: planDecision.raw
  });
}

async function main() {
  console.log('[MINECRAFT] Bot ist gespawnt. Warte auf Chunks...');
  await bot.waitForChunksToLoad();
  console.log('[MINECRAFT] Chunks geladen.');

  const movements = new Movements(bot);
  movements.allowParkour = false;
  movements.maxDropDown = 3;

  // Do not let the generic pathfinder place scaffolding/towers automatically.
  // The agent has explicit placement actions, and this also keeps navigation
  // deterministic on the newer 26.2 protocol.
  movements.allow1by1towers = false;
  movements.scafoldingBlocks = [];

  bot.pathfinder.setMovements(movements);

  log('start', {
    minecraft: config.minecraft,
    ollamaModel: config.ollama.model,
    jevModel: config.jev.model,
    architecture: 'ollama-3-plans -> jev-plan-choice -> jev-action-choice'
  });

  await refreshPlan();

  sayStatus('Ich bin bereit. Schreib im Chat "hilfe" fuer Befehle.', true);

  let consecutiveJevErrors = 0;

  while (!stopped) {
    if (fs.existsSync(path.join(runDir, 'stop'))) {
      log('stop_file', {});
      break;
    }

    if (pausedByUser) {
      await bot.waitForTicks(10);
      continue;
    }

    if (replanRequested) {
      replanRequested = false;
      try {
        await refreshPlan();
      } catch (error) {
        console.error('[PLAN] Benutzer-Neuplanung fehlgeschlagen:', error.message);
      }
    }

    if (step > 0 && step % config.plannerEvery === 0) {
      try {
        await refreshPlan();
      } catch (error) {
        log('planner_error', {error: error.message});
        console.error('Planner error:', error.message);
      }
    }

    let state = observe(bot, {plan, recent, step, userDirective});

    if (numericTargetsSatisfied(state, plan)) {
      console.log('[PLAN] Aktuelle numerische Targets erreicht. Neue 3-Wege-Planung...');
      try {
        await refreshPlan();
        state = observe(bot, {plan, recent, step, userDirective});
      } catch (error) {
        console.error('[PLAN] Neuplanung fehlgeschlagen:', error.message);
      }
    }

    if (repeatedSameAction(recent, 5) && userDirective?.type !== 'follow_player') {
      console.log('[PLAN] Gleiche Aktion 5x hintereinander. Neue 3-Wege-Planung...');
      try {
        await refreshPlan();
        state = observe(bot, {plan, recent, step, userDirective});
      } catch (error) {
        console.error('[PLAN] Neuplanung fehlgeschlagen:', error.message);
      }
    }

    const allCandidates = buildCandidates(bot, state, plan);
    const relevantCandidates = allCandidates.filter(candidate => actionAdvancesPlan(candidate.key, plan));

    // If there is a concrete action that advances the chosen plan, do not offer
    // unrelated exploration or "wait" to JEV. This prevents wait-loops.
    const candidates = relevantCandidates.length ? relevantCandidates : allCandidates;

    if (relevantCandidates.length) {
      console.log('[PLAN-GUARD] Aktionsauswahl auf planrelevante Aktionen begrenzt:', relevantCandidates.map(c => c.key));
    } else {
      console.warn('[PLAN-GUARD] Keine planrelevante Aktion vorhanden; voller Fallback-Aktionssatz wird verwendet.');
    }

    let decision;
    try {
      console.log(`[JEV] Waehle Aktion aus ${candidates.length} Aktionen...`);
      decision = await chooseAction(state, candidates);
      consecutiveJevErrors = 0;
    } catch (error) {
      consecutiveJevErrors += 1;
      log('jev_error', {error: error.message, consecutive: consecutiveJevErrors});
      console.error('[JEV] Fehler:', error.message);
      console.error('[JEV] Bot bleibt verbunden. Neuer Versuch in 10 Sekunden.');
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
      result = stopped
        ? 'FAILED: Minecraft connection ended while action was running.'
        : 'FAILED: ' + error.message;
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

    if (userDirective?.type === 'build_house') {
      const house = getHouseStatus(bot, userDirective);
      if (house.completed) {
        sayStatus('Haus fertig: ' + house.total + '/' + house.total + ' Bloecke.', true);
        userDirective = null;
        replanRequested = true;
      } else if (decision.candidate.key === 'build_house_step' && (house.placed % 5 === 0 || house.remaining < 5)) {
        sayStatus('Hausbau: ' + house.placed + '/' + house.total + ' Bloecke.');
      }
    }
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
