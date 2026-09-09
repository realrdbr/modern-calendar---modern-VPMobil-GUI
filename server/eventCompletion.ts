import { dbGetUser, dbGetEvents, dbGetPrivateCalendar, dbSetEventCompleted } from './db';

export async function setVisibleEventCompletion(owner: string, id: string, completed: unknown) {
  const fail = (status: number, message: string): never => { throw Object.assign(new Error(message), { status }); };
  if (typeof completed !== 'boolean' || !id || id.length > 255) fail(400, 'Ungültiger Erledigt-Status.');
  const user = await dbGetUser(owner);
  if (!user || user.status === 'BLOCKED') fail(403, 'Kein Zugriff.');
  const [shared, personal] = await Promise.all([
    dbGetEvents(false, user.status === 'ADMIN' ? undefined : (user.courses || [])),
    dbGetPrivateCalendar(owner),
  ]);
  if (![...shared, ...personal.events].some(event => event.id === id)) fail(404, 'Termin nicht verfügbar.');
  await dbSetEventCompleted(owner, id, completed as boolean);
  return { id, completed: completed as boolean };
}
