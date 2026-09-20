import { Vec3 } from 'vec3';
import pf from 'mineflayer-pathfinder';
import { config } from './config.mjs';

const { goals } = pf;

async function bounded(bot, promise, ms=config.actionTimeoutMs) {
  let timer;
  try {
    return await Promise.race([
      promise,
      new Promise((_, reject) => {
        timer = setTimeout(() => {
          try { bot.pathfinder.setGoal(null); } catch {}
          try { bot.clearControlStates(); } catch {}
          reject(new Error('Action timeout'));
        }, ms);
      })
    ]);
  } finally {
    clearTimeout(timer);
  }
}

async function goNear(bot, pos, range=2) {
  await bounded(bot, bot.pathfinder.goto(new goals.GoalNear(pos.x, pos.y, pos.z, range)));
  bot.pathfinder.setGoal(null);
  bot.clearControlStates();
}

async function mineBlock(bot, pos) {
  const block = bot.blockAt(pos);
  if (!block || block.name === 'air') throw new Error('Block is no longer available.');
  if (!block.diggable) throw new Error('Block is not diggable: ' + block.name);
  if (bot.entity.position.distanceTo(pos) > 4.5) await goNear(bot, pos, 3);
  const fresh = bot.blockAt(pos);
  if (!fresh || fresh.name === 'air') return 'Block already gone.';
  const tool = bot.pathfinder.bestHarvestTool?.(fresh);
  if (tool) await bot.equip(tool, 'hand');
  await bounded(bot, bot.dig(fresh, true));
  await bot.waitForTicks(4);
  return 'Mined ' + fresh.name;
}

function itemCount(state, name) {
  return Number(state.inventory?.[name] || 0);
}

function vec(p) {
  return new Vec3(Math.floor(p.x), Math.floor(p.y), Math.floor(p.z));
}

function nearestCraftingTable(bot) {
  const pos = bot.findBlock({
    matching: block => block.name === 'crafting_table',
    maxDistance: 4
  });
  return pos || null;
}

async function craftItem(bot, name) {
  const item = bot.registry.itemsByName[name];
  if (!item) throw new Error('Unknown item: ' + name);
  const table = nearestCraftingTable(bot);
  let recipes = bot.recipesFor(item.id, null, 1, table || null);
  if (!recipes.length && table) recipes = bot.recipesFor(item.id, null, 1, null);
  if (!recipes.length) throw new Error('No currently craftable recipe for ' + name);
  await bounded(bot, bot.craft(recipes[0], 1, table || null));
  return 'Crafted ' + name;
}

async function placeCraftingTable(bot) {
  const table = bot.inventory.items().find(i => i.name === 'crafting_table');
  if (!table) throw new Error('No crafting table in inventory.');
  await bot.equip(table, 'hand');
  const feet = bot.entity.position.floored();
  const refs = [
    feet.offset(0,-1,0), feet.offset(1,-1,0), feet.offset(-1,-1,0),
    feet.offset(0,-1,1), feet.offset(0,-1,-1)
  ];
  for (const refPos of refs) {
    const ref = bot.blockAt(refPos);
    const above = bot.blockAt(refPos.offset(0,1,0));
    if (ref?.boundingBox === 'block' && above?.name === 'air') {
      await bounded(bot, bot.placeBlock(ref, new Vec3(0,1,0)));
      return 'Placed crafting table.';
    }
  }
  throw new Error('No safe adjacent placement surface for crafting table.');
}

async function lootChest(bot, pos) {
  if (bot.entity.position.distanceTo(pos) > 4.5) await goNear(bot, pos, 3);
  const block = bot.blockAt(pos);
  if (!block || !['chest','barrel'].includes(block.name)) throw new Error('Container is no longer available.');
  const container = await bounded(bot, bot.openContainer(block));
  let moved = 0;
  try {
    for (const item of container.containerItems()) {
      try {
        await container.withdraw(item.type, item.metadata, item.count);
        moved += item.count;
      } catch {}
    }
  } finally {
    container.close();
  }
  return 'Looted container; moved ' + moved + ' items.';
}

export function buildCandidates(bot, state, plan) {
  const candidates = [];
  const add = (key, description, run) => candidates.push({key, description, run});

  // Immediate collectible drops.
  for (const entity of Object.values(bot.entities)
    .filter(e => e.name === 'item' && e.position)
    .sort((a,b) => a.position.distanceTo(bot.entity.position) - b.position.distanceTo(bot.entity.position))
    .slice(0, 3)) {
    add(
      'collect_' + entity.id,
      `Collect nearby dropped item at ${entity.position.floored()}; distance ${entity.position.distanceTo(bot.entity.position).toFixed(1)}.`,
      async () => {
        await goNear(bot, entity.position, 1);
        await bot.waitForTicks(4);
        return 'Approached dropped item.';
      }
    );
  }

  // Visible human players are explicit navigation targets. Move only a short,
  // bounded segment toward them per decision. This avoids one long dynamic
  // path to a moving entity and keeps movement similar to the already-tested
  // short exploration actions.
  for (const player of (state.nearbyPlayers || []).slice(0, 2)) {
    if (player.distance <= 2.5) continue;
    add(
      'follow_player_' + player.username,
      `Move a short step toward visible player ${player.username}; current distance ${player.distance} blocks.`,
      async () => {
        const live = bot.players?.[player.username]?.entity;
        if (!live?.position) throw new Error('Player is no longer visible: ' + player.username);

        const p = bot.entity.position;
        const dx = live.position.x - p.x;
        const dz = live.position.z - p.z;
        const horizontal = Math.hypot(dx, dz);

        if (horizontal <= 2.5) return 'Already near player ' + player.username;

        const maxStep = 4;
        const scale = Math.min(1, maxStep / horizontal);
        const dy = Math.max(-1, Math.min(1, live.position.y - p.y));
        const target = new Vec3(
          Math.round(p.x + dx * scale),
          Math.round(p.y + dy),
          Math.round(p.z + dz * scale)
        );

        await goNear(bot, target, 1);
        return `Moved one safe segment toward player ${player.username}; target ${target}.`;
      }
    );
  }

  // Planner waypoint becomes a bounded navigation action.
  if (plan?.waypoint) {
    const wp = vec(plan.waypoint);
    const distance = bot.entity.position.distanceTo(wp);
    if (distance > 3) {
      add(
        'waypoint',
        `Travel toward planner waypoint ${wp}; current straight-line distance ${distance.toFixed(1)} blocks.`,
        async () => {
          // Keep each decision bounded. Long trips are split into segments.
          const p = bot.entity.position;
          const horizontal = Math.hypot(wp.x - p.x, wp.z - p.z);
          const scale = horizontal > 20 ? 20 / horizontal : 1;
          const target = new Vec3(
            Math.round(p.x + (wp.x - p.x) * scale),
            Math.round(p.y + (wp.y - p.y) * Math.min(scale, 1)),
            Math.round(p.z + (wp.z - p.z) * scale)
          );
          await goNear(bot, target, 2);
          return 'Travelled one waypoint segment.';
        }
      );
    }
  }

  // Containers observed by the state.
  for (const block of (state.nearbyBlocks || [])
    .filter(b => ['chest','barrel'].includes(b.name))
    .slice(0, 2)) {
    const p = vec(block.position);
    add(
      'loot_' + p.toString(),
      `Open and loot nearby ${block.name} at ${p}; distance ${block.distance}.`,
      () => lootChest(bot, p)
    );
  }

  const resourceLike = name => name.endsWith('_log') || name.endsWith('_ore') || [
    'stone','cobblestone','dirt','sand','gravel','obsidian'
  ].includes(name);

  const targetStillNeeds = name => {
    const raw = plan?.targets?.[name];
    if (raw == null) return true;
    const minimum = Number(raw);
    if (!Number.isFinite(minimum)) return true;
    return itemCount(state, name) < minimum;
  };

  const desired = new Set(
    [...(plan?.desiredBlocks || []), ...Object.keys(plan?.targets || {})]
      .filter(resourceLike)
      .filter(targetStillNeeds)
  );

  const creativeMode = String(state.gameMode ?? '').toLowerCase().includes('creative')
    || Number(state.gameMode) === 1;

  const mineable = (state.nearbyBlocks || [])
    .filter(block => desired.has(block.name))
    // In Creative, breaking resource blocks does not satisfy inventory-count goals.
    .filter(block => !(creativeMode && Number.isFinite(Number(plan?.targets?.[block.name]))))
    .slice(0, 8);

  for (const block of mineable) {
    const p = vec(block.position);
    add(
      'mine_' + block.name + '_' + p.toString(),
      `Mine one observed ${block.name} at ${p}; distance ${block.distance}. Planner currently wants this resource.`,
      () => mineBlock(bot, p)
    );
  }

  // Offer crafting only for explicit planner targets that are still missing.
  for (const [name, minimumRaw] of Object.entries(plan?.targets || {})) {
    const minimum = Number(minimumRaw);
    if (!Number.isFinite(minimum) || itemCount(state, name) >= minimum) continue;
    const registryItem = bot.registry.itemsByName[name];
    if (!registryItem) continue;
    const table = nearestCraftingTable(bot);
    const recipe = bot.recipesFor(registryItem.id, null, 1, table || null)[0]
      || bot.recipesFor(registryItem.id, null, 1, null)[0];
    if (recipe) {
      add(
        'craft_' + name,
        `Craft one recipe for ${name}; have ${itemCount(state, name)}, planner target is at least ${minimum}.`,
        () => craftItem(bot, name)
      );
    }
  }

  if (state.inventory?.crafting_table && !nearestCraftingTable(bot)) {
    add(
      'place_crafting_table',
      'Place the carried crafting table on a safe adjacent block so table recipes can become available.',
      () => placeCraftingTable(bot)
    );
  }

  // Local exploration is available when the planner has no known waypoint.
  if (!plan?.waypoint) {
    const p = bot.entity.position.floored();
    const recentActions = new Set((state.recent || []).slice(-4).map(r => r.action));
    for (const [name, dx, dz] of [
      ['north',0,-8], ['east',8,0], ['south',0,8], ['west',-8,0]
    ]) {
      const key = 'explore_' + name;
      // Do not offer the same exploration direction again immediately.
      if (recentActions.has(key)) continue;
      const target = p.offset(dx, 0, dz);
      add(
        key,
        `Explore about 8 blocks ${name} to reveal more terrain because no trusted waypoint is known.`,
        () => goNear(bot, target, 2).then(() => 'Explored ' + name + '.')
      );
    }
  }

  add(
    'wait',
    'Wait briefly for fresh Minecraft observations. Use only when no safer useful progress action is available.',
    async () => {
      await bot.waitForTicks(10);
      return 'Waited briefly.';
    }
  );

  return candidates;
}
