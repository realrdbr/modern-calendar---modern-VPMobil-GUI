import mysql from 'mysql2/promise';
import dotenv from 'dotenv';
import crypto from 'crypto';
import fs from 'fs';
import { Course, COURSES as DEFAULT_COURSES } from '../src/types';

dotenv.config();

let pool: mysql.Pool | null = null;
let isConnected = false;

function hashPinLegacy(pin: string): string {
  return crypto.createHash('sha256').update(`${pin}_cal11_salt_2026`).digest('hex');
}

function safeEqualText(a: string, b: string): boolean {
  const aBuffer = Buffer.from(a, 'utf8');
  const bBuffer = Buffer.from(b, 'utf8');
  if (aBuffer.length !== bBuffer.length) {
    return false;
  }
  return crypto.timingSafeEqual(aBuffer, bBuffer);
}

// Hash PINs with scrypt and random salt.
export function hashPin(pin: string): string {
  if (!pin) return '';
  if (pin.startsWith('scrypt$')) return pin;
  if (pin.length === 64 && /^[0-9a-f]{64}$/i.test(pin)) return pin; // Legacy SHA-256 hash in DB
  const salt = crypto.randomBytes(16);
  const derived = crypto.scryptSync(pin, salt, 64);
  return `scrypt$${salt.toString('hex')}$${derived.toString('hex')}`;
}

export function verifyPin(inputPin: string, storedPin: string | undefined | null): boolean {
  if (!storedPin) return true; // No PIN required
  if (storedPin === inputPin) return true; // Legacy plaintext PIN match

  if (storedPin.startsWith('scrypt$')) {
    const parts = storedPin.split('$');
    if (parts.length !== 3) return false;
    const saltHex = parts[1];
    const hashHex = parts[2];
    if (!saltHex || !hashHex) return false;
    const salt = Buffer.from(saltHex, 'hex');
    const expected = Buffer.from(hashHex, 'hex');
    const actual = crypto.scryptSync(inputPin, salt, expected.length);
    return expected.length === actual.length && crypto.timingSafeEqual(expected, actual);
  }

  if (storedPin.length === 64 && /^[0-9a-f]{64}$/i.test(storedPin)) {
    return safeEqualText(hashPinLegacy(inputPin), storedPin);
  }

  return false;
}

// Active dynamic session tokens: token -> { username: string, expiresAt: number }
const activeSessions = new Map<string, { username: string; expiresAt: number }>();

export function generateSessionToken(username: string): string {
  const token = crypto.randomBytes(32).toString('hex');
  const expiresAt = Date.now() + 30 * 24 * 60 * 60 * 1000; // 30 Tage Gültigkeit
  activeSessions.set(token, { username: username.toLowerCase(), expiresAt });
  return token;
}

export function getSessionUsername(sessionToken: string): string | null {
  if (!sessionToken) return null;
  const session = activeSessions.get(sessionToken);
  if (!session) return null;
  if (Date.now() > session.expiresAt) {
    activeSessions.delete(sessionToken);
    return null;
  }
  return session.username;
}

export function verifySessionToken(username: string, sessionToken: string): boolean {
  const sessionUser = getSessionUsername(sessionToken);
  if (!sessionUser) return false;
  return sessionUser === username.toLowerCase();
}

export const DEFAULT_PREFERENCES = {
  darkMode: false,
  accentColor: '#e91e63',
  colorKlausur: '#e65176',
  colorHausaufgabe: '#59b3cb',
  colorSonstiges: '#3d60c7',
  colorFerien: '#f1c40f'
};

// In-Memory Fallback Store
const memoryStore: {
  users: Record<string, any>;
  events: any[];
  feedbacks: any[];
  courses: Course[];
  admins: string[];
  categories: any[];
} = {
  users: {
    'gustavd': {
      courses: [],
      pin: undefined,
      preferences: { ...DEFAULT_PREFERENCES },
      status: 'ADMIN'
    },
    'sophiam': {
      courses: [],
      pin: undefined,
      preferences: { ...DEFAULT_PREFERENCES },
      status: 'ACTIVE'
    }
  },
  events: [],
  feedbacks: [],
  courses: [...DEFAULT_COURSES],
  admins: ['gustavd'],
  categories: [
    { id: 'KLAUSUR', name: 'Klausur', color: '#e65176', sort_order: 0 },
    { id: 'HAUSAUFGABE', name: 'Hausaufgabe', color: '#59b3cb', sort_order: 1 },
    { id: 'SONSTIGES', name: 'Sonstiges', color: '#3d60c7', sort_order: 2 },
    { id: 'FERIEN', name: 'Ferien', color: '#f1c40f', sort_order: 3 }
  ]
};

async function sleep(ms: number) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

export async function initDatabase() {
  const dbHost = process.env.DB_HOST;
  const dbUser = process.env.DB_USER;
  const dbPassword = process.env.DB_PASSWORD;
  const dbName = process.env.DB_NAME;
  const dbPort = parseInt(process.env.DB_PORT || '3306', 10);

  if (!dbHost || !dbUser) {
    console.log('[Database] Keine DB-Umgebungsvariablen gefunden. Nutze In-Memory Speicher.');
    return;
  }

  // Wenn die Docker-interne MariaDB-Adresse konfiguriert ist, aber der Prozess
  // außerhalb eines Containers läuft, ist der Hostname "mariadb" nicht erreichbar.
  // In diesem Fall In-Memory-Speicher nutzen (wie beim Python-VP-Server).
  const runningInContainer = fs.existsSync('/.dockerenv');
  const appDatabaseUrl = (process.env.APP_DATABASE_URL || '').toLowerCase();
  const usesInternalMariadb =
    appDatabaseUrl.includes('@mariadb') || dbHost.toLowerCase() === 'mariadb';
  if (usesInternalMariadb && !runningInContainer) {
    console.log(
      '[Database] Docker-interne MariaDB-Konfiguration außerhalb des Containers erkannt. ' +
        'Nutze In-Memory Speicher für lokales Testing.'
    );
    return;
  }

  const maxRetries = 15;
  for (let attempt = 1; attempt <= maxRetries; attempt++) {
    try {
      console.log(`[Database] Verbindungsversuch ${attempt}/${maxRetries} zu MariaDB (${dbHost}:${dbPort}/${dbName})...`);
      
      pool = mysql.createPool({
        host: dbHost,
        port: dbPort,
        user: dbUser,
        password: dbPassword,
        database: dbName,
        waitForConnections: true,
        connectionLimit: 10,
        queueLimit: 0,
        charset: 'utf8mb4'
      });

      const conn = await pool.getConnection();
      console.log(`[Database] ERFOLG! Verbunden mit MariaDB (${dbHost}:${dbPort}/${dbName})`);

      // Create users table with default JSON values
      await conn.query(`
        CREATE TABLE IF NOT EXISTS users (
          username VARCHAR(64) PRIMARY KEY,
          courses LONGTEXT NOT NULL,
          pin VARCHAR(255) DEFAULT NULL,
          preferences LONGTEXT NOT NULL,
          status VARCHAR(20) DEFAULT 'ACTIVE',
          updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
      `);

      // Ensure pin column in existing tables is widened to VARCHAR(255)
      try {
        await conn.query('ALTER TABLE users MODIFY COLUMN pin VARCHAR(255) DEFAULT NULL;');
      } catch (e) {
        // Ignored if column modification is not needed
      }

      // Ensure status column exists in existing tables
      try {
        await conn.query("ALTER TABLE users ADD COLUMN status VARCHAR(20) DEFAULT 'ACTIVE';");
      } catch (e) {}

      // Create categories table
      await conn.query(`
        CREATE TABLE IF NOT EXISTS event_categories (
          id VARCHAR(64) PRIMARY KEY,
          name VARCHAR(64) NOT NULL,
          color VARCHAR(16) NOT NULL,
          sort_order INT NOT NULL DEFAULT 0
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
      `);

      // Create events table
      await conn.query(`
        CREATE TABLE IF NOT EXISTS events (
          id VARCHAR(64) PRIMARY KEY,
          title VARCHAR(255) NOT NULL,
          date VARCHAR(32) NOT NULL,
          end_date VARCHAR(32),
          start_time VARCHAR(16) DEFAULT NULL,
          end_time VARCHAR(16) DEFAULT NULL,
          course_id VARCHAR(64) NOT NULL,
          type VARCHAR(32) NOT NULL,
          description LONGTEXT,
          author VARCHAR(64),
          attachments LONGTEXT,
          deleted_at DATETIME NULL,
          created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
      `);

      try {
        await conn.query('ALTER TABLE events ADD COLUMN start_time VARCHAR(16) DEFAULT NULL;');
      } catch (e) {}
      try {
        await conn.query('ALTER TABLE events ADD COLUMN end_time VARCHAR(16) DEFAULT NULL;');
      } catch (e) {}

      // Create courses table for dynamic courses & custom ordering (utf8mb4_bin for case-sensitive DE1 vs de1)
      await conn.query(`
        CREATE TABLE IF NOT EXISTS courses (
          id VARCHAR(64) CHARACTER SET utf8mb4 COLLATE utf8mb4_bin PRIMARY KEY,
          name VARCHAR(64) CHARACTER SET utf8mb4 COLLATE utf8mb4_bin NOT NULL,
          teacher VARCHAR(64) NOT NULL,
          type VARCHAR(16) NOT NULL,
          sort_order INT NOT NULL DEFAULT 0
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
      `);

      try {
        await conn.query('ALTER TABLE courses MODIFY COLUMN id VARCHAR(64) CHARACTER SET utf8mb4 COLLATE utf8mb4_bin NOT NULL;');
        await conn.query('ALTER TABLE courses MODIFY COLUMN name VARCHAR(64) CHARACTER SET utf8mb4 COLLATE utf8mb4_bin NOT NULL;');
      } catch (e) {}

      // Create feedbacks table
      await conn.query(`
        CREATE TABLE IF NOT EXISTS feedbacks (
          id VARCHAR(64) PRIMARY KEY,
          username VARCHAR(64),
          text LONGTEXT,
          date VARCHAR(64)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
      `);

      // Create admins table
      await conn.query(`
        CREATE TABLE IF NOT EXISTS admins (
          username VARCHAR(64) PRIMARY KEY,
          created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
      `);
      await conn.query(`INSERT IGNORE INTO admins (username) VALUES ('gustavd');`);

      // Seed default accounts if users table is empty
      const [rows]: any = await conn.query('SELECT COUNT(*) as count FROM users');
      if (rows[0].count === 0) {
        console.log('[Database] Initialisiere Standard-Benutzerkonten (gustavd, sophiam)...');
        for (const [uname, udata] of Object.entries(memoryStore.users)) {
          await conn.query(
            'INSERT INTO users (username, courses, pin, preferences, status) VALUES (?, ?, ?, ?, ?)',
            [uname, JSON.stringify(udata.courses), udata.pin ? hashPin(udata.pin) : null, JSON.stringify(udata.preferences), udata.status || 'ACTIVE']
          );
        }
      } else {
        // Ensure gustavd has status ADMIN in database
        try {
          await conn.query("UPDATE users SET status = 'ADMIN' WHERE username = 'gustavd' AND (status IS NULL OR status = 'ACTIVE');");
        } catch (e) {}
      }

      // Seed default courses if missing in the MariaDB
      for (let i = 0; i < DEFAULT_COURSES.length; i++) {
        const c = DEFAULT_COURSES[i];
        await conn.query(
          'INSERT IGNORE INTO courses (id, name, teacher, type, sort_order) VALUES (?, ?, ?, ?, ?)',
          [c.id, c.name, c.teacher, c.type, i]
        );
      }

      // Seed default categories
      const [catRows]: any = await conn.query('SELECT COUNT(*) as count FROM event_categories');
      if (catRows[0].count === 0) {
        console.log('[Database] Initialisiere Standard-Kategorien...');
        const defCats = [
          { id: 'KLAUSUR', name: 'Klausur', color: '#e65176' },
          { id: 'HAUSAUFGABE', name: 'Hausaufgabe', color: '#59b3cb' },
          { id: 'SONSTIGES', name: 'Sonstiges', color: '#3d60c7' },
          { id: 'FERIEN', name: 'Ferien', color: '#f1c40f' }
        ];
        for (let i = 0; i < defCats.length; i++) {
          const c = defCats[i];
          await conn.query(
            'INSERT INTO event_categories (id, name, color, sort_order) VALUES (?, ?, ?, ?)',
            [c.id, c.name, c.color, i]
          );
        }
      }

      // Cleanup legacy course IDs in users table (e.g. 'Chor' -> 'CHO')
      try {
        await conn.query(`UPDATE users SET courses = REPLACE(courses, '"Chor"', '"CHO"') WHERE courses LIKE '%"Chor"%';`);
      } catch (e) {}

      conn.release();
      isConnected = true;
      return;
    } catch (err: any) {
      console.warn(`[Database] Versuch ${attempt} fehlgeschlagen (${err.message}). Warte 2 Sekunden...`);
      if (attempt < maxRetries) {
        await sleep(2000);
      }
    }
  }

  console.error('[Database] Konnte nach allen Versuchen keine Verbindung zu MariaDB herstellen.');
  isConnected = false;
}

export function isDbConnected(): boolean {
  return isConnected && pool !== null;
}

// User Operations
export async function dbGetUser(username: string) {
  const uname = username.toLowerCase();
  if (isConnected && pool) {
    const [rows]: any = await pool.query('SELECT * FROM users WHERE username = ?', [uname]);
    if (rows.length === 0) return null;
    const row = rows[0];
    const rawCourses = typeof row.courses === 'string' ? JSON.parse(row.courses || '[]') : (row.courses || []);
    const normalizedCourses = Array.from(new Set(rawCourses.map((c: string) => c === 'Chor' ? 'CHO' : c)));
    return {
      username: row.username,
      courses: normalizedCourses,
      pin: row.pin || undefined,
      status: row.status || 'ACTIVE',
      preferences: typeof row.preferences === 'string' ? { ...DEFAULT_PREFERENCES, ...JSON.parse(row.preferences || '{}') } : { ...DEFAULT_PREFERENCES, ...(row.preferences || {}) }
    };
  }
  return null;
}

export async function dbGetUsers() {
  if (isConnected && pool) {
    const [rows]: any = await pool.query('SELECT username, status FROM users ORDER BY username ASC');
    return rows.map((r: any) => ({ username: r.username, status: r.status || 'ACTIVE' }));
  }
  return Object.keys(memoryStore.users).sort().map(uname => ({
    username: uname,
    status: memoryStore.users[uname].status || 'ACTIVE'
  }));
}

export async function dbSaveUser(username: string, data: { courses?: string[]; pin?: string | null; preferences?: any; status?: string }) {
  const uname = username.toLowerCase();
  const existing = await dbGetUser(uname);

  const rawCourses = data.courses !== undefined ? data.courses : (existing ? existing.courses : []);
  const courses = Array.from(new Set(rawCourses.map((c: string) => c === 'Chor' ? 'CHO' : c)));
  
  let hashedPin: string | null = null;
  if (data.pin === null) {
    hashedPin = null;
  } else if (data.pin !== undefined) {
    hashedPin = data.pin ? hashPin(data.pin) : null;
  } else if (existing && existing.pin) {
    hashedPin = existing.pin;
  }

  const preferences = data.preferences !== undefined 
    ? { ...DEFAULT_PREFERENCES, ...data.preferences } 
    : (existing ? existing.preferences : { ...DEFAULT_PREFERENCES });

  const status = data.status !== undefined ? data.status : (existing ? existing.status : 'ACTIVE');

  if (isConnected && pool) {
    await pool.query(
      `INSERT INTO users (username, courses, pin, preferences, status) 
       VALUES (?, ?, ?, ?, ?) 
       ON DUPLICATE KEY UPDATE courses = VALUES(courses), pin = VALUES(pin), preferences = VALUES(preferences), status = VALUES(status)`,
      [uname, JSON.stringify(courses), hashedPin, JSON.stringify(preferences), status]
    );
    return { username: uname, courses, pin: hashedPin || undefined, preferences, status };
  }

  memoryStore.users[uname] = {
    courses,
    pin: hashedPin || undefined,
    preferences,
    status
  };
  return { username: uname, ...memoryStore.users[uname] };
}

export async function dbDeleteUser(username: string) {
  const uname = username.toLowerCase();
  if (isConnected && pool) {
    await pool.query('DELETE FROM users WHERE username = ?', [uname]);
  } else {
    delete memoryStore.users[uname];
  }
}

// Course Operations
export async function dbGetCourses(): Promise<Course[]> {
  if (isConnected && pool) {
    const [rows]: any = await pool.query('SELECT id, name, teacher, type FROM courses ORDER BY sort_order ASC, name ASC');
    return rows.map((r: any) => ({
      id: r.id,
      name: r.name,
      teacher: r.teacher,
      type: r.type
    }));
  }
  return memoryStore.courses;
}

export async function dbSaveCourse(course: Course) {
  if (isConnected && pool) {
    const [existing]: any = await pool.query('SELECT COUNT(*) as count FROM courses');
    const nextOrder = existing[0]?.count || 0;
    await pool.query(
      `INSERT INTO courses (id, name, teacher, type, sort_order)
       VALUES (?, ?, ?, ?, ?)
       ON DUPLICATE KEY UPDATE name = VALUES(name), teacher = VALUES(teacher), type = VALUES(type)`,
      [course.id, course.name, course.teacher, course.type, nextOrder]
    );
    return course;
  }
  const idx = memoryStore.courses.findIndex(c => c.id === course.id);
  if (idx !== -1) {
    memoryStore.courses[idx] = course;
  } else {
    memoryStore.courses.push(course);
  }
  return course;
}

export async function dbDeleteCourse(id: string) {
  if (isConnected && pool) {
    await pool.query('DELETE FROM courses WHERE id = ?', [id]);
    return true;
  }
  memoryStore.courses = memoryStore.courses.filter(c => c.id !== id);
  return true;
}

export async function dbReorderCourses(courseIds: string[]) {
  if (isConnected && pool) {
    for (let index = 0; index < courseIds.length; index++) {
      await pool.query('UPDATE courses SET sort_order = ? WHERE id = ?', [index, courseIds[index]]);
    }
    return true;
  }
  const sorted: Course[] = [];
  courseIds.forEach(id => {
    const found = memoryStore.courses.find(c => c.id === id);
    if (found) sorted.push(found);
  });
  memoryStore.courses.filter(c => !courseIds.includes(c.id)).forEach(c => sorted.push(c));
  memoryStore.courses = sorted;
  return true;
}

export async function dbResetCoursesToDefaults(): Promise<Course[]> {
  if (isConnected && pool) {
    await pool.query('DELETE FROM courses');
    for (let i = 0; i < DEFAULT_COURSES.length; i++) {
      const c = DEFAULT_COURSES[i];
      await pool.query(
        'INSERT INTO courses (id, name, teacher, type, sort_order) VALUES (?, ?, ?, ?, ?)',
        [c.id, c.name, c.teacher, c.type, i]
      );
    }
    return dbGetCourses();
  }
  memoryStore.courses = [...DEFAULT_COURSES];
  return memoryStore.courses;
}

// Event Operations (with Soft Delete)
export async function dbGetEvents(includeDeleted = false) {
  if (isConnected && pool) {
    const query = includeDeleted 
      ? 'SELECT * FROM events ORDER BY date ASC, start_time ASC' 
      : 'SELECT * FROM events WHERE deleted_at IS NULL ORDER BY date ASC, start_time ASC';
    const [rows]: any = await pool.query(query);
    return rows.map((r: any) => ({
      id: r.id,
      title: r.title,
      date: r.date,
      endDate: r.end_date || undefined,
      startTime: r.start_time || undefined,
      endTime: r.end_time || undefined,
      courseId: r.course_id,
      type: r.type,
      description: r.description || '',
      author: r.author || '',
      attachments: typeof r.attachments === 'string' ? JSON.parse(r.attachments || '[]') : (r.attachments || []),
      deletedAt: r.deleted_at || undefined
    }));
  }
  return memoryStore.events
    .filter(e => includeDeleted || !e.deletedAt)
    .map(e => ({ ...e }));
}

export async function dbGetEventById(id: string) {
  if (isConnected && pool) {
    const [rows]: any = await pool.query('SELECT * FROM events WHERE id = ?', [id]);
    if (rows.length === 0) return null;
    const r = rows[0];
    return {
      id: r.id,
      title: r.title,
      date: r.date,
      endDate: r.end_date || undefined,
      startTime: r.start_time || undefined,
      endTime: r.end_time || undefined,
      courseId: r.course_id,
      type: r.type,
      description: r.description || '',
      author: r.author || '',
      attachments: typeof r.attachments === 'string' ? JSON.parse(r.attachments || '[]') : (r.attachments || []),
      deletedAt: r.deleted_at || undefined
    };
  }
  const found = memoryStore.events.find(e => e.id === id);
  return found ? { ...found } : null;
}

export async function dbCreateEvent(event: any) {
  if (isConnected && pool) {
    await pool.query(
      `INSERT INTO events (id, title, date, end_date, start_time, end_time, course_id, type, description, author, attachments, deleted_at)
       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL)`,
      [
        event.id,
        event.title,
        event.date,
        event.endDate || event.date,
        event.startTime || null,
        event.endTime || null,
        event.courseId,
        event.type,
        event.description || '',
        event.author || '',
        JSON.stringify(event.attachments || [])
      ]
    );
    return event;
  }
  memoryStore.events.push(event);
  return event;
}

export async function dbUpdateEvent(id: string, data: any) {
  if (isConnected && pool) {
    const [existing]: any = await pool.query('SELECT * FROM events WHERE id = ?', [id]);
    if (existing.length === 0) return null;
    const current = existing[0];

    const title = data.title !== undefined ? data.title : current.title;
    const date = data.date !== undefined ? data.date : current.date;
    const endDate = data.endDate !== undefined ? data.endDate : current.end_date;
    const startTime = data.startTime !== undefined ? data.startTime : current.start_time;
    const endTime = data.endTime !== undefined ? data.endTime : current.end_time;
    const courseId = data.courseId !== undefined ? data.courseId : current.course_id;
    const type = data.type !== undefined ? data.type : current.type;
    const description = data.description !== undefined ? data.description : current.description;
    const attachments = data.attachments !== undefined ? JSON.stringify(data.attachments) : current.attachments;

    await pool.query(
      `UPDATE events 
       SET title = ?, date = ?, end_date = ?, start_time = ?, end_time = ?, course_id = ?, type = ?, description = ?, attachments = ? 
       WHERE id = ?`,
      [title, date, endDate, startTime, endTime, courseId, type, description, attachments, id]
    );

    return {
      id,
      title,
      date,
      endDate,
      startTime,
      endTime,
      courseId,
      type,
      description,
      author: current.author,
      attachments: typeof attachments === 'string' ? JSON.parse(attachments) : attachments
    };
  }

  const idx = memoryStore.events.findIndex(e => e.id === id);
  if (idx !== -1) {
    memoryStore.events[idx] = { ...memoryStore.events[idx], ...data };
    return memoryStore.events[idx];
  }
  return null;
}

// Soft Delete
export async function dbDeleteEvent(id: string) {
  if (isConnected && pool) {
    await pool.query('UPDATE events SET deleted_at = NOW() WHERE id = ?', [id]);
    return true;
  }
  const event = memoryStore.events.find(e => e.id === id);
  if (event) {
    event.deletedAt = new Date().toISOString();
  }
  return true;
}

// Restore
export async function dbRestoreEvent(id: string) {
  if (isConnected && pool) {
    await pool.query('UPDATE events SET deleted_at = NULL WHERE id = ?', [id]);
    return true;
  }
  const event = memoryStore.events.find(e => e.id === id);
  if (event) {
    delete event.deletedAt;
  }
  return true;
}

// Feedback Operations
export async function dbCreateFeedback(feedback: any) {
  if (isConnected && pool) {
    await pool.query(
      'INSERT INTO feedbacks (id, username, text, date) VALUES (?, ?, ?, ?)',
      [feedback.id, feedback.username, feedback.text, feedback.date]
    );
    return feedback;
  }
  memoryStore.feedbacks.push(feedback);
  return feedback;
}

// Admin Operations
export async function dbGetAdmins(): Promise<string[]> {
  const set = new Set<string>();
  if (isConnected && pool) {
    try {
      const [userAdmins]: any = await pool.query("SELECT username FROM users WHERE status = 'ADMIN'");
      userAdmins.forEach((r: any) => set.add(r.username.toLowerCase()));
    } catch (e) {}
    try {
      const [tableAdmins]: any = await pool.query("SELECT username FROM admins");
      tableAdmins.forEach((r: any) => set.add(r.username.toLowerCase()));
    } catch (e) {}
    return Array.from(set);
  }
  memoryStore.admins.forEach(a => set.add(a.toLowerCase()));
  Object.keys(memoryStore.users).forEach(u => {
    if (memoryStore.users[u].status === 'ADMIN') set.add(u.toLowerCase());
  });
  return Array.from(set);
}

export async function dbIsAdmin(username: string): Promise<boolean> {
  if (!username) return false;
  const uname = username.toLowerCase().trim();
  if (isConnected && pool) {
    try {
      const [userRows]: any = await pool.query("SELECT status FROM users WHERE username = ?", [uname]);
      if (userRows.length > 0 && userRows[0].status === 'ADMIN') {
        return true;
      }
    } catch (e) {}
    try {
      const [adminRows]: any = await pool.query("SELECT username FROM admins WHERE username = ?", [uname]);
      if (adminRows.length > 0) {
        return true;
      }
    } catch (e) {}
    return false;
  }
  const memUser = memoryStore.users[uname];
  if (memUser && memUser.status === 'ADMIN') return true;
  return memoryStore.admins.map(a => a.toLowerCase()).includes(uname);
}

export async function dbAddAdmin(username: string): Promise<string[]> {
  if (!username) return dbGetAdmins();
  const uname = username.toLowerCase().trim();
  if (isConnected && pool) {
    await pool.query('INSERT IGNORE INTO admins (username) VALUES (?)', [uname]);
  } else {
    if (!memoryStore.admins.includes(uname)) {
      memoryStore.admins.push(uname);
    }
  }
  return dbGetAdmins();
}

export async function dbRemoveAdmin(username: string): Promise<string[]> {
  if (!username) return dbGetAdmins();
  const uname = username.toLowerCase().trim();
  if (uname === 'gustavd') {
    return dbGetAdmins(); // Super admin cannot be removed
  }
  if (isConnected && pool) {
    await pool.query('DELETE FROM admins WHERE username = ?', [uname]);
  } else {
    memoryStore.admins = memoryStore.admins.filter(a => a !== uname);
  }
  return dbGetAdmins();
}

// Category Operations
export async function dbGetCategories() {
  if (isConnected && pool) {
    const [rows]: any = await pool.query('SELECT * FROM event_categories ORDER BY sort_order ASC');
    return rows.map((r: any) => ({
      id: r.id,
      name: r.name,
      color: r.color,
      sort_order: r.sort_order
    }));
  }
  return [...memoryStore.categories].sort((a, b) => a.sort_order - b.sort_order);
}

export async function dbSaveCategory(category: { id: string; name: string; color: string; sort_order?: number }) {
  if (isConnected && pool) {
    const sortOrder = category.sort_order !== undefined ? category.sort_order : 999;
    await pool.query(
      `INSERT INTO event_categories (id, name, color, sort_order) 
       VALUES (?, ?, ?, ?) 
       ON DUPLICATE KEY UPDATE name = VALUES(name), color = VALUES(color), sort_order = VALUES(sort_order)`,
      [category.id, category.name, category.color, sortOrder]
    );
    return category;
  }
  const idx = memoryStore.categories.findIndex(c => c.id === category.id);
  const sortOrder = category.sort_order !== undefined ? category.sort_order : (idx >= 0 ? memoryStore.categories[idx].sort_order : 999);
  const newCat = { ...category, sort_order: sortOrder };
  if (idx >= 0) {
    memoryStore.categories[idx] = newCat;
  } else {
    memoryStore.categories.push(newCat);
  }
  return newCat;
}

export async function dbDeleteCategory(id: string) {
  if (isConnected && pool) {
    await pool.query('DELETE FROM event_categories WHERE id = ?', [id]);
    return true;
  }
  memoryStore.categories = memoryStore.categories.filter(c => c.id !== id);
  return true;
}

export async function dbReorderCategories(categoryIds: string[]) {
  if (isConnected && pool) {
    for (let index = 0; index < categoryIds.length; index++) {
      await pool.query('UPDATE event_categories SET sort_order = ? WHERE id = ?', [index, categoryIds[index]]);
    }
    return true;
  }
  const sorted: any[] = [];
  categoryIds.forEach(id => {
    const found = memoryStore.categories.find(c => c.id === id);
    if (found) sorted.push(found);
  });
  memoryStore.categories.filter(c => !categoryIds.includes(c.id)).forEach(c => sorted.push(c));
  memoryStore.categories = sorted;
  return true;
}
