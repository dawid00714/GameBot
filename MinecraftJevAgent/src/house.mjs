import { Vec3 } from 'vec3';

export const HOUSE_TOTAL_BLOCKS = 55;

export function createHouseDirective(username, playerPosition) {
  const p = playerPosition.floored ? playerPosition.floored() : new Vec3(
    Math.floor(playerPosition.x),
    Math.floor(playerPosition.y),
    Math.floor(playerPosition.z)
  );

  // Build a few blocks away from the requesting player so we do not trap them.
  return {
    type: 'build_house',
    requestedBy: username,
    anchor: {
      x: p.x + 3,
      y: p.y,
      z: p.z + 3
    },
    material: null,
    createdAt: Date.now()
  };
}

export function houseBlueprint(anchor) {
  const out = [];
  const a = new Vec3(anchor.x, anchor.y, anchor.z);

  // 5x5 outer walls, two blocks high. Door is centered on the north wall.
  for (let y = 0; y <= 1; y++) {
    for (let x = 0; x < 5; x++) {
      for (let z = 0; z < 5; z++) {
        const perimeter = x === 0 || x === 4 || z === 0 || z === 4;
        if (!perimeter) continue;

        const door = z === 0 && x === 2 && (y === 0 || y === 1);
        if (door) continue;

        out.push(a.offset(x, y, z));
      }
    }
  }

  // Flat roof. Ordered row-by-row so every later roof block can attach to a
  // previously placed neighbor if the block below is air.
  for (let z = 0; z < 5; z++) {
    for (let x = 0; x < 5; x++) {
      out.push(a.offset(x, 2, z));
    }
  }

  return out;
}

function isEmpty(block) {
  return !block || ['air', 'cave_air', 'void_air'].includes(block.name);
}

export function getHouseStatus(bot, directive) {
  if (!directive?.anchor) {
    return {total: HOUSE_TOTAL_BLOCKS, placed: 0, remaining: HOUSE_TOTAL_BLOCKS, completed: false, remainingPositions: []};
  }

  const blueprint = houseBlueprint(directive.anchor);
  const remainingPositions = blueprint.filter(pos => isEmpty(bot.blockAt(pos)));
  return {
    total: blueprint.length,
    placed: blueprint.length - remainingPositions.length,
    remaining: remainingPositions.length,
    completed: remainingPositions.length === 0,
    remainingPositions
  };
}

export function chooseHouseMaterial(state, remainingCount, requested='auto') {
  const inventory = state?.inventory || {};
  const creative = String(state?.gameMode ?? '').toLowerCase().includes('creative')
    || Number(state?.gameMode) === 1;

  const wood = [
    'oak_planks','spruce_planks','birch_planks','jungle_planks','acacia_planks','dark_oak_planks',
    'mangrove_planks','cherry_planks','bamboo_planks','crimson_planks','warped_planks',
    'oak_log','spruce_log','birch_log','jungle_log','acacia_log','dark_oak_log',
    'mangrove_log','cherry_log'
  ];

  const preferred = requested === 'wood'
    ? wood
    : [...wood, 'cobblestone'];

  // Creative mode does not consume the held building block, so a single block
  // in the bot's inventory/hotbar is enough for the whole house.
  for (const name of preferred) {
    const have = Number(inventory[name] || 0);
    if (creative ? have > 0 : have >= remainingCount) return name;
  }

  // If wood was explicitly requested and some wood exists in Survival, keep
  // that choice rather than silently switching to dirt.
  if (requested === 'wood') {
    const partial = wood.find(name => Number(inventory[name] || 0) > 0);
    if (partial) return partial;
  }

  return 'dirt';
}


export function houseDoorInfo(directive) {
  if (!directive?.anchor) return null;
  const a = directive.anchor;
  return {
    lower: {x: a.x + 2, y: a.y, z: a.z},
    upper: {x: a.x + 2, y: a.y + 1, z: a.z},
    side: 'north',
    hasPhysicalDoor: false,
    description: 'two-block-high doorway opening centered on the north wall'
  };
}
