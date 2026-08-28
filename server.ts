import express from 'express';
import path from 'path';
import fs from 'fs';
import { createServer as createViteServer } from 'vite';
import { v4 as uuidv4 } from 'uuid';
import {
  initDatabase,
  isDbConnected,
  dbGetUser,
  dbGetUsers,
  dbSaveUser,
  dbDeleteUser,
  dbGetEvents,
  dbGetPrivateCalendar,
  dbCreatePrivateCategory,
  dbDeletePrivateCategory,
  dbCreatePrivateEvent,
  dbUpdatePrivateEvent,
  dbDeletePrivateEvent,
  dbCleanupExpiredEvents,
  dbCreateEvent,
  dbUpdateEvent,
  dbDeleteEvent,
  dbRecordCalendarLoginAttempt,
  dbGetEventById,
  dbCreateFeedback,
  dbGetCourses,
  dbSaveCourse,
  dbDeleteCourse,
  dbReorderCourses,
  dbResetCoursesToDefaults,
  dbGetCategories,
  dbSaveCategory,
  dbDeleteCategory,
  dbReorderCategories,
  verifyPin,
  generateSessionToken,
  verifySessionToken,
  getSessionUsername,
  deleteSessionToken,
  deleteUserSessions,
  dbGetAdmins,
  dbIsAdmin,
  dbAddAdmin,
  dbRemoveAdmin,
  dbGetVpOnlyUsers,
  dbCreateVpOnlyUser,
  dbSetVpOnlyUserStatus,
  dbSetVpOnlyUserPin,
  dbDeleteVpOnlyUser
} from './server/db';

function sanitizeUser(user: any) {
  if (!user) return null;
  const { pin, ...rest } = user;
  return {
    ...rest,
    hasPin: !!pin
  };
}

function validCalendarDate(value: unknown): value is string {
  return typeof value === 'string' && /^\d{4}-\d{2}-\d{2}$/.test(value) && !Number.isNaN(Date.parse(`${value}T00:00:00Z`));
}

function eventRetentionCutoff(): string {
  const cutoff = new Date();
  cutoff.setMonth(cutoff.getMonth() - 18);
  return cutoff.toISOString().slice(0, 10);
}

function safeEventUpdate(body: any) {
  const allowed = ['title', 'date', 'endDate', 'startTime', 'endTime', 'courseId', 'type', 'description', 'attachments'];
  return Object.fromEntries(allowed.filter(key => body[key] !== undefined).map(key => [key, body[key]]));
}

async function startServer() {
  const app = express();
  app.disable('x-powered-by');
  app.use((req, res, next) => {
    res.setHeader('X-Content-Type-Options', 'nosniff');
    res.setHeader('X-Frame-Options', 'DENY');
    res.setHeader('Referrer-Policy', 'same-origin');
    res.setHeader('Permissions-Policy', 'camera=(), microphone=(), geolocation=()');
    if (process.env.NODE_ENV === 'production') {
      res.setHeader('Strict-Transport-Security', 'max-age=31536000; includeSubDomains');
      res.setHeader('Content-Security-Policy', "default-src 'self'; base-uri 'self'; frame-ancestors 'none'; object-src 'none'; img-src 'self' data: blob:; style-src 'self' 'unsafe-inline'; script-src 'self' 'unsafe-inline'");
    }
    next();
  });
  const PORT = Number.parseInt(process.env.CAL11_PORT || '3000', 10);
  const requestedHost = process.env.BIND_HOST || process.env.HOST || '127.0.0.1';
  const runningInDocker = fs.existsSync('/.dockerenv');
  const HOST = runningInDocker && (requestedHost === '127.0.0.1' || requestedHost === 'localhost')
    ? '0.0.0.0'
    : requestedHost;

  // Initialize DB or in-memory fallback
  await initDatabase();
  await dbCleanupExpiredEvents();
  const scheduleCalendarCleanup = () => {
    const now = new Date();
    const next = new Date(now);
    next.setHours(24, 0, 5, 0);
    setTimeout(async () => {
      try { await dbCleanupExpiredEvents(); } catch (error) { console.error('[Cleanup] Kalender:', error); }
      scheduleCalendarCleanup();
    }, next.getTime() - now.getTime());
  };
  scheduleCalendarCleanup();

  const uploadsDir = process.env.UPLOADS_DIR || path.join(process.cwd(), 'uploads');
  if (!fs.existsSync(uploadsDir)) {
    fs.mkdirSync(uploadsDir, { recursive: true });
  }

  app.use(express.json({ limit: '50mb' }));
  app.use(express.urlencoded({ limit: '50mb', extended: true }));

  // Require active DB connection for all API routes
  app.use('/api', (req, res, next) => {
    if (req.path === '/health') return next();
    if (!isDbConnected()) {
      return res.status(503).json({ error: 'Keine Verbindung zur Datenbank. Zugriff verweigert.' });
    }
    next();
  });
  app.use('/api', (req, res, next) => {
    if (!['POST', 'PUT', 'PATCH', 'DELETE'].includes(req.method)) return next();
    const origin = req.get('Origin');
    if (!origin) return next();
    try {
      const allowed = new Set([process.env.CALENDAR_PUBLIC_URL, process.env.VERTRETUNGSPLAN_PUBLIC_URL]
        .filter(Boolean).map(value => new URL(String(value)).origin));
      if (!allowed.has(origin)) return res.status(403).json({ error: 'Ungültige Herkunft der Anfrage.' });
    } catch { return res.status(403).json({ error: 'Ungültige Herkunft der Anfrage.' }); }
    next();
  });

  // API Routes
  
  app.get('/api/check', async (req, res) => {
    const username = (req.query.username as string || '').toLowerCase();
    if (!username) return res.json({ exists: false });
    
    const user = await dbGetUser(username);
    if (!user) {
      return res.json({ exists: false, available: true, requiresPin: false });
    }
    if (user.status === 'BLOCKED') {
      return res.json({ exists: true, available: false, requiresPin: false, blocked: true, status: 'BLOCKED', error: 'Dieses Konto wurde gesperrt.' });
    }
    return res.json({ exists: true, available: false, requiresPin: !!user.pin, status: user.status || 'ACTIVE' });
  });

  app.post('/api/register', async (req, res) => {
    return res.status(403).json({ error: 'Registrierung zur Zeit deaktiviert.' });
  });

  // Rate limiting for login attempts
  const failedLoginAttempts = new Map<string, { count: number; lockUntil: number; lastFailedAt: number }>();
  const LOGIN_WINDOW_MS = 15 * 60 * 1000;
  const LOGIN_LOCK_MS = 5 * 60 * 1000;

  function loginKey(username: string, ip: string): string {
    return `${username}::${ip}`;
  }

  function cleanupAttempts() {
    const cutoff = Date.now() - LOGIN_WINDOW_MS;
    for (const [key, value] of failedLoginAttempts.entries()) {
      if (value.lastFailedAt < cutoff && value.lockUntil < Date.now()) {
        failedLoginAttempts.delete(key);
      }
    }
  }

  function checkRateLimit(key: string): { allowed: boolean; remainingSeconds?: number } {
    cleanupAttempts();
    const attempt = failedLoginAttempts.get(key);
    if (!attempt) return { allowed: true };
    if (Date.now() < attempt.lockUntil) {
      const remainingSeconds = Math.ceil((attempt.lockUntil - Date.now()) / 1000);
      return { allowed: false, remainingSeconds };
    }
    if (Date.now() >= attempt.lockUntil) {
      failedLoginAttempts.delete(key);
    }
    return { allowed: true };
  }

  function recordFailedAttempt(key: string) {
    const now = Date.now();
    const current = failedLoginAttempts.get(key) || { count: 0, lockUntil: 0, lastFailedAt: now };
    if (now - current.lastFailedAt > LOGIN_WINDOW_MS) {
      current.count = 0;
      current.lockUntil = 0;
    }
    current.count += 1;
    current.lastFailedAt = now;
    if (current.count >= 8) {
      current.lockUntil = now + LOGIN_LOCK_MS; // 5 Minutensperre
    }
    failedLoginAttempts.set(key, current);
  }

  function resetFailedAttempt(key: string) {
    failedLoginAttempts.delete(key);
  }

  app.post('/api/login', async (req, res) => {
    const { username, pin, sessionToken } = req.body;
    const uname = (username || '').toLowerCase();
    const ipAddress = req.ip || req.socket.remoteAddress || 'unknown';
    const rateLimitKey = loginKey(uname, ipAddress);
    const recordLoginAttempt = (successful: boolean) => dbRecordCalendarLoginAttempt(uname, ipAddress, successful);

    const rateCheck = checkRateLimit(rateLimitKey);
    if (!rateCheck.allowed) {
      await recordLoginAttempt(false);
      return res.status(429).json({ error: `Zu viele fehlerhafte Anmeldeversuche. Bitte warte ${rateCheck.remainingSeconds} Sekunden.` });
    }

    const user = await dbGetUser(uname);
    if (!user) {
      recordFailedAttempt(rateLimitKey);
      await recordLoginAttempt(false);
      return res.status(404).json({ error: 'Benutzer nicht gefunden' });
    }

    if (user.status === 'BLOCKED') {
      await recordLoginAttempt(false);
      return res.status(403).json({ error: 'Dein Konto wurde aufgrund unangemessener Aktivitäten gesperrt.' });
    }

    if (user.pin) {
      if (sessionToken) {
        if (!(await verifySessionToken(uname, sessionToken))) {
          await recordLoginAttempt(false);
          return res.status(401).json({ error: 'Sitzung abgelaufen oder ungültig. Bitte erneut anmelden.' });
        }
      } else {
        if (!verifyPin(pin, user.pin)) {
          recordFailedAttempt(rateLimitKey);
          await recordLoginAttempt(false);
          return res.status(401).json({ error: 'Falscher PIN' });
        }
        if (typeof user.pin === 'string' && !user.pin.startsWith('scrypt$')) {
          await dbSaveUser(uname, { pin });
        }
      }
    }

    resetFailedAttempt(rateLimitKey);
    await recordLoginAttempt(true);
    const token = await generateSessionToken(uname);
    res.setHeader('Set-Cookie', sessionCookies(token));
    res.json({ user: sanitizeUser(user) });
  });

  // Authentication middleware
  const requireAuth = async (req: express.Request, res: express.Response, next: express.NextFunction) => {
    const authHeader = req.headers.authorization;
    let token = '';
    if (authHeader && authHeader.startsWith('Bearer ')) {
      token = authHeader.substring(7);
    } else if (req.headers['x-session-token']) {
      token = req.headers['x-session-token'] as string;
    } else {
      for (const candidate of cookieTokens(req)) {
        if (await getSessionUsername(candidate)) {
          token = candidate;
          break;
        }
      }
    }

    if (!token) {
      return res.status(401).json({ error: 'Nicht authentifiziert (Session-Token fehlt).' });
    }

    const username = await getSessionUsername(token);
    if (!username) {
      return res.status(401).json({ error: 'Ungültige oder abgelaufene Sitzung. Bitte erneut anmelden.' });
    }

    const user = await dbGetUser(username);
    if (user && user.status === 'BLOCKED') {
      return res.status(403).json({ error: 'Dein Konto wurde aufgrund unangemessener Aktivitäten gesperrt.' });
    }

    (req as any).authenticatedUser = username;
    (req as any).authUserStatus = user?.status || 'ACTIVE';
    next();
  };

  app.use('/uploads', requireAuth, express.static(uploadsDir));

  const requireWriteAuth = async (req: express.Request, res: express.Response, next: express.NextFunction) => {
    requireAuth(req, res, () => {
      if ((req as any).authUserStatus === 'READ_ONLY') {
        return res.status(403).json({ error: 'Dein Konto hat aufgrund unangemessener Aktivitäten nur Leserechte.' });
      }
      next();
    });
  };

  const requireAdmin = async (req: express.Request, res: express.Response, next: express.NextFunction) => {
    requireAuth(req, res, async () => {
      const currentUser = (req as any).authenticatedUser;
      if (!(await dbIsAdmin(currentUser))) {
        return res.status(403).json({ error: 'Nur Administratoren haben Zugriff auf diese Funktion.' });
      }
      next();
    });
  };

  const calendarStreams = new Map<express.Response, string>();
  const broadcastCalendarChange = (privateOwner?: string) => {
    const payload = `event: calendar-change\ndata: ${JSON.stringify({ changedAt: Date.now() })}\n\n`;
    for (const [stream, username] of calendarStreams) {
      if (!privateOwner || username === privateOwner.toLowerCase()) stream.write(payload);
    }
  };

  app.get('/api/events/stream', requireAuth, (req, res) => {
    const username = String((req as any).authenticatedUser).toLowerCase();
    const openStreams = Array.from(calendarStreams.values()).filter(value => value === username).length;
    if (openStreams >= 4) return res.status(429).json({ error: 'Zu viele Live-Verbindungen für dieses Konto.' });
    res.status(200).set({
      'Content-Type': 'text/event-stream',
      'Cache-Control': 'no-cache, no-transform',
      'Connection': 'keep-alive',
      'X-Accel-Buffering': 'no',
    });
    res.flushHeaders();
    calendarStreams.set(res, username);
    res.write(`event: connected\ndata: {}\n\n`);
    const heartbeat = setInterval(() => res.write(': heartbeat\n\n'), 25000);
    req.on('close', () => {
      clearInterval(heartbeat);
      calendarStreams.delete(res);
    });
  });

  app.get('/api/users/:username', requireAuth, async (req, res) => {
    const username = (req.params.username as string || '').toLowerCase();
    const requester = String((req as any).authenticatedUser).toLowerCase();
    if (requester !== username && !(await dbIsAdmin(requester))) {
      return res.status(403).json({ error: 'Sie dürfen nur Ihr eigenes Profil abrufen.' });
    }
    const user = await dbGetUser(username);
    if (user) {
      res.json(sanitizeUser(user));
    } else {
      res.status(404).json({ error: 'User not found' });
    }
  });

  app.post('/api/users/:username', requireAuth, async (req, res) => {
    const username = (req.params.username as string || '').toLowerCase();
    const authUser = (req as any).authenticatedUser;

    if (authUser !== username) {
      return res.status(403).json({ error: 'Sie dürfen nur Ihr eigenes Profil bearbeiten.' });
    }

    const existing = await dbGetUser(username);
    if (!existing) {
      return res.status(404).json({ error: 'Benutzer nicht gefunden' });
    }

    const { preferences, courses, oldPin, newPin } = req.body;

    let pinToSave: string | undefined = undefined;
    if (newPin !== undefined) {
      if (existing.pin) {
        if (!oldPin || !verifyPin(oldPin, existing.pin)) {
          return res.status(400).json({ error: 'Der aktuelle PIN ist falsch.' });
        }
      }
      pinToSave = newPin;
    }

    const updated = await dbSaveUser(username, {
      preferences,
      courses,
      pin: pinToSave
    });

    const token = await generateSessionToken(username);
    res.setHeader('Set-Cookie', sessionCookies(token));
    res.json({ user: sanitizeUser(updated) });
  });

  app.get('/api/session', requireAuth, async (req, res) => {
    const user = await dbGetUser((req as any).authenticatedUser);
    res.json({ user: sanitizeUser(user) });
  });

  app.post('/api/logout', requireAuth, async (req, res) => {
    const tokens = cookieTokens(req);
    if (req.headers.authorization?.startsWith('Bearer ')) {
      tokens.push(req.headers.authorization.substring(7));
    }
    await Promise.all(Array.from(new Set(tokens.filter(Boolean))).map(deleteSessionToken));
    await deleteUserSessions((req as any).authenticatedUser);
    res.setHeader('Set-Cookie', sessionCookies('', 0));
    res.json({ success: true });
  });

  app.get('/api/events', requireAuth, async (req, res) => {
    const username = (req as any).authenticatedUser;
    const user = await dbGetUser(username);
    const visibleCourses = user?.status === 'ADMIN' ? undefined : (user?.courses || []);
    const [events, privateData] = await Promise.all([dbGetEvents(false, visibleCourses), dbGetPrivateCalendar(username)]);
    res.json([...events, ...privateData.events]);
  });

  app.post('/api/events', requireWriteAuth, async (req, res) => {
    const { title, date, endDate, startTime, endTime, courseId, type, description, author, attachments } = req.body;
    const currentUser = (req as any).authenticatedUser;
    if (typeof title !== 'string' || !title.trim() || title.length > 255 || !validCalendarDate(date)
      || (endDate && (!validCalendarDate(endDate) || endDate < date)) || (endDate || date) < eventRetentionCutoff()
      || typeof description !== 'string' && description !== undefined || String(description || '').length > 10000) {
      return res.status(400).json({ error: 'Ungültiger Titel oder ungültiges Datum.' });
    }
    const privateData = await dbGetPrivateCalendar(currentUser);
    const isPrivateType = privateData.categories.some(category => category.id === type);
    const globalCategories = await dbGetCategories();
    if (!isPrivateType && !globalCategories.some(category => category.id === type)) {
      return res.status(400).json({ error: 'Die gewählte Kategorie ist nicht verfügbar.' });
    }

    // FERIEN restriction: Only admins can create FERIEN events
    if (type === 'FERIEN' && !(await dbIsAdmin(currentUser))) {
      return res.status(403).json({ error: 'Nur Admins dürfen Ferientermine erstellen.' });
    }
    const isAdmin = await dbIsAdmin(currentUser);
    const courses = await dbGetCourses();
    if (!isPrivateType && courseId !== 'ALLGEMEIN' && (!courses.some(course => course.id === courseId)
      || (!isAdmin && !(await dbGetUser(currentUser))?.courses.includes(courseId)))) {
      return res.status(403).json({ error: 'Für diesen Kurs besteht keine Berechtigung.' });
    }

    const newEvent = {
      id: uuidv4(),
      title,
      date,
      endDate: endDate || date,
      startTime: startTime || null,
      endTime: endTime || null,
      courseId,
      type,
      description: description || '',
      author: currentUser || author || '',
      attachments: attachments || []
    };
    const saved = isPrivateType ? await dbCreatePrivateEvent(currentUser, newEvent) : await dbCreateEvent(newEvent);
    broadcastCalendarChange(isPrivateType ? currentUser : undefined);
    res.status(201).json(saved);
  });

  app.put('/api/events/:id', requireWriteAuth, async (req, res) => {
    const id = req.params.id as string;
    const currentUser = (req as any).authenticatedUser;
    const update = safeEventUpdate(req.body);
    if ((update.date !== undefined && !validCalendarDate(update.date)) || (update.endDate !== undefined && !validCalendarDate(update.endDate))
      || String(update.description || '').length > 10000 || String(update.title || '').length > 255) {
      return res.status(400).json({ error: 'Ungültige Termindaten.' });
    }
    const privateUpdated = await dbUpdatePrivateEvent(currentUser, id, update).catch(error => {
      if (error.message === 'PRIVATE_CATEGORY_FORBIDDEN') return undefined;
      throw error;
    });
    if (privateUpdated) {
      broadcastCalendarChange(currentUser);
      return res.json(privateUpdated);
    }
    const existing = await dbGetEventById(id);
    if (!existing) return res.status(404).json({ error: 'Event not found' });
    if (String(existing.author || '').toLowerCase() !== currentUser.toLowerCase() && !(await dbIsAdmin(currentUser))) {
      return res.status(403).json({ error: 'Sie dürfen nur eigene Termine bearbeiten.' });
    }
    if (update.type !== undefined) {
      const globalCategories = await dbGetCategories();
      if (!globalCategories.some(category => category.id === update.type)) {
        return res.status(400).json({ error: 'Globale Termine können nur globale Kategorien verwenden.' });
      }
    }
    if (update.courseId !== undefined && update.courseId !== 'ALLGEMEIN') {
      const courses = await dbGetCourses();
      const isAdmin = await dbIsAdmin(currentUser);
      const user = await dbGetUser(currentUser);
      if (!courses.some(course => course.id === update.courseId)
        || (!isAdmin && !(user?.courses || []).includes(update.courseId))) {
        return res.status(403).json({ error: 'Für diesen Kurs besteht keine Berechtigung.' });
      }
    }
    if ((existing.type === 'FERIEN' || update.type === 'FERIEN') && !(await dbIsAdmin(currentUser))) {
      return res.status(403).json({ error: 'Nur Admins dürfen Ferientermine bearbeiten.' });
    }

    const updated = await dbUpdateEvent(id, update);
    broadcastCalendarChange();
    res.json(updated);
  });

  app.delete('/api/events/:id', requireWriteAuth, async (req, res) => {
    const id = req.params.id as string;
    const currentUser = (req as any).authenticatedUser;
    if (await dbDeletePrivateEvent(currentUser, id)) {
      broadcastCalendarChange(currentUser);
      return res.json({ success: true });
    }
    const existing = await dbGetEventById(id);
    if (existing) {
      if (String(existing.author || '').toLowerCase() !== currentUser.toLowerCase() && !(await dbIsAdmin(currentUser))) {
        return res.status(403).json({ error: 'Sie dürfen nur eigene Termine löschen.' });
      }
      if (existing.type === 'FERIEN' && !(await dbIsAdmin(currentUser))) {
        return res.status(403).json({ error: 'Nur Admins dürfen Ferientermine löschen.' });
      }
    }
    await dbDeleteEvent(id, (req as any).authenticatedUser);
    broadcastCalendarChange();
    res.json({ success: true });
  });

  // Admin Management API Routes
  app.get('/api/admins', requireAdmin, async (req, res) => {
    const admins = await dbGetAdmins();
    res.json(admins);
  });

  app.post('/api/admins', requireAuth, async (req, res) => {
    const currentUser = (req as any).authenticatedUser;
    if (!(await dbIsAdmin(currentUser))) {
      return res.status(403).json({ error: 'Nur Admins dürfen neue Admins hinzufügen.' });
    }
    const { username } = req.body;
    if (!username || typeof username !== 'string') {
      return res.status(400).json({ error: 'Benutzername ist erforderlich.' });
    }
    const updated = await dbAddAdmin(username);
    res.json(updated);
  });

  app.delete('/api/admins/:username', requireAuth, async (req, res) => {
    const currentUser = (req as any).authenticatedUser;
    if (!(await dbIsAdmin(currentUser))) {
      return res.status(403).json({ error: 'Nur Admins dürfen Admins entfernen.' });
    }
    const targetUsername = (req.params.username as string || '').toLowerCase();
    const updated = await dbRemoveAdmin(targetUsername);
    res.json(updated);
  });

  app.post('/api/feedback', requireAuth, async (req, res) => {
    const { username, text } = req.body;
    if (typeof text !== 'string' || text.trim().length === 0 || text.length > 5000) {
      return res.status(400).json({ error: 'Das Feedback ist ungültig oder zu lang.' });
    }
    const currentUser = (req as any).authenticatedUser;
    const newFeedback = { id: uuidv4(), username: currentUser || username, text, date: new Date().toISOString() };
    const saved = await dbCreateFeedback(newFeedback);
    res.status(201).json(saved);
  });

  // File Upload API Route (Saves files to disk instead of DB, max 10MB)
  app.post('/api/upload', requireWriteAuth, async (req, res) => {
    try {
      const { filename, mimeType, data, privateAttachment } = req.body;
      if (!data) return res.status(400).json({ error: 'Data is required' });

      const matches = data.match(/^data:(.+);base64,(.+)$/);
      let buffer: Buffer;
      if (matches) {
        buffer = Buffer.from(matches[2], 'base64');
      } else {
        buffer = Buffer.from(data, 'base64');
      }

      const MAX_BYTES = 10 * 1024 * 1024; // 10MB
      if (buffer.length > MAX_BYTES) {
        return res.status(400).json({ error: 'Datei ist zu groß (maximal 10MB erlaubt).' });
      }

      const safeExt = path.extname(typeof filename === 'string' ? filename : '').toLowerCase() || '.bin';
      const allowedExts = new Set(['.bin', '.pdf', '.png', '.jpg', '.jpeg', '.gif', '.webp', '.txt', '.csv', '.doc', '.docx', '.xls', '.xlsx', '.ppt', '.pptx', '.zip']);
      if (!allowedExts.has(safeExt)) return res.status(400).json({ error: 'Dieser Dateityp ist nicht erlaubt.' });
      if (privateAttachment) {
        const PRIVATE_MAX_BYTES = 2 * 1024 * 1024;
        if (buffer.length > PRIVATE_MAX_BYTES) return res.status(400).json({ error: 'Private Anhänge sind auf 2 MB begrenzt.' });
        return res.json({
          id: uuidv4(), filename: filename || 'file', mimeType: mimeType || 'application/octet-stream',
          data: `data:${mimeType || 'application/octet-stream'};base64,${buffer.toString('base64')}`
        });
      }
      const uniqueName = `${uuidv4()}${safeExt}`;
      const filePath = path.join(uploadsDir, uniqueName);
      await fs.promises.writeFile(filePath, buffer);

      res.json({
        id: uuidv4(),
        filename: filename || 'file',
        mimeType: mimeType || 'application/octet-stream',
        url: `/uploads/${uniqueName}`
      });
    } catch (err: any) {
      console.error('Upload error:', err);
      res.status(500).json({ error: 'File upload failed' });
    }
  });

  // Course API Routes
  app.get('/api/courses', async (req, res) => {
    const courses = await dbGetCourses();
    res.json(courses);
  });

  app.post('/api/courses', requireAdmin, async (req, res) => {
    const { id, name, teacher, type } = req.body;
    const course = {
      id: id || name,
      name: name || id,
      teacher: teacher || '',
      type: type || 'GK'
    };
    const saved = await dbSaveCourse(course);
    res.status(201).json(saved);
  });

  app.put('/api/courses/reorder', requireAdmin, async (req, res) => {
    const { courseIds } = req.body;
    if (Array.isArray(courseIds)) {
      await dbReorderCourses(courseIds);
      return res.json({ success: true });
    }
    res.status(400).json({ error: 'courseIds must be an array' });
  });

  app.put('/api/courses/:id', requireAdmin, async (req, res) => {
    const { name, teacher, type } = req.body;
    const courseId = req.params.id as string;
    const course = {
      id: courseId,
      name: name || courseId,
      teacher: teacher || '',
      type: type || 'GK'
    };
    const saved = await dbSaveCourse(course);
    res.json(saved);
  });

  app.delete('/api/courses/:id', requireAdmin, async (req, res) => {
    const id = req.params.id as string;
    await dbDeleteCourse(id);
    res.json({ success: true });
  });

  app.post('/api/courses/reset-defaults', requireAdmin, async (req, res) => {
    const courses = await dbResetCoursesToDefaults();
    res.json(courses);
  });

  // Admin Panel Routes
  app.get('/api/admin/users', requireAdmin, async (req, res) => {
    const users = await dbGetUsers();
    
    // Add isAdmin flag to each user for the frontend
    const usersWithAdminFlag = await Promise.all(users.map(async (u) => ({
      ...u,
      isAdmin: await dbIsAdmin(u.username)
    })));
    
    res.json(usersWithAdminFlag);
  });

  app.post('/api/admin/users', requireAdmin, async (req, res) => {
    const { username, pin } = req.body;
    const uname = (username || '').toLowerCase();
    if (!uname) return res.status(400).json({ error: 'Username erforderlich' });
    
    const existing = await dbGetUser(uname);
    if (existing) return res.status(400).json({ error: 'Username bereits vergeben' });

    await dbSaveUser(uname, {
      courses: [],
      pin: pin || undefined,
      preferences: {
        darkMode: false,
        accentColor: '#e91e63',
        colorKlausur: '#e65176',
        colorHausaufgabe: '#59b3cb',
        colorSonstiges: '#3d60c7',
        colorFerien: '#f1c40f'
      }
    });
    res.status(201).json({ success: true, username: uname });
  });

  app.post('/api/admin/verify-pin', requireAdmin, async (req, res) => {
    const { pin } = req.body;
    const currentUser = (req as any).authenticatedUser;
    const user = await dbGetUser(currentUser);
    if (!user) return res.status(404).json({ error: 'Benutzer nicht gefunden' });
    if (!user.pin) return res.json({ success: true, message: 'Keine PIN gesetzt' });
    if (!pin || !verifyPin(pin, user.pin)) {
      return res.status(401).json({ error: 'Falsche Admin-PIN' });
    }
    return res.json({ success: true });
  });

  app.delete('/api/admin/users/:username', requireAdmin, async (req, res) => {
    const target = (req.params.username as string || '').toLowerCase();
    if (await dbIsAdmin(target)) return res.status(403).json({ error: 'Admins können sich gegenseitig nicht bearbeiten oder löschen.' });
    
    await dbDeleteUser(target);
    res.json({ success: true });
  });

  app.put('/api/admin/users/:username/status', requireAdmin, async (req, res) => {
    const target = (req.params.username as string || '').toLowerCase();
    const { status } = req.body;
    if (await dbIsAdmin(target)) {
      return res.status(403).json({ error: 'Admins können sich gegenseitig nicht bearbeiten oder sperren.' });
    }
    if (status === 'ADMIN') {
      return res.status(400).json({ error: 'Admin-Rechte können nur direkt in der Datenbank vergeben werden.' });
    }
    await dbSaveUser(target, { status });
    res.json({ success: true, status });
  });

  app.put('/api/admin/users/:username/reset-pin', requireAdmin, async (req, res) => {
    const target = (req.params.username as string || '').toLowerCase();
    if (await dbIsAdmin(target)) return res.status(403).json({ error: 'Admin-PIN kann nicht von anderen Admins zurückgesetzt werden.' });
    await dbSaveUser(target, { pin: null });
    res.json({ success: true });
  });

  app.put('/api/admin/users/:username/set-pin', requireAdmin, async (req, res) => {
    const target = (req.params.username as string || '').toLowerCase();
    const { pin } = req.body;
    if (await dbIsAdmin(target)) return res.status(403).json({ error: 'Admin-PIN kann nicht von anderen Admins geändert werden.' });
    await dbSaveUser(target, { pin: pin || null });
    res.json({ success: true });
  });

  // VP-Only User Admin Routes
  app.get('/api/admin/vp-users', requireAdmin, async (req, res) => {
    try {
      const vpUsers = await dbGetVpOnlyUsers();
      res.json(vpUsers);
    } catch (err: any) {
      res.status(500).json({ error: err.message || 'Fehler beim Laden der VP-Nutzer' });
    }
  });

  app.post('/api/admin/vp-users', requireAdmin, async (req, res) => {
    const { username, pin, className } = req.body;
    const currentUser = (req as any).authenticatedUser;
    try {
      const created = await dbCreateVpOnlyUser(username, pin, className, currentUser);
      res.status(201).json(created);
    } catch (err: any) {
      res.status(400).json({ error: err.message || 'Fehler beim Erstellen des VP-Nutzers' });
    }
  });

  app.put('/api/admin/vp-users/:username/status', requireAdmin, async (req, res) => {
    const target = (req.params.username as string || '').toLowerCase();
    const { active } = req.body;
    try {
      await dbSetVpOnlyUserStatus(target, Boolean(active));
      res.json({ success: true, active: Boolean(active) });
    } catch (err: any) {
      res.status(400).json({ error: err.message || 'Fehler beim Aktualisieren des Status' });
    }
  });

  app.put('/api/admin/vp-users/:username/pin', requireAdmin, async (req, res) => {
    const target = (req.params.username as string || '').toLowerCase();
    const { pin, mustChangePin } = req.body;
    try {
      await dbSetVpOnlyUserPin(target, String(pin), Boolean(mustChangePin));
      res.json({ success: true });
    } catch (err: any) {
      res.status(400).json({ error: err.message || 'Fehler beim Setzen der PIN' });
    }
  });

  app.delete('/api/admin/vp-users/:username', requireAdmin, async (req, res) => {
    const target = (req.params.username as string || '').toLowerCase();
    try {
      await dbDeleteVpOnlyUser(target);
      res.json({ success: true });
    } catch (err: any) {
      res.status(400).json({ error: err.message || 'Fehler beim Löschen des VP-Nutzers' });
    }
  });

  app.get('/api/categories', requireAuth, async (req, res) => {
    const username = (req as any).authenticatedUser;
    const [categories, privateData] = await Promise.all([dbGetCategories(), dbGetPrivateCalendar(username)]);
    res.json([...categories, ...privateData.categories]);
  });

  app.post('/api/private-categories', requireWriteAuth, async (req, res) => {
    const username = (req as any).authenticatedUser;
    const name = String(req.body.name || '').trim();
    const color = String(req.body.color || '').trim();
    if (!name || name.length > 64 || !/^#[0-9a-fA-F]{6}$/.test(color)) {
      return res.status(400).json({ error: 'Name oder Farbe ist ungültig.' });
    }
    try {
      const category = await dbCreatePrivateCategory(username, { id: `PRIVATE_${uuidv4()}`, name, color });
      broadcastCalendarChange(username);
      res.status(201).json(category);
    } catch (error: any) {
      if (error.message === 'CATEGORY_LIMIT') return res.status(409).json({ error: 'Maximal fünf private Kategorien sind erlaubt.' });
      if (error.message === 'CATEGORY_DUPLICATE') return res.status(409).json({ error: 'Diese Kategorie existiert bereits.' });
      throw error;
    }
  });

  app.delete('/api/private-categories/:id', requireWriteAuth, async (req, res) => {
    const deleted = await dbDeletePrivateCategory((req as any).authenticatedUser, req.params.id as string);
    if (!deleted) return res.status(404).json({ error: 'Private Kategorie nicht gefunden.' });
    broadcastCalendarChange((req as any).authenticatedUser);
    res.json({ success: true });
  });

  app.post('/api/categories', requireAdmin, async (req, res) => {
    const saved = await dbSaveCategory(req.body);
    broadcastCalendarChange();
    res.json(saved);
  });

  app.put('/api/categories/:id', requireAdmin, async (req, res) => {
    const id = req.params.id as string;
    const saved = await dbSaveCategory({ ...req.body, id });
    broadcastCalendarChange();
    res.json(saved);
  });

  app.delete('/api/categories/:id', requireAdmin, async (req, res) => {
    const id = req.params.id as string;
    await dbDeleteCategory(id);
    broadcastCalendarChange();
    res.json({ success: true });
  });

  app.put('/api/categories/reorder', requireAdmin, async (req, res) => {
    const { categoryIds } = req.body;
    if (Array.isArray(categoryIds)) {
      await dbReorderCategories(categoryIds);
      broadcastCalendarChange();
      return res.json({ success: true });
    }
    res.status(400).json({ error: 'Invalid payload' });
  });

  if (process.env.NODE_ENV !== 'production') {
    const vite = await createViteServer({
      server: { middlewareMode: true },
      appType: 'spa',
    });
    app.use(vite.middlewares);
  } else {
    const distPath = path.join(process.cwd(), 'dist');
    app.use(express.static(distPath, { index: false }));
    const indexHtmlPath = path.join(distPath, 'index.html');
    const indexHtmlTemplate = fs.readFileSync(indexHtmlPath, 'utf-8');
    app.get('*', (req, res) => {
      const publicHostRaw = (process.env.HOST || '127.0.0.1').trim();
      const publicHost = (publicHostRaw === '0.0.0.0' || publicHostRaw === '::') ? '127.0.0.1' : publicHostRaw;
      const calendarPort = (process.env.CAL11_PORT || process.env.PORT || '3000').trim();
      const vpPort = (process.env.VP_PORT || '8000').trim();
      const calendarFallback = `http://${publicHost}:${calendarPort}`;
      const vpFallback = `http://${publicHost}:${vpPort}`;
      const resolveUrl = (preferred: string | undefined, fallback: string): string => {
        const normalized = (preferred || '').trim();
        if (!normalized) return fallback;
        return normalized;
      };
      const runtimeConfig = {
        kalenderUrl: resolveUrl(process.env.CALENDAR_PUBLIC_URL, calendarFallback),
        vertretungsplanUrl: resolveUrl(process.env.VERTRETUNGSPLAN_PUBLIC_URL, vpFallback),
      };
      const runtimeConfigScript = `<script>window.__CAL11_RUNTIME_CONFIG__=${JSON.stringify(runtimeConfig)};</script>`;
      const html = indexHtmlTemplate.includes('</head>')
        ? indexHtmlTemplate.replace('</head>', `${runtimeConfigScript}</head>`)
        : `${runtimeConfigScript}${indexHtmlTemplate}`;
      res.setHeader('Content-Type', 'text/html; charset=utf-8');
      res.send(html);
    });
  }

  app.listen(PORT, HOST, () => {
    console.log(`Server running on ${HOST}:${PORT}`);
  });
}

startServer();
  const SESSION_COOKIE = 'cal11_session';
  const cookieSecure = (process.env.COOKIE_SECURE || 'true').toLowerCase() === 'true';
  function resolveCookieDomain(): string {
    const configured = (process.env.COOKIE_DOMAIN || '').trim().replace(/^\./, '').toLowerCase();
    const hosts = ['CALENDAR_PUBLIC_URL', 'VERTRETUNGSPLAN_PUBLIC_URL'].map(name => {
      try {
        return new URL(process.env[name] || '').hostname.toLowerCase();
      } catch {
        return '';
      }
    });
    if (configured) {
      if (hosts.some(host => host && host !== configured && !host.endsWith(`.${configured}`))) {
        throw new Error('COOKIE_DOMAIN passt nicht zu den öffentlichen Kalender-/VP-URLs.');
      }
      return configured;
    }
    const [calendarHost, vpHost] = hosts;
    if (calendarHost && vpHost && calendarHost !== vpHost) {
      if (vpHost.endsWith(`.${calendarHost}`)) return calendarHost;
      if (calendarHost.endsWith(`.${vpHost}`)) return vpHost;
    }
    return '';
  }
  const cookieDomain = resolveCookieDomain();
  function cookieTokens(req: express.Request): string[] {
    const raw = req.headers.cookie || '';
    const tokens: string[] = [];
    for (const part of raw.split(';')) {
      const [name, ...value] = part.trim().split('=');
      if (name === SESSION_COOKIE) {
        const token = decodeURIComponent(value.join('='));
        if (token && !tokens.includes(token)) tokens.push(token);
      }
    }
    return tokens;
  }
  function sessionCookie(token: string, maxAgeSeconds = 14 * 86400, domain = cookieDomain): string {
    return `${SESSION_COOKIE}=${encodeURIComponent(token)}; Max-Age=${maxAgeSeconds}; Path=/; HttpOnly; SameSite=Lax${cookieSecure ? '; Secure' : ''}${domain ? `; Domain=${domain}` : ''}`;
  }
  function sessionCookies(token: string, maxAgeSeconds = 14 * 86400): string[] {
    const shared = sessionCookie(token, maxAgeSeconds);
    return cookieDomain ? [sessionCookie('', 0, ''), shared] : [shared];
  }
