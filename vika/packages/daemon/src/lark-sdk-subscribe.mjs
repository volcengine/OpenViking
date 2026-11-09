// Official SDK event transport. CLI still owns identity/auth and all operations.
// Credentials stay in this local connector subprocess and never reach MA.
import fs from 'node:fs';
import * as Lark from '@larksuiteoapi/node-sdk';
import { SUBSCRIBED_EVENT_TYPES } from './lark.mjs';
import { sdkEventEnvelope } from './lark-sdk-events.mjs';

const file = process.env.VIKA_LARK_WS_CREDENTIALS;
if (!file) throw new Error('VIKA_LARK_WS_CREDENTIALS is required for SDK subscription');
const stat = fs.lstatSync(file);
if (!stat.isFile() || stat.isSymbolicLink() || (stat.mode & 0o077) !== 0
  || (process.getuid && stat.uid !== process.getuid())) throw new Error('Lark websocket credentials must be an owned mode-0600 regular file');
const credentials = JSON.parse(fs.readFileSync(file, 'utf8'));
if (!credentials.appId || !credentials.appSecret) throw new Error('Lark websocket credentials require appId and appSecret');
const allowedChats = new Set((process.env.VIKA_LARK_ALLOWED_CHAT_IDS || '').split(',').filter(Boolean));
const sdkLogger = { error: () => process.stderr.write('Lark SDK transport error (details withheld)\n'),
  warn() {}, info() {}, debug() {}, trace() {} };
const client = new Lark.WSClient({ appId: credentials.appId, appSecret: credentials.appSecret,
  loggerLevel: Lark.LoggerLevel.error, logger: sdkLogger,
  onReady: () => process.stdout.write('{"type":"__vika_sdk_ready"}\n'),
  onError: () => { process.stderr.write('Lark SDK connection failed\n'); process.exit(1); } });
const handlers = Object.fromEntries(SUBSCRIBED_EVENT_TYPES.map((eventType) => [eventType, async (data) => {
  const envelope = sdkEventEnvelope(eventType, data, allowedChats);
  if (envelope) process.stdout.write(`${JSON.stringify(envelope)}\n`);
  return {};
}]));
// Subscribed read receipts carry no user input; acknowledge without forwarding.
handlers['im.message.message_read_v1'] = async () => ({});
await client.start({ eventDispatcher: new Lark.EventDispatcher({ logger: sdkLogger, loggerLevel: Lark.LoggerLevel.error }).register(handlers) });
const stop = () => { client.close({ force: true }); process.exit(0); };
process.once('SIGTERM', stop); process.once('SIGINT', stop);
