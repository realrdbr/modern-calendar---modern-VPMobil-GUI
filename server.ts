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
  dbCreateEvent,
  dbUpdateEvent,
  dbDeleteEvent,
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
  dbGetAdmins,
  dbIsAdmin,
  dbAddAdmin,
  dbRemoveAdmin
} from './server/db';

function sanitizeUser(user: any) {
  if (!user) return null;
  const { pin, ...rest } = user;
  return {
    ...rest,
    hasPin: !!pin
  };
}

async function startServer() {
  const app = express();
  const PORT = Number.parseInt(process.env.CAL11_PORT || '3000', 10);
  const requestedHost = process.env.BIND_HOST || process.env.HOST || '127.0.0.1';
  const runningInDocker = fs.existsSync('/.dockerenv');
  const HOST = runningInDocker && (requestedHost === '127.0.0.1' || requestedHost === 'localhost')
    ? '0.0.0.0'
    : requestedHost;

  // Initialize DB or in-memory fallback
  await initDatabase();

  const uploadsDir = path.join(process.cwd(), 'uploads');
  if (!fs.existsSync(uploadsDir)) {
    fs.mkdirSync(uploadsDir, { recursive: true });
  }

  app.use(express.json({ limit: '50mb' }));
  app.use(express.urlencoded({ limit: '50mb', extended: true }));
  app.use('/uploads', express.static(uploadsDir));

  // Require active DB connection for all API routes
  app.use('/api', (req, res, next) => {
    if (req.path === '/health') return next();
    if (!isDbConnected()) {
      return res.status(503).json({ error: 'Keine Verbindung zur Datenbank. Zugriff verweigert.' });
    }
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

    const rateCheck = checkRateLimit(rateLimitKey);
    if (!rateCheck.allowed) {
      return res.status(429).json({ error: `Zu viele fehlerhafte Anmeldeversuche. Bitte warte ${rateCheck.remainingSeconds} Sekunden.` });
    }

    const user = await dbGetUser(uname);
    if (!user) {
      recordFailedAttempt(rateLimitKey);
      return res.status(404).json({ error: 'Benutzer nicht gefunden' });
    }

    if (user.status === 'BLOCKED') {
      return res.status(403).json({ error: 'Dein Konto wurde aufgrund unangemessener Aktivitäten gesperrt.' });
    }

    if (user.pin) {
      if (sessionToken) {
        if (!verifySessionToken(uname, sessionToken)) {
          return res.status(401).json({ error: 'Sitzung abgelaufen oder ungültig. Bitte erneut anmelden.' });
        }
      } else {
        if (!verifyPin(pin, user.pin)) {
          recordFailedAttempt(rateLimitKey);
          return res.status(401).json({ error: 'Falscher PIN' });
        }
        if (typeof user.pin === 'string' && !user.pin.startsWith('scrypt$')) {
          await dbSaveUser(uname, { pin });
        }
      }
    }

    resetFailedAttempt(rateLimitKey);
    const token = generateSessionToken(uname);
    res.json({ user: sanitizeUser(user), sessionToken: token });
  });

  // Authentication middleware
  const requireAuth = async (req: express.Request, res: express.Response, next: express.NextFunction) => {
    const authHeader = req.headers.authorization;
    let token = '';
    if (authHeader && authHeader.startsWith('Bearer ')) {
      token = authHeader.substring(7);
    } else if (req.headers['x-session-token']) {
      token = req.headers['x-session-token'] as string;
    }

    if (!token) {
      return res.status(401).json({ error: 'Nicht authentifiziert (Session-Token fehlt).' });
    }

    const username = getSessionUsername(token);
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

  app.get('/api/users/:username', requireAuth, async (req, res) => {
    const username = (req.params.username as string || '').toLowerCase();
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

    const token = generateSessionToken(username);
    res.json({ user: sanitizeUser(updated), sessionToken: token });
  });

  app.get('/api/events', async (req, res) => {
    const events = await dbGetEvents();
    res.json(events);
  });

  app.post('/api/events', requireWriteAuth, async (req, res) => {
    const { title, date, endDate, startTime, endTime, courseId, type, description, author, attachments } = req.body;
    const currentUser = (req as any).authenticatedUser;

    // FERIEN restriction: Only admins can create FERIEN events
    if (type === 'FERIEN' && !(await dbIsAdmin(currentUser))) {
      return res.status(403).json({ error: 'Nur Admins dürfen Ferientermine erstellen.' });
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
    const saved = await dbCreateEvent(newEvent);
    res.status(201).json(saved);
  });

  app.put('/api/events/:id', requireWriteAuth, async (req, res) => {
    const id = req.params.id as string;
    const existing = await dbGetEventById(id);
    if (!existing) {
      return res.status(404).json({ error: 'Event not found' });
    }

    const currentUser = (req as any).authenticatedUser;
    if ((existing.type === 'FERIEN' || req.body.type === 'FERIEN') && !(await dbIsAdmin(currentUser))) {
      return res.status(403).json({ error: 'Nur Admins dürfen Ferientermine bearbeiten.' });
    }

    const updated = await dbUpdateEvent(id, req.body);
    res.json(updated);
  });

  app.delete('/api/events/:id', requireWriteAuth, async (req, res) => {
    const id = req.params.id as string;
    const existing = await dbGetEventById(id);
    if (existing) {
      const currentUser = (req as any).authenticatedUser;
      if (existing.type === 'FERIEN' && !(await dbIsAdmin(currentUser))) {
        return res.status(403).json({ error: 'Nur Admins dürfen Ferientermine löschen.' });
      }
    }
    await dbDeleteEvent(id);
    res.json({ success: true });
  });

  // Admin Management API Routes
  app.get('/api/admins', async (req, res) => {
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
    if (targetUsername === 'gustavd') {
      return res.status(400).json({ error: 'Der Hauptadmin (gustavd) kann nicht entfernt werden.' });
    }
    const updated = await dbRemoveAdmin(targetUsername);
    res.json(updated);
  });

  app.post('/api/feedback', requireAuth, async (req, res) => {
    const { username, text } = req.body;
    const currentUser = (req as any).authenticatedUser;
    const newFeedback = { id: uuidv4(), username: currentUser || username, text, date: new Date().toISOString() };
    const saved = await dbCreateFeedback(newFeedback);
    res.status(201).json(saved);
  });

  // File Upload API Route (Saves files to disk instead of DB, max 10MB)
  app.post('/api/upload', requireWriteAuth, async (req, res) => {
    try {
      const { filename, mimeType, data } = req.body;
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

      const safeExt = path.extname(filename || '') || '.bin';
      const dangerousExts = ['.exe', '.bat', '.cmd', '.sh', '.php', '.js', '.py', '.html', '.htm', '.vbs', '.ps1', '.cgi', '.pl'];
      if (dangerousExts.includes(safeExt.toLowerCase())) {
        return res.status(400).json({ error: 'Aus Sicherheitsgründen sind ausführbare Dateien nicht erlaubt.' });
      }
      const uniqueName = `${uuidv4()}${safeExt}`;
      const filePath = path.join(process.cwd(), 'uploads', uniqueName);
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

  app.delete('/api/admin/users/:username', requireAdmin, async (req, res) => {
    const target = req.params.username as string;
    if (await dbIsAdmin(target)) return res.status(400).json({ error: 'Admins können nicht gelöscht werden.' });
    
    await dbDeleteUser(target);
    res.json({ success: true });
  });

  app.put('/api/admin/users/:username/status', requireAdmin, async (req, res) => {
    const target = (req.params.username as string || '').toLowerCase();
    const { status } = req.body;
    if (await dbIsAdmin(target)) {
      return res.status(400).json({ error: 'Admins können nur direkt in der Datenbank geändert werden.' });
    }
    if (status === 'ADMIN') {
      return res.status(400).json({ error: 'Admin-Rechte können nur direkt in der Datenbank vergeben werden.' });
    }
    await dbSaveUser(target, { status });
    res.json({ success: true, status });
  });

  app.put('/api/admin/users/:username/reset-pin', requireAdmin, async (req, res) => {
    const target = req.params.username as string;
    if (await dbIsAdmin(target)) return res.status(400).json({ error: 'Admin PIN kann nicht zurückgesetzt werden.' });
    await dbSaveUser(target, { pin: null });
    res.json({ success: true });
  });

  app.get('/api/categories', async (req, res) => {
    const categories = await dbGetCategories();
    res.json(categories);
  });

  app.post('/api/categories', requireAdmin, async (req, res) => {
    const saved = await dbSaveCategory(req.body);
    res.json(saved);
  });

  app.put('/api/categories/:id', requireAdmin, async (req, res) => {
    const id = req.params.id as string;
    const saved = await dbSaveCategory({ ...req.body, id });
    res.json(saved);
  });

  app.delete('/api/categories/:id', requireAdmin, async (req, res) => {
    const id = req.params.id as string;
    await dbDeleteCategory(id);
    res.json({ success: true });
  });

  app.put('/api/categories/reorder', requireAdmin, async (req, res) => {
    const { categoryIds } = req.body;
    if (Array.isArray(categoryIds)) {
      await dbReorderCategories(categoryIds);
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
