import 'dotenv/config';

const int = (name, fallback) => {
  const value = Number(process.env[name]);
  return Number.isFinite(value) ? value : fallback;
};

const bool = (name, fallback=false) => {
  const value = process.env[name];
  if (value == null || value === '') return fallback;
  return ['1','true','yes','on'].includes(String(value).toLowerCase());
};

const auth = (process.env.MC_AUTH || 'offline').toLowerCase();
if (!['offline', 'microsoft'].includes(auth)) {
  throw new Error('MC_AUTH must be "offline" or "microsoft".');
}

export const config = {
  minecraft: {
    host: process.env.MC_HOST || '127.0.0.1',
    port: int('MC_PORT', 25565),
    username: process.env.MC_USERNAME || 'JevOllama',
    version: process.env.MC_VERSION || '26.2',
    auth
  },
  ollama: {
    url: (process.env.OLLAMA_URL || 'http://127.0.0.1:11434').replace(/\/$/, ''),
    model: process.env.OLLAMA_MODEL || 'qwen3:8b',
    timeoutMs: int('OLLAMA_TIMEOUT_MS', 60000),
    think: bool('OLLAMA_THINK', false)
  },
  jev: {
    baseUrl: (process.env.JEV_BASE_URL || 'https://api.typesafe.ai').replace(/\/$/, ''),
    path: process.env.JEV_PATH || '/v1/systemone',
    model: process.env.JEV_MODEL || 'jev-latest',
    apiKey: process.env.JEV_API_KEY || ''
  },
  plannerEvery: int('PLANNER_EVERY_N_ACTIONS', 12),
  actionTimeoutMs: int('ACTION_TIMEOUT_MS', 20000),
  logDir: process.env.LOG_DIR || './runs',
  dreamRsi: {
    enabled: bool('DREAM_RSI_ENABLED', true),
    dir: process.env.DREAM_RSI_DIR || './dream-rsi',
    dreamEvery: int('DREAM_RSI_DREAM_EVERY', 40),
    minHistory: int('DREAM_RSI_MIN_HISTORY', 25),
    maxHistory: int('DREAM_RSI_MAX_HISTORY', 5000),
    proposals: int('DREAM_RSI_PROPOSALS', 4),
    minImprovement: Number.isFinite(Number(process.env.DREAM_RSI_MIN_IMPROVEMENT))
      ? Number(process.env.DREAM_RSI_MIN_IMPROVEMENT)
      : 0.03
  }
};
