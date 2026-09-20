const INTERESTING_BLOCKS = new Set([
  'oak_log','birch_log','spruce_log','jungle_log','acacia_log','dark_oak_log',
  'oak_planks','birch_planks','spruce_planks',
  'stone','cobblestone','coal_ore','iron_ore','gold_ore','diamond_ore',
  'crafting_table','furnace','chest','barrel',
  'dirt','sand','gravel','obsidian',
  'white_bed','red_bed','blue_bed','yellow_bed',
  'water','lava'
]);

function position(v) {
  return v ? {
    x: Number(v.x.toFixed(2)),
    y: Number(v.y.toFixed(2)),
    z: Number(v.z.toFixed(2))
  } : null;
}

function inventory(bot) {
  const out = {};
  for (const item of bot.inventory.items()) {
    out[item.name] = (out[item.name] || 0) + item.count;
  }
  return out;
}

function nearbyBlocks(bot) {
  if (!bot.entity) return [];
  const found = bot.findBlocks({
    matching: block => INTERESTING_BLOCKS.has(block.name),
    maxDistance: 24,
    count: 48
  });

  return found
    .map(pos => {
      const block = bot.blockAt(pos);
      if (!block) return null;
      return {
        name: block.name,
        position: position(pos),
        distance: Number(bot.entity.position.distanceTo(pos).toFixed(1))
      };
    })
    .filter(Boolean)
    .sort((a,b) => a.distance - b.distance)
    .slice(0, 32);
}

function nearbyEntities(bot) {
  if (!bot.entity) return [];
  return Object.values(bot.entities)
    .filter(entity => entity !== bot.entity && entity.position)
    .map(entity => ({
      id: entity.id,
      name: entity.name || entity.username || entity.displayName || 'unknown',
      type: entity.type,
      position: position(entity.position),
      distance: Number(bot.entity.position.distanceTo(entity.position).toFixed(1))
    }))
    .sort((a,b) => a.distance - b.distance)
    .slice(0, 20);
}

export function observe(bot, {plan=null, recent=[], step=0}={}) {
  return {
    step,
    position: position(bot.entity?.position),
    dimension: bot.game?.dimension,
    difficulty: bot.game?.difficulty,
    gameMode: bot.game?.gameMode ?? bot.player?.gamemode ?? null,
    health: bot.health,
    food: bot.food,
    timeOfDay: bot.time?.timeOfDay,
    isRaining: bot.isRaining,
    inventory: inventory(bot),
    nearbyBlocks: nearbyBlocks(bot),
    nearbyEntities: nearbyEntities(bot),
    planner: plan,
    recent: recent.slice(-8)
  };
}
