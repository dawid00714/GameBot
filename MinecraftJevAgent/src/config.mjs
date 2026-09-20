import 'dotenv/config';

const int = (name, fallback) => {
  const value = Number(process.env[name]);
  return Number.isFinite(value) ? value : fallback;
};

export const config = {
  minecraft: {
    host: process.env.MC_HOST || '127.0.0.1',
    port: int('MC_PORT', 25565),
    username: process.env.MC_USERNAME || 'JevOllama',
    version: process.env.MC_VERSION || '1.16.5'
  },
  ollama: {
    url: (process.env.OLLAMA_URL || 'http://127.0.0.1:11434').replace(/\/$/, ''),
    model: process.env.OLLAMA_MODEL || 'qwen3:8b'
  },
  jev: {
    baseUrl: (process.env.JEV_BASE_URL || 'https://openrouter.ai').replace(/\/$/, ''),
    path: process.env.JEV_PATH || '/api/alpha/decisions',
    model: process.env.JEV_MODEL || 'typesafe/jev-1.13',
    apiKey: process.env.JEV_API_KEY || ''
  },
  plannerEvery: int('PLANNER_EVERY_N_ACTIONS', 12),
  actionTimeoutMs: int('ACTION_TIMEOUT_MS', 20000),
  logDir: process.env.LOG_DIR || './runs'
};
