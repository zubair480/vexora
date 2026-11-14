// A pretend Deepgram on this computer, for the tests of the online choice of Live transcript
// (deepgram.test.mjs, deepgram-check.mjs). It answers the way the real service was seen to
// answer in November 2026:
//
//   GET /v1/listen (WebSocket)     key in the request's protocol field:  Sec-WebSocket-Protocol: token, <key>
//   a wrong key                    no WebSocket: a plain 401 with the header dg-error
//   sound (binary messages)        "Results" messages: first guesses (is_final false), then final pieces
//   {"type":"CloseStream"}         the last Results, a "Metadata" message, then the connection closes
//
// It only uses what comes with Node. The keys below are made-up words, not real keys.
import { createServer } from 'node:http';
import { createHash } from 'node:crypto';

export const TEST_KEY = 'k-test';
export const BAD_KEY = 'k-bad';
export const SENTENCE = 'Hello from the pretend service.';

// ---- just enough of the WebSocket protocol (RFC 6455) for a test server

function frame(opcode, payload) {
  const body = Buffer.from(payload);
  let head;
  if (body.length < 126) head = Buffer.from([0x80 | opcode, body.length]);
  else {
    head = Buffer.alloc(4);
    head[0] = 0x80 | opcode;
    head[1] = 126;
    head.writeUInt16BE(body.length, 2);
  }
  return Buffer.concat([head, body]);
}

/** Calls onMessage(text or Buffer) for each message the client sends; returns { send, close }. */
function talk(socket, onMessage) {
  let waiting = Buffer.alloc(0);
  let parts = [];
  let kind = 0;
  const peer = {
    send: (data) => socket.writable && socket.write(frame(typeof data === 'string' ? 1 : 2, data)),
    close(code = 1000, reason = '') {
      if (!socket.writable) return;
      const body = Buffer.alloc(2 + Buffer.byteLength(reason));
      body.writeUInt16BE(code);
      body.write(reason, 2);
      socket.end(frame(8, body));
    },
  };
  socket.on('error', () => {});
  socket.on('data', (data) => {
    waiting = Buffer.concat([waiting, data]);
    for (;;) {
      if (waiting.length < 2) return;
      const opcode = waiting[0] & 0x0f;
      const last = (waiting[0] & 0x80) !== 0;
      let size = waiting[1] & 0x7f;
      let at = 2;
      if (size === 126) {
        if (waiting.length < 4) return;
        size = waiting.readUInt16BE(2);
        at = 4;
      } else if (size === 127) {
        if (waiting.length < 10) return;
        size = Number(waiting.readBigUInt64BE(2));
        at = 10;
      }
      if (waiting.length < at + 4 + size) return;
      const mask = waiting.subarray(at, at + 4);      // what a client sends is always masked
      const body = Buffer.from(waiting.subarray(at + 4, at + 4 + size));
      for (let i = 0; i < body.length; i++) body[i] ^= mask[i & 3];
      waiting = waiting.subarray(at + 4 + size);
      if (opcode === 8) {
        peer.close();
        return;
      }
      if (opcode === 9) socket.write(frame(10, body));
      if (opcode > 2) continue;
      if (opcode !== 0) kind = opcode;
      parts.push(body);
      if (last) {
        const whole = Buffer.concat(parts);
        parts = [];
        onMessage(kind === 1 ? whole.toString('utf8') : whole);
      }
    }
  });
  return peer;
}

/**
 * Start the pretend service on a free port of this computer.
 * @param {object} [options]
 * @param {string} [options.closeReason] after 20 packets, close with code 1011 and this reason (a service that fails)
 * @returns {Promise<{ port: number, ws: string, connections: object[], close: () => Promise<void> }>}
 *   connections: one record per request, with everything the client sent
 */
export async function startMock({ closeReason = '' } = {}) {
  const connections = [];
  const sockets = new Set();
  const server = createServer((request, response) => response.writeHead(404).end());

  server.on('upgrade', (request, socket) => {
    sockets.add(socket);
    socket.on('close', () => sockets.delete(socket));
    const address = new URL(request.url, 'http://mock');
    const [scheme, key] = (request.headers['sec-websocket-protocol'] ?? '').split(',').map((s) => s.trim());
    const record = {
      url: request.url, path: address.pathname, query: Object.fromEntries(address.searchParams), scheme, key,
      origin: request.headers.origin, referer: request.headers.referer, cookie: request.headers.cookie,
      binary: [], text: [], closed: false,
    };
    connections.push(record);
    if (address.pathname !== '/v1/listen') {
      socket.end('HTTP/1.1 404 Not Found\r\ncontent-length: 0\r\n\r\n');
      return;
    }
    if (scheme !== 'token' || !key || key === BAD_KEY) {
      socket.end('HTTP/1.1 401 Unauthorized\r\ncontent-type: application/json\r\ndg-error: Invalid credentials.\r\n'
        + `content-length: 53\r\n\r\n{"err_code":"INVALID_AUTH","err_msg":"SERVER-SECRET"}`);
      return;
    }
    const hash = createHash('sha1').update(`${request.headers['sec-websocket-key']}258EAFA5-E914-47DA-95CA-C5AB0DC85B11`).digest('base64');
    socket.write(`HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\nConnection: Upgrade\r\nSec-WebSocket-Accept: ${hash}\r\nSec-WebSocket-Protocol: token\r\n\r\n`);
    // One Results message. The times are in seconds of the sound received so far (20 ms a packet).
    const results = (transcript, isFinal, speechFinal) => {
      const until = record.binary.length * 0.02;
      return JSON.stringify({
        type: 'Results', channel_index: [0, 1], start: Math.max(0, until - 1), duration: Math.min(1, until), is_final: isFinal, speech_final: speechFinal,
        channel: { alternatives: [{ transcript, confidence: 0.99, words: transcript ? [{ word: 'x', start: Math.max(0, until - 0.4), end: Math.max(0, until - 0.1) }] : [] }] },
        metadata: { request_id: 'mock' },
      });
    };
    const peer = talk(socket, (message) => {
      if (typeof message === 'string') {
        record.text.push(message);
        let type = '';
        try {
          type = JSON.parse(message).type;
        } catch {
          // not JSON: kept in the record, nothing answered
        }
        if (type === 'Finalize') peer.send(results('', true, true));
        if (type === 'CloseStream') {
          record.closed = true;
          peer.send(results('service.', true, true));
          peer.send(results('', true, true));
          peer.send(JSON.stringify({ type: 'Metadata', request_id: 'mock', sha256: '', created: '', duration: record.binary.length * 0.02, channels: 1 }));
          peer.close(1000);
        }
        return;
      }
      record.binary.push(message);
      const said = record.binary.length;
      if (said === 1) peer.send(results('', false, false));
      if (said === 3) peer.send(results('hello from', false, false));
      if (said === 5) peer.send(results('Hello from the', true, false));
      if (said === 7) peer.send(results('pretend', false, false));
      if (said === 9) peer.send(results('pretend', true, false));
      if (said === 20 && closeReason) peer.close(1011, closeReason);
    });
  });

  await new Promise((resolve) => server.listen(0, '127.0.0.1', resolve));
  const { port } = server.address();
  return {
    port,
    ws: `ws://127.0.0.1:${port}`,
    connections,
    close() {
      for (const socket of sockets) socket.destroy();
      return new Promise((resolve) => server.close(resolve));
    },
  };
}
