import { createRequire } from 'node:module';

const rootRequire = createRequire(import.meta.url);

function patchOneProtocol(protocol) {
  const seen = new Set();
  let mappingFound = 0;
  let mappingPatched = 0;
  let typePatched = 0;
  let switchPatched = 0;

  function walk(node) {
    if (!node || typeof node !== 'object' || seen.has(node)) return;
    seen.add(node);

    if (!Array.isArray(node)) {
      const mappings = node.mappings;
      if (mappings && typeof mappings === 'object') {
        const looksLike26_2Serverbound =
          mappings['0x3c'] === 'set_test_block' &&
          mappings['0x3d'] === 'update_sign' &&
          (
            mappings['0x40'] === 'test_instance_block_action' ||
            mappings['0x41'] === 'test_instance_block_action'
          );

        if (looksLike26_2Serverbound) {
          mappingFound += 1;

          // Correct Minecraft Java 26.2 / protocol 776 serverbound IDs.
          // The Complexity 26.2.5 data accidentally omitted the UUID spectate
          // packet at 0x40 and had 0x3e/0x3f reversed, shifting later packets.
          mappings['0x3e'] = 'spectator_action';
          mappings['0x3f'] = 'arm_animation';
          mappings['0x40'] = 'spectate';
          mappings['0x41'] = 'test_instance_block_action';
          mappings['0x42'] = 'block_place';
          mappings['0x43'] = 'use_item';
          mappings['0x44'] = 'custom_click_action';
          mappingPatched += 1;
        }
      }

      if (
        Object.prototype.hasOwnProperty.call(node, 'packet_test_instance_block_action') &&
        !Object.prototype.hasOwnProperty.call(node, 'packet_spectate')
      ) {
        node.packet_spectate = [
          'container',
          [
            { name: 'target', type: 'UUID' }
          ]
        ];
        typePatched += 1;
      }

      const fields = node.fields;
      if (
        fields &&
        typeof fields === 'object' &&
        fields.test_instance_block_action === 'packet_test_instance_block_action' &&
        fields.block_place === 'packet_block_place' &&
        !fields.spectate
      ) {
        fields.spectate = 'packet_spectate';
        switchPatched += 1;
      }
    }

    for (const value of Object.values(node)) walk(value);
  }

  walk(protocol);

  return {
    mappingFound,
    mappingPatched,
    typePatched,
    switchPatched
  };
}

function addFactory(factories, label, req) {
  try {
    const factory = req('minecraft-data');
    if (typeof factory === 'function') {
      factories.push({ label, factory });
    }
  } catch {}
}

export function applyMinecraft26_2ProtocolPatch({verbose=true}={}) {
  const factories = [];

  addFactory(factories, 'project', rootRequire);

  try {
    const mineflayerPkg = rootRequire.resolve('mineflayer/package.json');
    const mineflayerRequire = createRequire(mineflayerPkg);
    addFactory(factories, 'mineflayer', mineflayerRequire);

    try {
      const protocolPkg = mineflayerRequire.resolve('minecraft-protocol/package.json');
      const protocolRequire = createRequire(protocolPkg);
      addFactory(factories, 'minecraft-protocol', protocolRequire);
    } catch {}
  } catch {}

  const uniqueProtocols = new Set();
  const results = [];

  for (const {label, factory} of factories) {
    try {
      const data = factory('26.2');
      if (!data?.protocol) continue;
      if (uniqueProtocols.has(data.protocol)) {
        results.push({label, shared:true});
        continue;
      }
      uniqueProtocols.add(data.protocol);
      const result = patchOneProtocol(data.protocol);
      results.push({label, ...result});
    } catch (error) {
      results.push({label, error:error.message});
    }
  }

  const patched = results.some(r => Number(r.mappingPatched) > 0);
  const found = results.some(r => Number(r.mappingFound) > 0);

  if (!found) {
    throw new Error(
      'Minecraft 26.2 protocol map was not found. Refusing to start because the known packet-ID bug cannot be corrected.'
    );
  }

  if (verbose) {
    console.log('[PROTOCOL] Minecraft 26.2 serverbound packet map corrected in memory.');
    console.log('[PROTOCOL] Correct IDs: 0x3e spectator_action, 0x3f arm_animation, 0x40 spectate, 0x41 test_instance_block_action, 0x42 block_place, 0x43 use_item, 0x44 custom_click_action.');
    if (!patched) {
      console.log('[PROTOCOL] Mapping was already corrected; no changes were necessary.');
    }
  }

  return {patched, found, results};
}
