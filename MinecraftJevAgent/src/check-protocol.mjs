import { applyMinecraft26_2ProtocolPatch } from './protocol-26.2-patch.mjs';

try {
  const report = applyMinecraft26_2ProtocolPatch({verbose:true});
  console.log('\nOK: Minecraft Java 26.2 protocol patch is active.');
  console.log(JSON.stringify(report, null, 2));
  process.exit(0);
} catch (error) {
  console.error('\nFEHLER: 26.2-Protokoll konnte nicht korrigiert werden.');
  console.error(error.stack || error.message);
  process.exit(1);
}
