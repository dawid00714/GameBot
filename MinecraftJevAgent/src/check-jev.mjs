import { config } from './config.mjs';
import { chooseAction } from './jev-controller.mjs';

console.log('Teste TypeSafe/JEV mit:');
console.log({
  baseUrl: config.jev.baseUrl,
  path: config.jev.path,
  model: config.jev.model,
  apiKeyPresent: Boolean(config.jev.apiKey)
});

const candidates = [
  { key: 'continue', description: 'Continue the test successfully.' },
  { key: 'stop', description: 'Stop the test.' }
];

try {
  const result = await chooseAction(
    { test: true, goal: 'Choose continue for this connectivity test.' },
    candidates
  );
  console.log('OK: JEV antwortet.');
  console.log({
    choice: result.choice,
    selected: result.candidate.key,
    confidence: result.confidence,
    probabilities: result.probabilities
  });
  process.exit(0);
} catch (error) {
  console.error('JEV-TEST FEHLGESCHLAGEN:');
  console.error(error.message);
  process.exit(1);
}
