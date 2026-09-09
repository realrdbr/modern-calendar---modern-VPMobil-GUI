import assert from 'node:assert/strict';
import test from 'node:test';
import { dbSaveUser, dbCreateEvent, dbGetEvents, dbGetCompletedEventIds, dbCreatePrivateCategory, dbCreatePrivateEvent, dbDeleteEvent } from '../server/db';
import { setVisibleEventCompletion } from '../server/eventCompletion';

const owner = 'completion-a';
const other = 'completion-b';
const makeEvent = (id: string, courseId = 'ALLGEMEIN') => ({ id, courseId, title: id, type: 'SONSTIGES', date: '2099-01-01', author: owner, description: '', attachments: [] });

test('completion is personal, reversible and does not edit the shared event', async () => {
  await dbSaveUser(owner, { courses: ['MA1'] });
  await dbSaveUser(other, { courses: ['MA1'], status: 'READ_ONLY' });
  await dbCreateEvent(makeEvent('completion-shared'));
  const before = (await dbGetEvents()).find(e => e.id === 'completion-shared');
  await setVisibleEventCompletion(owner, 'completion-shared', true);
  await setVisibleEventCompletion(owner, 'completion-shared', true);
  assert.deepEqual([...await dbGetCompletedEventIds(owner)], ['completion-shared']);
  assert.equal((await dbGetCompletedEventIds(other)).size, 0);
  assert.deepEqual((await dbGetEvents()).find(e => e.id === 'completion-shared'), before);
  await setVisibleEventCompletion(other, 'completion-shared', true);
  await setVisibleEventCompletion(owner, 'completion-shared', false);
  assert.equal((await dbGetCompletedEventIds(owner)).size, 0);
  assert.equal((await dbGetCompletedEventIds(other)).has('completion-shared'), true);
});

test('completion denies invisible courses, other users private events, deleted and unknown events', async () => {
  await dbSaveUser(owner, { courses: ['MA1'] });
  await dbSaveUser(other, { courses: [] });
  await dbCreateEvent(makeEvent('completion-course', 'MA1'));
  await assert.rejects(setVisibleEventCompletion(other, 'completion-course', true), { status: 404 });
  await dbCreatePrivateCategory(owner, { id: 'completion-private-category', name: 'Private', color: '#123456' });
  await dbCreatePrivateEvent(owner, { ...makeEvent('completion-private'), type: 'completion-private-category' });
  await setVisibleEventCompletion(owner, 'completion-private', true);
  await assert.rejects(setVisibleEventCompletion(other, 'completion-private', true), { status: 404 });
  await assert.rejects(setVisibleEventCompletion(owner, 'unknown-event', true), { status: 404 });
  await dbCreateEvent(makeEvent('completion-deleted'));
  await dbDeleteEvent('completion-deleted', owner);
  await assert.rejects(setVisibleEventCompletion(owner, 'completion-deleted', true), { status: 404 });
});

test('completion validates values and rejects blocked or nonexistent accounts', async () => {
  for (const invalid of ['true', 1, null, {}, undefined]) {
    await assert.rejects(setVisibleEventCompletion(owner, 'completion-shared', invalid), { status: 400 });
  }
  await dbSaveUser('completion-blocked', { status: 'BLOCKED' });
  await assert.rejects(setVisibleEventCompletion('completion-blocked', 'completion-shared', true), { status: 403 });
  await assert.rejects(setVisibleEventCompletion('completion-unknown-user', 'completion-shared', true), { status: 403 });
});
