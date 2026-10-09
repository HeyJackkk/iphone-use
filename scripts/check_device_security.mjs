// Read-only, bounded acceptance against the configured personal iPhone.
// No input, app activation, session creation or screenshot persistence.
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import {createRequire} from 'node:module';

const state = process.argv[2] ?? path.join(os.homedir(), '.local/share/iphone-use');
const config = JSON.parse(fs.readFileSync(path.join(state, 'config.json'), 'utf8'));
const require = createRequire(path.join(state, 'runtime/forward/package.json'));
const iosDevice = require('appium-ios-device');
const token = fs.readFileSync(path.join(state, 'auth-token'), 'ascii');
if (!/^[a-f0-9]{64}$/.test(token)) throw new Error('Invalid private authentication file');

async function exchange(port, chunks, headersOnly = false, expectedResponses = 1) {
  const socket = await iosDevice.utilities.connectPort(config.udid, port);
  return new Promise((resolve, reject) => {
    let bytes = Buffer.alloc(0), finished = false;
    const finish = () => {
      if (finished) return;
      finished = true;
      clearTimeout(timer);
      socket.destroy();
      resolve(bytes.toString('latin1'));
    };
    const timer = setTimeout(() => {
      if (finished) return;
      finished = true;
      socket.destroy();
      reject(new Error('Bounded security probe timed out'));
    }, 6500);
    socket.on('data', chunk => {
      bytes = Buffer.concat([bytes, chunk]).subarray(0, 131072);
      if (headersOnly && bytes.includes(Buffer.from('\r\n\r\n'))) finish();
      if (!headersOnly) {
        let offset = 0, completed = 0;
        while (offset < bytes.length) {
          const end = bytes.indexOf(Buffer.from('\r\n\r\n'), offset);
          if (end < 0) break;
          const header = bytes.subarray(offset, end).toString('latin1');
          const size = header.match(/(?:^|\r\n)Content-Length:\s*(\d+)/i);
          if (!size || bytes.length < end + 4 + Number(size[1])) break;
          offset = end + 4 + Number(size[1]);
          completed++;
        }
        if (completed >= expectedResponses) finish();
      }
    });
    socket.on('end', finish);
    socket.on('close', finish);
    socket.on('error', finish);
    socket.resume();
    (async () => {
      for (const chunk of chunks) {
        if (finished) return;
        socket.write(chunk);
        if (chunks.length > 1) await new Promise(resolve => setTimeout(resolve, 25));
      }
    })().catch(reject);
  });
}

function request(target, headers = '', close = true) {
  return `GET ${target} HTTP/1.1\r\nHost: localhost\r\n${headers}Connection: ${close ? 'close' : 'keep-alive'}\r\n\r\n`;
}
function statuses(response) {
  return [...response.matchAll(/HTTP\/1\.[01] (\d{3})/g)].map(match => Number(match[1]));
}
function expect(condition, label) {
  if (!condition) throw new Error(`Security acceptance failed: ${label}`);
  console.log(`PASS ${label}`);
}
const auth = `Authorization: Bearer ${token}\r\n`;
const wrong = `Authorization: Bearer ${'0'.repeat(64)}\r\n`;
for (const [label, target, header, expected] of [
  ['HTTP missing token', '/status', '', 401],
  ['HTTP wrong token', '/status', wrong, 401],
  ['HTTP screenshot without token', '/screenshot', '', 401],
  ['HTTP duplicate authentication', '/status', auth + wrong, 400],
  ['HTTP valid authentication', '/status', auth, 200],
]) {
  const response = await exchange(config.device_port, [request(target, header)]);
  expect(statuses(response)[0] === expected, label);
}
const pipeline = await exchange(config.device_port, [request('/status', auth, false) + request('/status')], false, 2);
expect(JSON.stringify(statuses(pipeline)) === '[200,401]', 'Every pipelined HTTP request authenticated');
const streamPort = config.mjpeg_device_port ?? 9100;
for (const [label, headers] of [['MJPEG missing token', ''], ['MJPEG wrong token', wrong], ['MJPEG duplicate authentication', auth + wrong]]) {
  const response = await exchange(streamPort, [request('/', headers)], true);
  expect(!statuses(response).includes(200), label);
}
const oversized = await exchange(streamPort, ['x'.repeat(8193)], true);
expect(!statuses(oversized).includes(200), 'MJPEG oversized incomplete header rejected');
const stream = await exchange(streamPort, ['GET / HTTP/1.1\r\nHost: localhost\r\nAuthorization: Bearer ', token.slice(0, 17), token.slice(17), '\r\n\r\n'], true);
expect(statuses(stream)[0] === 200, 'MJPEG fragmented authenticated request retained');
