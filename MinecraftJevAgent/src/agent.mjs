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
  checkTimeoutInterval: 120000
});

bot.loadPlugin(pathfinder);

let stopped = false;
let step = 0;
let plan = null;
let recent = [];

bot.on('error', error => log('minecraft_error', {error: error.message}));
bot.on('kicked', reason => {
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
  log('planner_request', {model: config.ollama.model, state: plannerState});
  const next = await makePlan(plannerState);
  plan = next;
  log('planner_response', {plan});
  console.log('\nPLAN:', JSON.stringify(plan, null, 2));
}

async function main() {
  await bot.waitForChunksToLoad();

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

    const state = observe(bot, {plan, recent, step});
    const candidates = buildCandidates(bot, state, plan);

    let decision;
    try {
      decision = await chooseAction(state, candidates);
    } catch (error) {
      log('jev_error', {error: error.message});
      throw error;
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
