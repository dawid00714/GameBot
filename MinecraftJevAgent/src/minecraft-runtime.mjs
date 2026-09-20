import { applyMinecraft26_2ProtocolPatch } from './protocol-26.2-patch.mjs';

// Patch the exact minecraft-data instance(s) used by Mineflayer and
// minecraft-protocol BEFORE either runtime is loaded.
export const protocolPatchReport = applyMinecraft26_2ProtocolPatch();

export const mineflayer = (await import('mineflayer')).default;
export const pf = (await import('mineflayer-pathfinder')).default;
