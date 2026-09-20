import { mineflayer } from './minecraft-runtime.mjs';
import { config } from './config.mjs';

console.log('Teste Minecraft-Verbindung mit:');
console.log({
  host: config.minecraft.host,
  port: config.minecraft.port,
  version: config.minecraft.version,
  auth: config.minecraft.auth,
  username: config.minecraft.username
});

const bot = mineflayer.createBot({
  host: config.minecraft.host,
  port: config.minecraft.port,
  username: config.minecraft.username,
  version: config.minecraft.version,
  auth: config.minecraft.auth,
  checkTimeoutInterval: 30000
});

const timeout = setTimeout(() => {
  console.error('FEHLER: Nach 30 Sekunden kein Spawn.');
  try { bot.quit(); } catch {}
  process.exit(2);
}, 30000);

bot.once('spawn', () => {
  clearTimeout(timeout);
  console.log('OK: Bot ist Minecraft Java 26.2 beigetreten.');
  console.log('Position:', bot.entity.position);
  console.log('Server-Version:', bot.version);
  setTimeout(() => {
    try { bot.quit(); } catch {}
    process.exit(0);
  }, 1500);
});

bot.on('kicked', reason => {
  clearTimeout(timeout);
  console.error('KICK:', reason);
});

bot.on('error', error => {
  clearTimeout(timeout);
  console.error('VERBINDUNGSFEHLER:', {
    message: error.message,
    code: error.code,
    errno: error.errno,
    syscall: error.syscall
  });
  process.exitCode = 1;
});
