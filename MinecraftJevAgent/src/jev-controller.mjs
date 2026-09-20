import { config } from './config.mjs';

async function askChoice(state, criteria, instructions, questionName) {
  if (!config.jev.apiKey) {
    throw new Error('JEV_API_KEY is missing. Copy .env.example to .env and add your key.');
  }

  const body = {
    model: config.jev.model,
    state: JSON.stringify(state),
    questions: {
      [questionName]: {
        type: 'choice',
        instructions,
        criteria
      }
    }
  };

  const response = await fetch(config.jev.baseUrl + config.jev.path, {
    method: 'POST',
    headers: {
      Authorization: 'Bearer ' + config.jev.apiKey,
      'Content-Type': 'application/json'
    },
    body: JSON.stringify(body),
    signal: AbortSignal.timeout(120000)
  });

  const payload = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new Error(`JEV HTTP ${response.status}: ${JSON.stringify(payload)}`);
  }

  const root = payload.data || payload;
  const answer = root?.answers?.[questionName];
  return {answer, payload, body};
}

export async function choosePlan(state, planCandidates) {
  if (!planCandidates.length) throw new Error('No plan candidates available.');

  const criteria = Object.fromEntries(
    planCandidates.map((candidate, index) => [
      'p' + index,
      [
        `Objective: ${candidate.plan.objective}`,
        `Targets: ${JSON.stringify(candidate.plan.targets || {})}`,
        `Waypoint: ${JSON.stringify(candidate.plan.waypoint)}`,
        `Desired blocks: ${JSON.stringify(candidate.plan.desiredBlocks || [])}`,
        `Notes: ${candidate.plan.notes || ''}`,
        `Already-completed targets removed: ${JSON.stringify(candidate.completed || [])}`,
        `Currently available actions for this plan: ${JSON.stringify(candidate.availableActions || [])}`,
        `Useful non-wait actions: ${candidate.usefulActionCount ?? 0}`
      ].join(' | ')
    ])
  );

  const {answer, payload, body} = await askChoice(
    state,
    criteria,
    'Choose exactly one high-level Minecraft plan. Prefer a plan that is useful now, not already completed, and has concrete executable non-wait actions in the current state. Reject redundancy implicitly by choosing a different candidate. Use inventory, health, nearby observations, visible players and recent results. Avoid a plan whose only meaningful action is waiting when another candidate can make progress.',
    'plan'
  );

  const choice = answer?.choice;
  const index = Number(String(choice || '').replace(/^p/, ''));
  if (!Number.isInteger(index) || index < 0 || index >= planCandidates.length) {
    throw new Error('JEV returned an invalid plan: ' + JSON.stringify(answer));
  }

  return {
    candidate: planCandidates[index],
    choice,
    probabilities: answer?.probabilities || null,
    confidence: answer?.confidence ?? null,
    raw: payload,
    request: body
  };
}

export async function chooseAction(state, candidates) {
  if (!candidates.length) throw new Error('No action candidates available.');

  const criteria = Object.fromEntries(
    candidates.map((candidate, index) => ['a' + index, candidate.description])
  );

  const {answer, payload, body} = await askChoice(
    state,
    criteria,
    'Control the Minecraft player by choosing exactly one offered action. Choose the safe action that best advances the current planner objective. Use the current inventory, position, nearby observations, recent results and planner target. Avoid repeating failed actions or waiting when a useful safe action is available.',
    'action'
  );

  const choice = answer?.choice;
  const index = Number(String(choice || '').replace(/^a/, ''));
  if (!Number.isInteger(index) || index < 0 || index >= candidates.length) {
    throw new Error('JEV returned an invalid action: ' + JSON.stringify(answer));
  }

  return {
    candidate: candidates[index],
    choice,
    probabilities: answer?.probabilities || null,
    confidence: answer?.confidence ?? null,
    raw: payload,
    request: body
  };
}
