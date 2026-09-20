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

export function chooseHouseMaterial(state, remainingCount) {
  const inventory = state?.inventory || {};
  const preferred = [
    'oak_planks',
    'spruce_planks',
    'birch_planks',
    'jungle_planks',
    'acacia_planks',
    'dark_oak_planks',
    'cobblestone'
  ];

  // If we already own enough of a nicer material, use it. Otherwise choose
  // dirt because the current agent can reliably gather it almost anywhere.
  for (const name of preferred) {
    if (Number(inventory[name] || 0) >= remainingCount) return name;
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
