import fs from 'node:fs';
import path from 'node:path';
import { config } from './config.mjs';
import { runPlanner } from './ollama-planner.mjs';

const int = (name, fallback) => {
  const n = Number(process.env[name]);
  return Number.isFinite(n) && n > 0 ? Math.floor(n) : fallback;
};
const bool = (name, fallback=false) => {
  const v = process.env[name];
  if (v == null || v === '') return fallback;
  return ['1','true','yes','on'].includes(String(v).toLowerCase());
};

const runs = int('BENCH_RUNS', 1);
const timeoutMs = int('BENCH_TIMEOUT_MS', 45000);
const think = bool('BENCH_THINK', false);
const requested = (process.env.BENCH_MODELS || '')
  .split(',')
  .map(s => s.trim())
  .filter(Boolean);

const nsToMs = n => Number(n || 0) / 1e6;
const median = values => {
  const a = values.filter(Number.isFinite).sort((x,y)=>x-y);
  if (!a.length) return null;
  const i = Math.floor(a.length / 2);
  return a.length % 2 ? a[i] : (a[i-1] + a[i]) / 2;
};
const round = (n, digits=1) => Number.isFinite(n) ? Number(n.toFixed(digits)) : null;
const targetAtLeast = (plan, name, min=1) => Number(plan?.targets?.[name] || 0) >= min;
const mentions = (text, words) => words.some(w => String(text || '').toLowerCase().includes(w));
const exactWaypoint = (plan, p) => !!plan?.waypoint &&
  Number(plan.waypoint.x) === p.x &&
  Number(plan.waypoint.y) === p.y &&
  Number(plan.waypoint.z) === p.z;

const cases = [
  {
    name: 'startup_wood',
    state: {
      goal: 'Get basic wooden tools. Use only observed resources.',
      position: {x: 0, y: 64, z: 0},
      health: 20,
      food: 20,
      inventory: {},
      nearbyBlocks: [
        {name:'oak_log', position:{x:3,y:64,z:1}, distance:3.2},
        {name:'dirt', position:{x:1,y:63,z:0}, distance:1.4}
      ],
      nearbyEntities: [],
      recent: []
    },
    max: 6,
    score(plan) {
      let s = 0;
      if (plan.waypoint === null) s += 1;
      if (targetAtLeast(plan,'oak_log') || plan.desiredBlocks.includes('oak_log')) s += 3;
      if (mentions(plan.objective, ['wood','log','tool'])) s += 2;
      return s;
    }
  },
  {
    name: 'crafting_table',
    state: {
      goal: 'Craft one crafting table using the carried oak logs.',
      position: {x: 0, y: 64, z: 0},
      health: 20,
      food: 20,
      inventory: {oak_log: 4},
      nearbyBlocks: [{name:'dirt', position:{x:1,y:63,z:0}, distance:1.4}],
      nearbyEntities: [],
      recent: []
    },
    max: 6,
    score(plan) {
      let s = 0;
      if (plan.waypoint === null) s += 1;
      if (targetAtLeast(plan,'crafting_table')) s += 3;
      if (mentions(plan.objective + ' ' + plan.notes, ['crafting table','crafting_table','craft'])) s += 2;
      return s;
    }
  },
  {
    name: 'verified_waypoint',
    state: {
      goal: 'Travel to the verified waypoint. Do not invent another destination.',
      position: {x: 0, y: 64, z: 0},
      verifiedWaypoint: {x: 30, y: 64, z: -12},
      health: 20,
      food: 20,
      inventory: {},
      nearbyBlocks: [],
      nearbyEntities: [],
      recent: []
    },
    max: 6,
    score(plan) {
      let s = 0;
      if (exactWaypoint(plan,{x:30,y:64,z:-12})) s += 4;
      if (mentions(plan.objective + ' ' + plan.notes, ['travel','waypoint','30','-12','reach'])) s += 2;
      return s;
    }
  },
  {
    name: 'no_hallucinated_waypoint',
    state: {
      goal: 'Explore safely. No destination coordinates are known.',
      position: {x: 0, y: 64, z: 0},
      health: 20,
      food: 20,
      inventory: {},
      nearbyBlocks: [
        {name:'dirt', position:{x:1,y:63,z:0}, distance:1.4},
        {name:'grass_block', position:{x:2,y:63,z:0}, distance:2.2}
      ],
      nearbyEntities: [],
      recent: []
    },
    max: 6,
    score(plan) {
      let s = 0;
      if (plan.waypoint === null) s += 4;
      if (mentions(plan.objective + ' ' + plan.notes, ['explore','observe','safe','terrain','progress'])) s += 2;
      return s;
    }
  }
];

async function listModels() {
  const r = await fetch(config.ollama.url + '/api/tags', {signal: AbortSignal.timeout(10000)});
  if (!r.ok) throw new Error(`Ollama /api/tags HTTP ${r.status}`);
  const data = await r.json();
  return (data.models || []).map(m => m.name || m.model).filter(Boolean);
}

async function unload(model) {
  try {
    await fetch(config.ollama.url + '/api/generate', {
      method:'POST',
      headers:{'Content-Type':'application/json'},
      body:JSON.stringify({model, prompt:'', stream:false, keep_alive:0}),
      signal:AbortSignal.timeout(10000)
    });
  } catch {}
}

async function benchmarkModel(model) {
  const record = {
    model,
    think,
    timeoutMs,
    warmup: null,
    cases: [],
    errors: []
  };

  console.log(`\n=== ${model} ===`);
  try {
    const warm = await runPlanner({
      goal:'Warm up the planner. Return a tiny safe plan.',
      position:{x:0,y:64,z:0},
      inventory:{},
      nearbyBlocks:[],
      nearbyEntities:[],
      recent:[]
    }, {model, timeoutMs, think, keepAlive:'5m', temperature:0});

    record.warmup = {
      wallMs: round(warm.latencyMs),
      loadMs: round(nsToMs(warm.data.load_duration)),
      evalTokens: warm.data.eval_count ?? null,
      evalMs: round(nsToMs(warm.data.eval_duration)),
      tokensPerSecond: warm.data.eval_count && warm.data.eval_duration
        ? round(warm.data.eval_count / (Number(warm.data.eval_duration) / 1e9), 2)
        : null
    };
    console.log(`Warmup: ${record.warmup.wallMs} ms | load ${record.warmup.loadMs} ms | ${record.warmup.tokensPerSecond ?? '?'} tok/s`);
  } catch (error) {
    record.errors.push({stage:'warmup', error:error.message});
    console.log('Warmup FEHLER:', error.message);
    await unload(model);
    return record;
  }

  for (const test of cases) {
    for (let run = 1; run <= runs; run++) {
      try {
        const r = await runPlanner(test.state, {
          model,
          timeoutMs,
          think,
          keepAlive:'5m',
          temperature:0
        });
        const points = test.score(r.plan);
        const row = {
          case:test.name,
          run,
          points,
          max:test.max,
          qualityPct: round(points / test.max * 100),
          wallMs: round(r.latencyMs),
          loadMs: round(nsToMs(r.data.load_duration)),
          promptTokens: r.data.prompt_eval_count ?? null,
          evalTokens: r.data.eval_count ?? null,
          tokensPerSecond: r.data.eval_count && r.data.eval_duration
            ? round(r.data.eval_count / (Number(r.data.eval_duration) / 1e9), 2)
            : null,
          plan:r.plan
        };
        record.cases.push(row);
        console.log(`${test.name} #${run}: ${row.wallMs} ms | Qualität ${row.points}/${row.max} | ${row.tokensPerSecond ?? '?'} tok/s`);
      } catch (error) {
        record.errors.push({stage:test.name, run, error:error.message});
        console.log(`${test.name} #${run}: FEHLER ${error.message}`);
      }
    }
  }

  await unload(model);
  return record;
}

function summarize(record) {
  const rows = record.cases;
  const earned = rows.reduce((s,r)=>s+r.points,0);
  const possible = rows.reduce((s,r)=>s+r.max,0);
  return {
    model:record.model,
    qualityPct:possible ? earned/possible*100 : 0,
    medianMs:median(rows.map(r=>r.wallMs)),
    tokensPerSecond:median(rows.map(r=>r.tokensPerSecond)),
    coldLoadMs:record.warmup?.loadMs ?? null,
    successfulRuns:rows.length,
    errors:record.errors.length
  };
}

const installed = await listModels();
const models = requested.length ? requested : installed;
if (!models.length) throw new Error('Keine Ollama-Modelle gefunden. Prüfe "ollama list".');

console.log('Ollama:', config.ollama.url);
console.log('Modelle:', models.join(', '));
console.log('Runs pro Test:', runs);
console.log('Timeout pro Anfrage:', timeoutMs + ' ms');
console.log('Thinking:', think ? 'AN' : 'AUS');
console.log('Tests pro Modell:', cases.length);

const records = [];
for (const model of models) records.push(await benchmarkModel(model));

const summaries = records.map(summarize);
const successful = summaries.filter(s => s.successfulRuns > 0 && Number.isFinite(s.medianMs));
const fastestMs = successful.length ? Math.min(...successful.map(s=>s.medianMs)) : null;

for (const s of summaries) {
  const speedPct = fastestMs && s.medianMs ? Math.min(100, fastestMs / s.medianMs * 100) : 0;
  s.speedPct = speedPct;
  s.balanceScore = s.successfulRuns ? (s.qualityPct * 0.65 + speedPct * 0.35) : 0;
}

summaries.sort((a,b)=>b.balanceScore-a.balanceScore);
summaries.forEach((s,i)=>s.rank=i+1);

console.log('\n=== RANGLISTE FÜR DEN MINECRAFT-PLANER ===');
console.table(summaries.map(s=>({
  Rang:s.rank,
  Modell:s.model,
  'Balance':round(s.balanceScore),
  'Qualität %':round(s.qualityPct),
  'Median ms':round(s.medianMs),
  'tok/s':round(s.tokensPerSecond,2),
  'Cold load ms':round(s.coldLoadMs),
  'OK Runs':s.successfulRuns,
  Fehler:s.errors
})));

if (successful.length) {
  const fastest=[...summaries].filter(s=>s.successfulRuns).sort((a,b)=>a.medianMs-b.medianMs)[0];
  const quality=[...summaries].filter(s=>s.successfulRuns).sort((a,b)=>b.qualityPct-a.qualityPct || a.medianMs-b.medianMs)[0];
  const balanced=summaries.find(s=>s.successfulRuns);
  console.log('\nBESTE BALANCE:', balanced?.model, '| Score', round(balanced?.balanceScore));
  console.log('BESTE QUALITÄT:', quality?.model, '|', round(quality?.qualityPct) + '%');
  console.log('SCHNELLSTES:', fastest?.model, '|', round(fastest?.medianMs) + ' ms Median');
}

const stamp = new Date().toISOString().replace(/[:.]/g,'-');
const dir = path.resolve('benchmarks');
fs.mkdirSync(dir,{recursive:true});
const jsonPath = path.join(dir,`ollama-${stamp}.json`);
fs.writeFileSync(jsonPath, JSON.stringify({
  createdAt:new Date().toISOString(),
  ollamaUrl:config.ollama.url,
  think,
  timeoutMs,
  runs,
  cases:cases.map(c=>({name:c.name,max:c.max})),
  summaries,
  records
}, null, 2));

const csvPath = path.join(dir,`ollama-${stamp}.csv`);
const csv = [
  ['rank','model','balance_score','quality_pct','median_ms','tokens_per_second','cold_load_ms','successful_runs','errors'].join(','),
  ...summaries.map(s=>[
    s.rank,
    JSON.stringify(s.model),
    round(s.balanceScore),
    round(s.qualityPct),
    round(s.medianMs),
    round(s.tokensPerSecond,2),
    round(s.coldLoadMs),
    s.successfulRuns,
    s.errors
  ].join(','))
].join('\n');
fs.writeFileSync(csvPath,csv);

console.log('\nGespeichert:');
console.log(jsonPath);
console.log(csvPath);
