import assert from 'node:assert/strict';
import { test } from 'node:test';
import { mkdtemp, writeFile, rm } from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import express from 'express';
import { uploadDownloads } from '../server/uploadDownloads';

test('attachments retain bytes; absent files never become HTML downloads', async () => {
  const directory = await mkdtemp(path.join(os.tmpdir(), 'calendar-download-'));
  const app = express();
  app.use('/uploads', uploadDownloads(directory));
  app.get('*', (_req, res) => res.type('html').send('<html>SPA</html>'));
  const server = app.listen(0, '127.0.0.1');
  await new Promise<void>(resolve => server.once('listening', resolve));
  const address = server.address() as { port: number };
  try {
    for (const ext of ['pdf', 'docx', 'xlsx', 'zip', 'txt', 'bin']) {
      const bytes = Buffer.from([0, 255, 13, 10, 60, 62]);
      await writeFile(path.join(directory, `file.${ext}`), bytes);
      const res = await fetch(`http://127.0.0.1:${address.port}/uploads/file.${ext}`);
      assert.equal(res.status, 200);
      assert.equal(res.headers.get('content-type'), 'application/octet-stream');
      assert.equal(res.headers.get('content-disposition'), 'attachment');
      assert.equal(res.headers.get('x-content-type-options'), 'nosniff');
      assert.deepEqual(Buffer.from(await res.arrayBuffer()), bytes);
    }
    for (const name of ['missing.pdf', '']) {
      const res = await fetch(`http://127.0.0.1:${address.port}/uploads/${name}`);
      assert.equal(res.status, 404);
      assert.match(res.headers.get('content-type')!, /application\/json/);
    }
  } finally {
    await new Promise<void>((resolve, reject) => server.close(e => e ? reject(e) : resolve()));
    await rm(directory, { recursive: true, force: true });
  }
});
