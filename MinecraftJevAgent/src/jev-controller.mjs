import { config } from './config.mjs';

export async function chooseAction(state, candidates) {
  if (!config.jev.apiKey) {
    throw new Error('JEV_API_KEY is missing. Copy .env.example to .env and add your key.');
  }
  if (!candidates.length) throw new Error('No action candidates available.');

  const criteria = Object.fromEntries(
    candidates.map((candidate, index) => ['a' + index, candidate.description])
  );

  const body = {
    model: config.jev.model,
    state: JSON.stringify(state),
    questions: {
      action: {
        type: 'choice',
        instructions:
          'Control the Minecraft player by choosing exactly one offered action. ' +
          'Choose the safe action that best advances the current planner objective. ' +
          'Use the current inventory, position, nearby observations, recent results and planner target. ' +
          'Avoid repeating failed actions or waiting when a useful safe action is available.',
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

  // OpenRouter's decision endpoint wraps the answer in data.
  // A direct compatible endpoint may return answers at the top level.
  const root = payload.data || payload;
  const answer = root?.answers?.action;
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
