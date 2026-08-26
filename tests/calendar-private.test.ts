import assert from 'node:assert/strict';
import test from 'node:test';
import {
  dbCreatePrivateCategory,
  dbCreatePrivateEvent,
  dbGetPrivateCalendar,
  decryptPrivateData,
  encryptPrivateData,
} from '../server/db';

test('private calendar ciphertext is authenticated and bound to its owner', () => {
  process.env.CALENDAR_PRIVATE_DATA_KEY = 'test-only-key-with-at-least-32-random-characters';
  const cleartext = { categories: [{ id: 'private-1', name: 'Privat', color: '#123456' }], events: [] };
  const encrypted = encryptPrivateData('gustav', cleartext);
  assert.equal(encrypted.ciphertext.includes(Buffer.from('Privat')), false);
  assert.deepEqual(
    decryptPrivateData('gustav', { ...encrypted, auth_tag: encrypted.authTag }),
    cleartext,
  );
  assert.throws(() => decryptPrivateData('anderer', { ...encrypted, auth_tag: encrypted.authTag }));
});

test('private calendars are isolated and limited to five categories', async () => {
  process.env.CALENDAR_PRIVATE_DATA_KEY = 'test-only-key-with-at-least-32-random-characters';
  for (let index = 0; index < 5; index += 1) {
    await dbCreatePrivateCategory('gustav', { id: `g-${index}`, name: `G ${index}`, color: '#123456' });
  }
  await assert.rejects(
    dbCreatePrivateCategory('gustav', { id: 'g-5', name: 'Zu viel', color: '#123456' }),
    /CATEGORY_LIMIT/,
  );
  await dbCreatePrivateCategory('anderer', { id: 'a-0', name: 'Eigen', color: '#654321' });
  await dbCreatePrivateEvent('gustav', { id: 'event-g', title: 'Geheim', date: '2026-08-27', type: 'g-0' });
  assert.deepEqual((await dbGetPrivateCalendar('gustav')).events.map(event => event.id), ['event-g']);
  assert.deepEqual((await dbGetPrivateCalendar('anderer')).events, []);
});
