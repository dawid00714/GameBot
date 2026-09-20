import fs from 'node:fs';
import path from 'node:path';
import { config } from './config.mjs';

const ROOT = path.resolve(config.dreamRsi.dir);
const HISTORY_FILE = path.join(ROOT, 'history.jsonl');
const POLICY_FILE = path.join(ROOT, 'policy.json');
const POLICY_HISTORY_FILE = path.join(ROOT, 'policy-history.jsonl');

fs.mkdirSync(ROOT, {recursive: true});

const ACTION_FAMILIES = [
  'build','install','mine','collect','explore','follow','craft','loot',
  'waypoint','place','wait','other'
];

const DEFAULT_POLICY = {
  schemaVersion: 1,
  generation: 0,
  name: 'baseline',
  candidateLimit: 5,
  plannerEvery: config.plannerEvery,
  replanRepeatThreshold: 5,
  waitPenalty: 1.4,
  repeatPenalty: 0.75,
  userTaskBonus: 2.5,
  noveltyBonus: 0.45,
  historyRewardWeight: 0.35,
  actionBias: {
    build: 1.1,
    install: 1.25,
    mine: 0.35,
    collect: 0.6,
    explore: 0.15,
    follow: 0.25,
    craft: 0.65,
    loot: 0.6,
    waypoint: 0.35,
    place: 0.55,
    wait: -0.8,
    other: 0
  }
};

const clamp = (value, min, max, fallback) => {
  const n = Number(value);
  return Number.isFinite(n) ? Math.max(min, Math.min(max, n)) : fallback;
};

function normalizePolicy(raw, fallback=DEFAULT_POLICY) {
  const bias = {};
  for (const family of ACTION_FAMILIES) {
    bias[family] = clamp(
      raw?.actionBias?.[family],
      -2.5,
      2.5,
      fallback.actionBias?.[family] ?? 0
    );
  }

  return {
    schemaVersion: 1,
    generation: Math.max(0, Math.floor(clamp(raw?.generation, 0, 100000, fallback.generation))),
    name: String(raw?.name || fallback.name || 'policy').slice(0, 80),
    candidateLimit: Math.floor(clamp(raw?.candidateLimit, 1, 10, fallback.candidateLimit)),
    plannerEvery: Math.floor(clamp(raw?.plannerEvery, 4, 60, fallback.plannerEvery)),
    replanRepeatThreshold: Math.floor(clamp(raw?.replanRepeatThreshold, 2, 10, fallback.replanRepeatThreshold)),
    waitPenalty: clamp(raw?.waitPenalty, 0, 3, fallback.waitPenalty),
    repeatPenalty: clamp(raw?.repeatPenalty, 0, 3, fallback.repeatPenalty),
    userTaskBonus: clamp(raw?.userTaskBonus, 0, 4, fallback.userTaskBonus),
    noveltyBonus: clamp(raw?.noveltyBonus, 0, 2, fallback.noveltyBonus),
    historyRewardWeight: clamp(raw?.historyRewardWeight, 0, 1.5, fallback.historyRewardWeight),
    actionBias: bias
  };
}

function loadPolicy() {
  try {
    if (fs.existsSync(POLICY_FILE)) {
      return normalizePolicy(JSON.parse(fs.readFileSync(POLICY_FILE, 'utf8')));
    }
  } catch (error) {
    console.warn('[DREAM-RSI] Policy konnte nicht geladen werden:', error.message);
  }
  return structuredClone(DEFAULT_POLICY);
}

let activePolicy = loadPolicy();
let sessionId = new Date().toISOString().replace(/[:.]/g, '-');
let lastNodeId = null;
let historyCache = null;
let improvementRunning = false;
let lastDreamStep = -1;

function appendJsonl(file, row) {
  fs.appendFileSync(file, JSON.stringify(row) + '\n');
}

function readHistory() {
  if (historyCache) return historyCache;
  const rows = [];
  try {
    if (fs.existsSync(HISTORY_FILE)) {
      const lines = fs.readFileSync(HISTORY_FILE, 'utf8').split(/\r?\n/).filter(Boolean);
      for (const line of lines.slice(-config.dreamRsi.maxHistory)) {
        try {
          const row = JSON.parse(line);
          if (row?.type === 'transition') rows.push(row);
        } catch {}
      }
    }
  } catch (error) {
    console.warn('[DREAM-RSI] History konnte nicht gelesen werden:', error.message);
  }
  historyCache = rows;
  return rows;
}

export function actionFamily(key='') {
  const k = String(key);
  if (k === 'build_house_step') return 'build';
  if (k === 'install_house_door') return 'install';
  if (k.startsWith('mine_')) return 'mine';
  if (k.startsWith('collect_')) return 'collect';
  if (k.startsWith('explore_')) return 'explore';
  if (k.startsWith('follow_player_')) return 'follow';
  if (k.startsWith('craft_')) return 'craft';
  if (k.startsWith('loot_')) return 'loot';
  if (k === 'waypoint') return 'waypoint';
  if (k === 'place_crafting_table') return 'place';
  if (k === 'wait') return 'wait';
  return 'other';
}

function directiveExpectedFamily(type) {
  if (type === 'build_house') return 'build';
  if (type === 'install_door') return 'install';
  if (type === 'follow_player' || type === 'come_here') return 'follow';
  return null;
}

function recentActionKeys(context) {
  if (Array.isArray(context?.recentActionKeys)) return context.recentActionKeys;
  if (Array.isArray(context?.recent)) return context.recent.map(row => row.action).filter(Boolean);
  if (Array.isArray(context?.state?.recent)) return context.state.recent.map(row => row.action).filter(Boolean);
  return [];
}

function historyStats() {
  const stats = new Map();
  for (const row of readHistory()) {
    const family = actionFamily(row.selectedAction);
    const prev = stats.get(family) || {sum:0, count:0};
    prev.sum += Number(row.reward || 0);
    prev.count += 1;
    stats.set(family, prev);
  }
  return stats;
}

function priorityFor(candidate, context, policy=activePolicy, stats=null) {
  const family = actionFamily(candidate.key);
  let score = Number(policy.actionBias?.[family] || 0);

  if (family === 'wait') score -= policy.waitPenalty;

  const recent = recentActionKeys(context);
  const sameCount = recent.slice(-5).filter(key => key === candidate.key).length;
  score -= sameCount * policy.repeatPenalty;

  const directiveType = context?.directiveType
    ?? context?.userDirective?.type
    ?? context?.state?.userDirective?.type
    ?? null;

  const expected = directiveExpectedFamily(directiveType);
  if (expected) {
    if (family === expected) score += policy.userTaskBonus;
    else if (family !== 'wait') score -= policy.userTaskBonus * 0.75;
  }

  if (family === 'explore') {
    const wasRecent = recent.slice(-4).some(key => key === candidate.key);
    if (!wasRecent) score += policy.noveltyBonus;
  }

  const familyStats = stats || historyStats();
  const historical = familyStats.get(family);
  if (historical?.count) {
    const avg = historical.sum / historical.count;
    score += Math.max(-2, Math.min(2, avg)) * policy.historyRewardWeight;
  }

  return score;
}

export function applyDreamPolicy(candidates, context={}) {
  if (!config.dreamRsi.enabled || !Array.isArray(candidates) || candidates.length <= 1) {
    return candidates;
  }

  // Explicit user tasks should not be pruned by the self-improvement layer.
  // We still record their outcomes so later dreaming can learn from them.
  if (context?.userDirective?.type || context?.state?.userDirective?.type) {
    return candidates.map(candidate => ({
      ...candidate,
      dreamPriority: priorityFor(candidate, context),
      description: candidate.description + ' [Dream-RSI: explicit user task; no pruning]'
    }));
  }

  const stats = historyStats();
  const ranked = candidates
    .map((candidate, index) => {
      const dreamPriority = priorityFor(candidate, context, activePolicy, stats);
      return {
        ...candidate,
        dreamPriority,
        __dreamIndex: index,
        description: candidate.description + ` [Dream-RSI priority ${dreamPriority.toFixed(2)}]`
      };
    })
    .sort((a,b) => (b.dreamPriority - a.dreamPriority) || (a.__dreamIndex - b.__dreamIndex));

  const limit = Math.max(1, Math.min(activePolicy.candidateLimit, ranked.length));
  return ranked.slice(0, limit).map(({__dreamIndex, ...candidate}) => candidate);
}

function compactInventory(inventory={}) {
  return Object.entries(inventory)
    .filter(([, count]) => Number(count) > 0)
    .sort(([a],[b]) => a.localeCompare(b))
    .slice(0, 24)
    .map(([name, count]) => [name, Math.min(64, Number(count))]);
}

function objectiveClass(plan) {
  const text = String(plan?.objective || '').toLowerCase();
  if (/build|house|haus/.test(text)) return 'build';
  if (/door|tuer|tür|install/.test(text)) return 'install';
  if (/follow|approach.*player/.test(text)) return 'follow';
  if (/craft/.test(text)) return 'craft';
  if (/mine|gather|collect/.test(text)) return 'resource';
  if (/explore|search|scout|observe|find/.test(text)) return 'explore';
  return text.slice(0, 48) || 'none';
}

function signatureFor(state, plan, candidateKeys=[]) {
  const nearbyBlocks = [...new Set((state?.nearbyBlocks || []).map(b => b.name))].sort().slice(0, 16);
  const candidateFamilies = [...new Set(candidateKeys.map(actionFamily))].sort();
  const players = (state?.nearbyPlayers || []).length > 0 ? 'visible' : 'none';

  return JSON.stringify({
    gameMode: state?.gameMode ?? null,
    directive: state?.userDirective?.type ?? null,
    objective: objectiveClass(plan),
    health: Math.round(Number(state?.health || 0) / 4) * 4,
    food: Math.round(Number(state?.food || 0) / 4) * 4,
    inventory: compactInventory(state?.inventory),
    nearbyBlocks,
    players,
    candidateFamilies
  });
}

function positionDistance(a, b) {
  if (!a || !b) return 0;
  return Math.hypot(
    Number(b.x || 0) - Number(a.x || 0),
    Number(b.y || 0) - Number(a.y || 0),
    Number(b.z || 0) - Number(a.z || 0)
  );
}

function inventoryTotal(inventory={}) {
  return Object.values(inventory).reduce((sum, value) => sum + Number(value || 0), 0);
}

function rewardTransition({beforeState, afterState, selectedAction, result, recentBefore=[]}) {
  const family = actionFamily(selectedAction);
  const text = String(result || '');
  const failed = /^FAILED:/i.test(text);
  let reward = failed ? -3.5 : 0.8;

  if (family === 'wait') reward -= 1.7;
  if (family === 'build' && !failed) reward += 2.0;
  if (family === 'install' && !failed) reward += 2.8;
  if (family === 'craft' && !failed) reward += 1.2;
  if (family === 'collect' && !failed) reward += 0.7;
  if (family === 'mine' && !failed) reward += 0.45;
  if (family === 'loot' && !failed) reward += 0.9;

  const moved = positionDistance(beforeState?.position, afterState?.position);
  if (moved > 0.5) reward += Math.min(1.0, moved / 6);

  const inventoryDelta = inventoryTotal(afterState?.inventory) - inventoryTotal(beforeState?.inventory);
  if (inventoryDelta > 0) reward += Math.min(1.5, inventoryDelta * 0.15);

  const healthDelta = Number(afterState?.health || 0) - Number(beforeState?.health || 0);
  if (healthDelta < 0) reward += healthDelta * 0.8;

  const expected = directiveExpectedFamily(beforeState?.userDirective?.type);
  if (expected && family === expected && !failed) reward += 1.5;

  const repeated = recentBefore.slice(-4).filter(row => row.action === selectedAction).length;
  if (repeated >= 2 && !['build','install','follow'].includes(family)) {
    reward -= repeated * 0.3;
  }

  return Number(reward.toFixed(4));
}

export function recordDreamTransition({
  step,
  beforeState,
  afterState,
  plan,
  candidates,
  selectedAction,
  result,
  recentBefore=[]
}) {
  if (!config.dreamRsi.enabled) return null;

  const candidateKeys = (candidates || []).map(candidate => candidate.key);
  const nodeId = sessionId + ':' + step;
  const reward = rewardTransition({
    beforeState,
    afterState,
    selectedAction,
    result,
    recentBefore
  });

  const row = {
    type: 'transition',
    schemaVersion: 1,
    sessionId,
    nodeId,
    parentId: lastNodeId,
    step,
    time: new Date().toISOString(),
    signature: signatureFor(beforeState, plan, candidateKeys),
    nextSignature: signatureFor(afterState, plan, []),
    planObjective: plan?.objective || null,
    directiveType: beforeState?.userDirective?.type || null,
    candidateKeys,
    selectedAction,
    result: String(result || '').slice(0, 500),
    reward,
    before: {
      position: beforeState?.position || null,
      health: beforeState?.health ?? null,
      food: beforeState?.food ?? null,
      inventory: beforeState?.inventory || {}
    },
    after: {
      position: afterState?.position || null,
      health: afterState?.health ?? null,
      food: afterState?.food ?? null,
      inventory: afterState?.inventory || {}
    },
    recentActionKeys: recentBefore.slice(-6).map(row => row.action).filter(Boolean),
    policyGeneration: activePolicy.generation,
    policyName: activePolicy.name
  };

  appendJsonl(HISTORY_FILE, row);
  if (!historyCache) historyCache = [];
  historyCache.push(row);
  if (historyCache.length > config.dreamRsi.maxHistory) {
    historyCache = historyCache.slice(-config.dreamRsi.maxHistory);
  }
  lastNodeId = nodeId;

  console.log(
    `[DREAM-RSI] Record node=${nodeId} action=${selectedAction} reward=${reward.toFixed(2)} history=${historyCache.length}`
  );

  return row;
}

function buildOutcomeIndex(records) {
  const index = new Map();
  for (const row of records) {
    const key = row.signature + '\n' + row.selectedAction;
    const prev = index.get(key) || {sum:0, count:0};
    prev.sum += Number(row.reward || 0);
    prev.count += 1;
    index.set(key, prev);
  }
  return index;
}

function evaluatePolicy(policy, records) {
  const normalized = normalizePolicy(policy, activePolicy);
  const outcomeIndex = buildOutcomeIndex(records);
  const stats = new Map();

  for (const row of records) {
    const family = actionFamily(row.selectedAction);
    const prev = stats.get(family) || {sum:0, count:0};
    prev.sum += Number(row.reward || 0);
    prev.count += 1;
    stats.set(family, prev);
  }

  let sum = 0;
  let evaluated = 0;
  let chosenWait = 0;

  for (const row of records) {
    const candidates = (row.candidateKeys || []).map(key => ({key, description:''}));
    if (!candidates.length) continue;

    const context = {
      directiveType: row.directiveType,
      recentActionKeys: row.recentActionKeys || []
    };

    candidates.sort((a,b) =>
      priorityFor(b, context, normalized, stats) - priorityFor(a, context, normalized, stats)
    );

    const selected = candidates[0]?.key;
    if (!selected) continue;
    if (actionFamily(selected) === 'wait') chosenWait += 1;

    const outcome = outcomeIndex.get(row.signature + '\n' + selected);
    if (!outcome?.count) continue;

    sum += outcome.sum / outcome.count;
    evaluated += 1;
  }

  const coverage = records.length ? evaluated / records.length : 0;
  const averageReward = evaluated ? sum / evaluated : -999;
  const coveragePenalty = (1 - coverage) * 0.6;
  const waitRate = records.length ? chosenWait / records.length : 0;
  const score = averageReward - coveragePenalty - waitRate * 0.25;

  return {
    policy: normalized,
    score: Number(score.toFixed(5)),
    averageReward: Number(averageReward.toFixed(5)),
    coverage: Number(coverage.toFixed(5)),
    evaluated,
    waitRate: Number(waitRate.toFixed(5))
  };
}

async function proposePolicies(records) {
  const recent = records.slice(-Math.min(records.length, 120));
  const summary = {
    transitions: records.length,
    recentAverageReward: recent.length
      ? Number((recent.reduce((sum,row) => sum + Number(row.reward || 0), 0) / recent.length).toFixed(3))
      : 0,
    recentFailures: recent.filter(row => /^FAILED:/i.test(row.result || '')).length,
    recentWaits: recent.filter(row => actionFamily(row.selectedAction) === 'wait').length,
    familyStats: Object.fromEntries(
      ACTION_FAMILIES.map(family => {
        const rows = recent.filter(row => actionFamily(row.selectedAction) === family);
        const avg = rows.length ? rows.reduce((sum,row) => sum + Number(row.reward || 0), 0) / rows.length : null;
        return [family, {count:rows.length, avgReward:avg == null ? null : Number(avg.toFixed(3))}];
      })
    )
  };

  const system = `You improve ONLY the exploration/orchestration policy of a Minecraft agent.
This is inspired by Dream-RSI: history is replayed offline and the underlying Ollama/JEV/Mineflayer agents remain unchanged.
Propose ${config.dreamRsi.proposals} conservative policy variants. Do not write JavaScript or change tools.

Return ONLY JSON:
{
  "policies": [
    {
      "name": "short name",
      "candidateLimit": 1..10,
      "plannerEvery": 4..60,
      "replanRepeatThreshold": 2..10,
      "waitPenalty": 0..3,
      "repeatPenalty": 0..3,
      "userTaskBonus": 0..4,
      "noveltyBonus": 0..2,
      "historyRewardWeight": 0..1.5,
      "actionBias": {
        "build": -2.5..2.5,
        "install": -2.5..2.5,
        "mine": -2.5..2.5,
        "collect": -2.5..2.5,
        "explore": -2.5..2.5,
        "follow": -2.5..2.5,
        "craft": -2.5..2.5,
        "loot": -2.5..2.5,
        "waypoint": -2.5..2.5,
        "place": -2.5..2.5,
        "wait": -2.5..2.5,
        "other": -2.5..2.5
      }
    }
  ]
}
Prefer small changes. Reduce repeated waiting/failing behavior without destroying useful exploration.
Explicit user tasks should stay high priority.
The incumbent is always evaluated too, so you do not need to copy it exactly.`;

  const response = await fetch(config.ollama.url + '/api/chat', {
    method: 'POST',
    headers: {'Content-Type':'application/json'},
    body: JSON.stringify({
      model: config.ollama.model,
      stream: false,
      format: 'json',
      think: false,
      keep_alive: '5m',
      messages: [
        {role:'system', content:system},
        {role:'user', content: JSON.stringify({incumbent:activePolicy, historySummary:summary})}
      ],
      options: {temperature:0.35}
    }),
    signal: AbortSignal.timeout(config.ollama.timeoutMs)
  });

  if (!response.ok) {
    throw new Error(`Ollama Dream-RSI HTTP ${response.status}: ${await response.text()}`);
  }

  const data = await response.json();
  const parsed = JSON.parse(String(data?.message?.content || '{}').replace(/^\s*```(?:json)?/i,'').replace(/```\s*$/,'').trim());
  const raw = Array.isArray(parsed?.policies) ? parsed.policies : [];

  return raw
    .slice(0, config.dreamRsi.proposals)
    .map((policy, index) => normalizePolicy({
      ...policy,
      generation: activePolicy.generation + 1,
      name: policy?.name || ('dream-' + (activePolicy.generation + 1) + '-' + index)
    }, activePolicy));
}

function savePolicy(policy, evaluation, incumbentEvaluation) {
  const persisted = {
    ...policy,
    updatedAt: new Date().toISOString(),
    replay: {
      score: evaluation.score,
      averageReward: evaluation.averageReward,
      coverage: evaluation.coverage,
      evaluated: evaluation.evaluated,
      incumbentScore: incumbentEvaluation.score
    }
  };
  fs.writeFileSync(POLICY_FILE, JSON.stringify(persisted, null, 2));
  appendJsonl(POLICY_HISTORY_FILE, {
    time: persisted.updatedAt,
    policy: persisted
  });
}

export async function maybeImproveDreamPolicy(step) {
  if (!config.dreamRsi.enabled || improvementRunning) return null;
  if (step <= 0 || step === lastDreamStep) return null;
  if (step % config.dreamRsi.dreamEvery !== 0) return null;

  const records = readHistory();
  if (records.length < config.dreamRsi.minHistory) {
    console.log(
      `[DREAM-RSI] Noch zu wenig History fuer Dreaming: ${records.length}/${config.dreamRsi.minHistory}`
    );
    lastDreamStep = step;
    return null;
  }

  improvementRunning = true;
  lastDreamStep = step;

  try {
    console.log(`\n[DREAM-RSI] Dreaming ueber ${records.length} gespeicherte Transitionen...`);

    const incumbentEval = evaluatePolicy(activePolicy, records);
    const proposals = await proposePolicies(records);
    const evaluations = [
      {...incumbentEval, incumbent:true},
      ...proposals.map(policy => ({...evaluatePolicy(policy, records), incumbent:false}))
    ].sort((a,b) => b.score - a.score);

    console.log('[DREAM-RSI] Replay-Rangliste:');
    for (const e of evaluations) {
      console.log(
        `  ${e.incumbent ? '[incumbent]' : '[candidate]'} ${e.policy.name} score=${e.score.toFixed(3)} avg=${e.averageReward.toFixed(3)} coverage=${(e.coverage*100).toFixed(1)}%`
      );
    }

    const best = evaluations[0];
    const minCoverage = Math.max(0.15, incumbentEval.coverage * 0.7);
    const improvesReplay = !best.incumbent
      && best.score >= incumbentEval.score + config.dreamRsi.minImprovement
      && best.coverage >= minCoverage;

    if (!improvesReplay) {
      console.log('[DREAM-RSI] Keine konservativ bessere Replay-Policy gefunden. Incumbent bleibt aktiv.');
      return {updated:false, incumbent:incumbentEval, evaluations};
    }

    activePolicy = normalizePolicy({
      ...best.policy,
      generation: activePolicy.generation + 1
    }, activePolicy);
    savePolicy(activePolicy, best, incumbentEval);

    console.log(
      `[DREAM-RSI] POLICY UPDATE -> generation=${activePolicy.generation} name=${activePolicy.name} replayScore=${best.score.toFixed(3)}`
    );

    return {updated:true, policy:activePolicy, incumbent:incumbentEval, selected:best, evaluations};
  } catch (error) {
    console.error('[DREAM-RSI] Dreaming fehlgeschlagen:', error.message);
    return {updated:false, error:error.message};
  } finally {
    improvementRunning = false;
  }
}

export function getDreamRuntimeConfig() {
  return {
    plannerEvery: config.dreamRsi.enabled ? activePolicy.plannerEvery : config.plannerEvery,
    replanRepeatThreshold: config.dreamRsi.enabled ? activePolicy.replanRepeatThreshold : 5,
    candidateLimit: config.dreamRsi.enabled ? activePolicy.candidateLimit : 999
  };
}

export function getDreamPolicy() {
  return structuredClone(activePolicy);
}

export function describeDreamPolicy() {
  const historyCount = readHistory().length;
  return {
    enabled: config.dreamRsi.enabled,
    historyCount,
    directory: ROOT,
    policy: getDreamPolicy()
  };
}
