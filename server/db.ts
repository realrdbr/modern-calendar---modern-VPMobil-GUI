import mysql from 'mysql2/promise';
import dotenv from 'dotenv';
import crypto from 'crypto';
import { Course, COURSES as DEFAULT_COURSES } from '../src/types';

dotenv.config();

let pool: mysql.Pool | null = null;
let isConnected = false;

type PrivateCalendarData = { categories: any[]; events: any[] };
const privateMemoryStore = new Map<string, PrivateCalendarData>();
const privateMutationQueues = new Map<string, Promise<void>>();

async function withPrivateMutation<T>(username: string, operation: () => Promise<T>): Promise<T> {
  const key = username.toLowerCase();
  const previous = privateMutationQueues.get(key) || Promise.resolve();
  let release!: () => void;
  const current = new Promise<void>(resolve => { release = resolve; });
  const chain = previous.then(() => current);
  privateMutationQueues.set(key, chain);
  await previous;
  try { return await operation(); }
  finally {
    release();
    if (privateMutationQueues.get(key) === chain) privateMutationQueues.delete(key);
  }
}

function privateDataKey(): Buffer {
  const secret = process.env.CALENDAR_PRIVATE_DATA_KEY || process.env.APP_ENCRYPTION_KEY || '';
  if (!secret && process.env.NODE_ENV === 'production') {
    throw new Error('CALENDAR_PRIVATE_DATA_KEY oder APP_ENCRYPTION_KEY muss in Produktion gesetzt sein.');
  }
  return crypto.createHash('sha256').update(secret || 'development-only-private-calendar-key').digest();
}

function fernetEncrypt(plaintext: string): Buffer {
  const rawKey = process.env.APP_ENCRYPTION_KEY || '';
  if (!rawKey) throw new Error('APP_ENCRYPTION_KEY fehlt.');
  const key = Buffer.from(rawKey, 'base64url');
  if (key.length !== 32) throw new Error('APP_ENCRYPTION_KEY ist kein gültiger Fernet-Schlüssel.');
  const signingKey = key.subarray(0, 16);
  const encryptionKey = key.subarray(16);
  const version = Buffer.from([0x80]);
  const timestamp = Buffer.alloc(8);
  timestamp.writeBigUInt64BE(BigInt(Math.floor(Date.now() / 1000)));
  const iv = crypto.randomBytes(16);
  const cipher = crypto.createCipheriv('aes-128-cbc', encryptionKey, iv);
  const ciphertext = Buffer.concat([cipher.update(plaintext, 'utf8'), cipher.final()]);
  const signed = Buffer.concat([version, timestamp, iv, ciphertext]);
  const signature = crypto.createHmac('sha256', signingKey).update(signed).digest();
  return Buffer.from(Buffer.concat([signed, signature]).toString('base64url'), 'utf8');
}

export function encryptPrivateData(username: string, data: PrivateCalendarData) {
  const nonce = crypto.randomBytes(12);
  const cipher = crypto.createCipheriv('aes-256-gcm', privateDataKey(), nonce);
  cipher.setAAD(Buffer.from(username.toLowerCase(), 'utf8'));
  const ciphertext = Buffer.concat([cipher.update(JSON.stringify(data), 'utf8'), cipher.final()]);
  return { nonce, ciphertext, authTag: cipher.getAuthTag() };
}

export function decryptPrivateData(username: string, row: any): PrivateCalendarData {
  const decipher = crypto.createDecipheriv('aes-256-gcm', privateDataKey(), Buffer.from(row.nonce));
  decipher.setAAD(Buffer.from(username.toLowerCase(), 'utf8'));
  decipher.setAuthTag(Buffer.from(row.auth_tag));
  const parsed = JSON.parse(Buffer.concat([decipher.update(Buffer.from(row.ciphertext)), decipher.final()]).toString('utf8'));
  return { categories: Array.isArray(parsed.categories) ? parsed.categories : [], events: Array.isArray(parsed.events) ? parsed.events : [] };
}

function hashPinLegacy(pin: string): string {
  return crypto.createHash('sha256').update(`${pin}_cal11_salt_2026`).digest('hex');
}

function generateInitialCredentials(): { username: string; pin: string } {
  const username = crypto.randomBytes(6).toString('base64url').replace(/[^A-Za-z0-9]/g, '').slice(0, 8).padEnd(8, 'A');
  const pin = crypto.randomInt(0, 10000).toString().padStart(4, '0');
  return { username, pin };
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

const activeSessions = new Map<string, { username: string; expiresAt: number }>();

export async function generateSessionToken(username: string): Promise<string> {
  const token = crypto.randomBytes(32).toString('hex');
  const expiresAt = Date.now() + 30 * 24 * 60 * 60 * 1000; // 30 Tage Gültigkeit
  if (isConnected && pool) {
    await pool.query(
      `INSERT INTO app_sessions (token_hash, username, csrf_token, expires_at, created_at)
       VALUES (?, ?, ?, ?, ?)`,
      [crypto.createHash('sha256').update(token).digest('hex'), username.toLowerCase(), crypto.randomBytes(32).toString('hex'), new Date(expiresAt), new Date()]
    );
  }
  activeSessions.set(token, { username: username.toLowerCase(), expiresAt });
  return token;
}

export async function getSessionUsername(sessionToken: string): Promise<string | null> {
  if (!sessionToken) return null;
  if (isConnected && pool) {
    const hash = crypto.createHash('sha256').update(sessionToken).digest('hex');
    const [rows]: any = await pool.query(
      'SELECT username FROM app_sessions WHERE token_hash = ? AND expires_at > NOW()', [hash]
    );
    if (rows.length) return String(rows[0].username).toLowerCase();
  }
  if (isConnected && pool) return null;
  const session = activeSessions.get(sessionToken);
  if (!session) return null;
  if (Date.now() > session.expiresAt) {
    activeSessions.delete(sessionToken);
    return null;
  }
  return session.username;
}

export async function verifySessionToken(username: string, sessionToken: string): Promise<boolean> {
  const sessionUser = await getSessionUsername(sessionToken);
  if (!sessionUser) return false;
  return sessionUser === username.toLowerCase();
}

export async function deleteSessionToken(sessionToken: string): Promise<void> {
  activeSessions.delete(sessionToken);
  if (isConnected && pool && sessionToken) {
    await pool.query('DELETE FROM app_sessions WHERE token_hash = ?', [crypto.createHash('sha256').update(sessionToken).digest('hex')]);
  }
}

export async function deleteUserSessions(username: string): Promise<void> {
  const normalized = username.toLowerCase();
  for (const [token, session] of activeSessions.entries()) {
    if (session.username === normalized) activeSessions.delete(token);
  }
  if (isConnected && pool) {
    await pool.query('DELETE FROM app_sessions WHERE LOWER(username) = LOWER(?)', [normalized]);
    const [vpOnlySessionTables]: any = await pool.query("SHOW TABLES LIKE 'vp_only_sessions'");
    if (vpOnlySessionTables.length) {
      await pool.query('DELETE FROM vp_only_sessions WHERE LOWER(username) = LOWER(?)', [normalized]);
    }
  }
}

export async function dbRecordCalendarLoginAttempt(username: string, ipAddress: string, successful: boolean): Promise<void> {
  if (isConnected && pool) {
    await pool.query(
      'INSERT INTO calendar_login_attempts(username, ip_address, attempted_at, successful) VALUES (?, ?, ?, ?)',
      [username, ipAddress, new Date().toISOString(), successful ? 1 : 0]
    );
  }
}

export const DEFAULT_PREFERENCES = {
  darkMode: false,
  themeMode: 'system',
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
  users: {},
  events: [],
  feedbacks: [],
  courses: [...DEFAULT_COURSES],
  admins: [],
  categories: [
    { id: 'KLAUSUR', name: 'Klausur', color: '#e65176', sort_order: 0 },
    { id: 'HAUSAUFGABE', name: 'Hausaufgabe', color: '#59b3cb', sort_order: 1 },
    { id: 'SONSTIGES', name: 'Sonstiges', color: '#3d60c7', sort_order: 2 },
    { id: 'FERIEN', name: 'Ferien', color: '#f1c40f', sort_order: 3, locked: true }
  ]
};

async function sleep(ms: number) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

export async function initDatabase() {
  // Fail closed before accepting requests if encrypted private data cannot be protected.
  privateDataKey();
  const dbHost = process.env.DB_HOST;
  const dbUser = process.env.DB_USER;
  const dbPassword = process.env.DB_PASSWORD;
  const dbName = process.env.DB_NAME;
  const dbPort = parseInt(process.env.DB_PORT || '3306', 10);

  if (!dbHost || !dbUser) {
    console.log('[Database] Keine DB-Umgebungsvariablen gefunden. Nutze In-Memory Speicher.');
    if (Object.keys(memoryStore.users).length === 0) {
      const credentials = generateInitialCredentials();
      const hashedPin = hashPin(credentials.pin);
      memoryStore.users[credentials.username.toLowerCase()] = {
        courses: [], pin: hashedPin, preferences: { ...DEFAULT_PREFERENCES }, status: 'ADMIN'
      };
      memoryStore.admins.push(credentials.username.toLowerCase());
      console.warn(`[Database] Erster Admin angelegt: Benutzername=${credentials.username}, PIN=${credentials.pin}. PIN sofort sicher speichern.`);
    }
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
          class_name VARCHAR(64) NOT NULL DEFAULT '11',
          updated_at TIMESTAMP(6) DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
      `);

      await conn.query(`
        CREATE TABLE IF NOT EXISTS app_sessions (
          token_hash CHAR(64) PRIMARY KEY,
          username VARCHAR(64) NOT NULL,
          csrf_token VARCHAR(255) NOT NULL,
          expires_at DATETIME(3) NOT NULL,
          created_at DATETIME(3) NOT NULL,
          INDEX idx_app_sessions_user (username),
          INDEX idx_app_sessions_expiry (expires_at)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
      `);
      await conn.query('DELETE FROM app_sessions WHERE expires_at <= NOW()');

      await conn.query(`
        CREATE TABLE IF NOT EXISTS user_private_calendar_data (
          username VARCHAR(64) PRIMARY KEY,
          nonce BINARY(12) NOT NULL,
          ciphertext LONGBLOB NOT NULL,
          auth_tag BINARY(16) NOT NULL,
          crypto_version TINYINT UNSIGNED NOT NULL DEFAULT 1,
          updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
          CONSTRAINT fk_private_calendar_user FOREIGN KEY (username)
            REFERENCES users(username) ON DELETE CASCADE
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
      `);

      await conn.query(`
        CREATE TABLE IF NOT EXISTS calendar_login_attempts (
          id BIGINT AUTO_INCREMENT PRIMARY KEY,
          username VARCHAR(64) NOT NULL,
          ip_address VARCHAR(64) NOT NULL,
          attempted_at VARCHAR(40) NOT NULL,
          successful TINYINT(1) NOT NULL
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
      `);
      await conn.query(`
        CREATE INDEX idx_calendar_login_attempts_lookup
        ON calendar_login_attempts(username, ip_address, attempted_at)
      `).catch((error: any) => {
        if (error?.code !== 'ER_DUP_KEYNAME') throw error;
      });

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

      // The initial VP class belongs to the shared calendar account as well.
      // Keeping it here lets the VP service bootstrap a matching account on a
      // fresh browser/device instead of falling back to VP_DEFAULT_CLASS.
      try {
        await conn.query("ALTER TABLE users ADD COLUMN class_name VARCHAR(64) NOT NULL DEFAULT '11';");
      } catch (e) {}

      // Migrate the former standalone admin list into the authoritative user
      // status before removing the redundant table.
      const [legacyAdminTables]: any = await conn.query("SHOW TABLES LIKE 'admins'");
      if (legacyAdminTables.length > 0) {
        await conn.query(`
          UPDATE users
          INNER JOIN admins ON LOWER(admins.username) = LOWER(users.username)
          SET users.status = 'ADMIN'
        `);
        await conn.query('DROP TABLE admins');
      }

      // Create categories table
      await conn.query(`
        CREATE TABLE IF NOT EXISTS event_categories (
          id VARCHAR(64) PRIMARY KEY,
          name VARCHAR(64) NOT NULL,
          color VARCHAR(16) NOT NULL,
          sort_order INT NOT NULL DEFAULT 0,
          locked TINYINT(1) NOT NULL DEFAULT 0
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
      `);
      // Bei alten Datenbanken wird die neue Sperrspalte einmalig ergänzt und
      // Ferien als sichere Voreinstellung gesperrt. Sobald die Spalte bereits
      // existiert, bleibt eine spätere Admin-Entscheidung (auch Entsperren)
      // unverändert erhalten.
      try {
        await conn.query('ALTER TABLE event_categories ADD COLUMN locked TINYINT(1) NOT NULL DEFAULT 0;');
        await conn.query("UPDATE event_categories SET locked = 1 WHERE LOWER(id) = 'ferien'");
      } catch (e) {}

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
          deleted_by VARCHAR(64) DEFAULT NULL,
          created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
          updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
      `);

      try {
        await conn.query('ALTER TABLE events ADD COLUMN start_time VARCHAR(16) DEFAULT NULL;');
      } catch (e) {}
      try {
        await conn.query('ALTER TABLE events ADD COLUMN end_time VARCHAR(16) DEFAULT NULL;');
      } catch (e) {}
      try {
        await conn.query('ALTER TABLE events ADD COLUMN deleted_by VARCHAR(64) DEFAULT NULL;');
      } catch (e) {}
      try { await conn.query('ALTER TABLE events ADD COLUMN updated_at TIMESTAMP(6) DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6);'); } catch (e) {}
      try { await conn.query('ALTER TABLE events MODIFY COLUMN updated_at TIMESTAMP(6) DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6);'); } catch (e) {}

      await conn.query(`
        DELETE FROM events
        WHERE COALESCE(NULLIF(end_date, ''), date) < DATE_FORMAT(DATE_SUB(CURDATE(), INTERVAL 18 MONTH), '%Y-%m-%d')
      `);

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

      // Seed one random administrator on a genuinely empty installation.
      const [rows]: any = await conn.query('SELECT COUNT(*) as count FROM users');
      if (rows[0].count === 0) {
        const credentials = generateInitialCredentials();
        await conn.query(
          'INSERT INTO users (username, courses, pin, preferences, status) VALUES (?, ?, ?, ?, ?)',
          [credentials.username, '[]', hashPin(credentials.pin), JSON.stringify(DEFAULT_PREFERENCES), 'ADMIN']
        );
        console.warn(`[Database] Erster Admin angelegt: Benutzername=${credentials.username}, PIN=${credentials.pin}. PIN sofort sicher speichern.`);
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
          { id: 'FERIEN', name: 'Ferien', color: '#f1c40f', locked: 1 }
        ];
        for (let i = 0; i < defCats.length; i++) {
          const c = defCats[i];
          await conn.query(
            'INSERT INTO event_categories (id, name, color, sort_order, locked) VALUES (?, ?, ?, ?, ?)',
            [c.id, c.name, c.color, i, c.locked ? 1 : 0]
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
    const parsedPreferences = typeof row.preferences === 'string' ? JSON.parse(row.preferences || '{}') : (row.preferences || {});
    const themeMode = parsedPreferences.themeMode || (
      Object.prototype.hasOwnProperty.call(parsedPreferences, 'darkMode')
        ? (parsedPreferences.darkMode ? 'dark' : 'light')
        : 'system'
    );
    return {
      username: row.username,
      courses: normalizedCourses,
      pin: row.pin || undefined,
      status: row.status || 'ACTIVE',
      className: String(row.class_name || '11'),
      preferences: { ...DEFAULT_PREFERENCES, ...parsedPreferences, themeMode }
    };
  }
  const memoryUser = memoryStore.users[uname];
  if (!memoryUser) return null;
  return {
    username: uname,
    courses: memoryUser.courses || [],
    pin: memoryUser.pin || undefined,
    status: memoryUser.status || 'ACTIVE',
    className: memoryUser.className || '11',
    preferences: { ...DEFAULT_PREFERENCES, ...(memoryUser.preferences || {}) }
  };
}

export async function dbGetUsers() {
  if (isConnected && pool) {
    const [vpOnlyTables]: any = await pool.query("SHOW TABLES LIKE 'vp_only_users'");
    if (vpOnlyTables.length) {
      const [rows]: any = await pool.query(`
        SELECT username, status, 0 AS vpOnly FROM users
        UNION
        SELECT vp.username, IF(vp.active = 1 AND only_users.active = 1, 'VP_ONLY', 'BLOCKED') AS status, 1 AS vpOnly
        FROM vp_only_users only_users
        JOIN vp_users vp ON vp.id = only_users.user_id
        LEFT JOIN users u ON LOWER(u.username) = LOWER(vp.username)
        WHERE u.username IS NULL
        ORDER BY username ASC
      `);
      return rows.map((r: any) => ({ username: r.username, status: r.status || 'ACTIVE', vpOnly: !!r.vpOnly }));
    }
    const [rows]: any = await pool.query('SELECT username, status, 0 AS vpOnly FROM users ORDER BY username ASC');
    return rows.map((r: any) => ({ username: r.username, status: r.status || 'ACTIVE', vpOnly: false }));
  }
  return Object.keys(memoryStore.users).sort().map(uname => ({
    username: uname,
    status: memoryStore.users[uname].status || 'ACTIVE',
    vpOnly: false,
  }));
}

export async function dbSaveUser(username: string, data: { courses?: string[]; pin?: string | null; preferences?: any; status?: string; className?: string }) {
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
  const className = data.className !== undefined
    ? String(data.className).trim()
    : String(existing?.className || '11').trim();
  if (!className || className.length > 64) {
    throw new Error('Ungültige Klasse.');
  }

  if (isConnected && pool) {
    await pool.query(
      `INSERT INTO users (username, courses, pin, preferences, status, class_name) 
       VALUES (?, ?, ?, ?, ?, ?) 
       ON DUPLICATE KEY UPDATE courses = VALUES(courses), pin = VALUES(pin), preferences = VALUES(preferences), status = VALUES(status), class_name = VALUES(class_name)`,
      [uname, JSON.stringify(courses), hashedPin, JSON.stringify(preferences), status, className]
    );
    // Calendar `users.pin` is authoritative. Keep the legacy VP column only
    // for rolling compatibility and VP-only account migration.
    if (data.pin !== undefined) {
      try {
        await pool.query('UPDATE vp_users SET pin_hash = ? WHERE LOWER(username) = LOWER(?)', ['', uname]);
      } catch (error: any) {
        // During a rolling/fresh start, the VP service may not have created its
        // compatibility table yet. `users.pin` is already saved successfully.
        if (error?.code !== 'ER_NO_SUCH_TABLE') throw error;
      }
    }
    if (data.courses !== undefined) {
      const [vpRows]: any = await pool.query('SELECT id, class_name FROM vp_users WHERE LOWER(username) = LOWER(?)', [uname]);
      if (vpRows.length) {
        const vpUser = vpRows[0];
        const connection = await pool.getConnection();
        try {
          await connection.beginTransaction();
          await connection.query('DELETE FROM vp_user_selected_classes WHERE user_id = ?', [vpUser.id]);
          await connection.query('INSERT INTO vp_user_selected_classes(user_id, class_name) VALUES (?, ?)', [vpUser.id, vpUser.class_name]);
          await connection.query('DELETE FROM vp_user_subject_selections WHERE user_id = ?', [vpUser.id]);
          for (const courseId of courses) {
            await connection.query(
              'INSERT INTO vp_user_subject_selections(user_id, class_name, subject_key) VALUES (?, ?, ?)',
              [vpUser.id, vpUser.class_name, `subject:${courseId}`]
            );
          }
          await connection.commit();
        } catch (error) {
          await connection.rollback();
          throw error;
        } finally {
          connection.release();
        }
      }
    }
    if (data.className !== undefined) {
      try {
        await pool.query('UPDATE vp_users SET class_name = ? WHERE LOWER(username) = LOWER(?)', [className, uname]);
      } catch (error: any) {
        if (error?.code !== 'ER_NO_SUCH_TABLE') throw error;
      }
    }
    return { username: uname, courses, pin: hashedPin || undefined, preferences, status, className };
  }

  memoryStore.users[uname] = {
    courses,
    pin: hashedPin || undefined,
    preferences,
    status,
    className,
  };
  return { username: uname, ...memoryStore.users[uname] };
}

export async function dbAdminSetUserPin(username: string, pin: string) {
  if (!/^\d{4}$/.test(pin)) {
    throw new Error('Die PIN muss exakt vier Ziffern enthalten.');
  }
  const uname = username.toLowerCase();
  if (isConnected && pool) {
    const [vpOnlyTables]: any = await pool.query("SHOW TABLES LIKE 'vp_only_users'");
    const [vpUserTables]: any = await pool.query("SHOW TABLES LIKE 'vp_users'");
    if (vpOnlyTables.length && vpUserTables.length) {
      const [vpOnlyRows]: any = await pool.query(
        `SELECT only_users.user_id
         FROM vp_only_users only_users
         JOIN vp_users vp ON vp.id = only_users.user_id
         LEFT JOIN users calendar_users ON LOWER(calendar_users.username) = LOWER(vp.username)
         WHERE LOWER(vp.username) = LOWER(?)
           AND calendar_users.username IS NULL
           AND vp.active = 1
           AND only_users.active = 1
         LIMIT 1`,
        [uname]
      );
      if (vpOnlyRows.length) {
        await pool.query(
          'UPDATE vp_only_users SET pin_hash = ?, must_change_pin = 1 WHERE user_id = ?',
          [hashPin(pin), vpOnlyRows[0].user_id]
        );
        await pool.query('UPDATE vp_users SET pin_hash = ? WHERE LOWER(username) = LOWER(?)', ['', uname]);
        await deleteUserSessions(uname);
        return { success: true, vpOnly: true };
      }
    }
  }
  const existing = await dbGetUser(uname);
  if (!existing) {
    throw new Error('Kalendernutzer nicht gefunden.');
  }
  if (existing.status === 'ADMIN') {
    throw new Error('Admin-PINs können nur durch den Benutzer selbst geändert werden.');
  }
  const preferences = { ...(existing.preferences || DEFAULT_PREFERENCES), forcePinChange: true };
  return dbSaveUser(uname, { pin, preferences });
}

export async function dbCreateVpOnlyUser(data: {
  username: string;
  pin: string;
  className: string;
  createdBy: string;
  ntfyTopic: string;
  ntfyUsername: string;
  ntfyPassword: string;
}) {
  const uname = data.username.trim().toLowerCase();
  if (!/^[A-Za-z0-9._-]{3,64}$/.test(uname)) throw new Error('Ungültiger Benutzername.');
  if (!/^\d{4}$/.test(data.pin)) throw new Error('VP-only-Nutzer brauchen eine vierstellige Start-PIN.');
  const className = data.className.trim();
  if (!className || className.length > 64) throw new Error('Ungültige Klasse.');
  const encryptedPassword = fernetEncrypt(data.ntfyPassword);
  if (isConnected && pool) {
    const [existingCalendar]: any = await pool.query('SELECT 1 FROM users WHERE LOWER(username) = LOWER(?)', [uname]);
    if (existingCalendar.length) throw new Error('Name bereits vergeben.');
    const [existingVp]: any = await pool.query("SHOW TABLES LIKE 'vp_users'");
    if (!existingVp.length) throw new Error('VP-Tabellen sind noch nicht migriert. Starte den VP-Dienst einmal neu.');
    const [existingVpUser]: any = await pool.query('SELECT 1 FROM vp_users WHERE LOWER(username) = LOWER(?)', [uname]);
    if (existingVpUser.length) throw new Error('Name bereits vergeben.');
    const connection = await pool.getConnection();
    try {
      await connection.beginTransaction();
      const [result]: any = await connection.query(
        `INSERT INTO vp_users(username, pin_hash, class_name, active, ntfy_topic, ntfy_username, ntfy_password_encrypted, created_at)
         VALUES (?, '', ?, 1, ?, ?, ?, ?)`,
        [uname, className, data.ntfyTopic, data.ntfyUsername, encryptedPassword, new Date().toISOString()]
      );
      await connection.query(
        `INSERT INTO vp_only_users(username, user_id, pin_hash, active, must_change_pin, created_by, created_at)
         VALUES (?, ?, ?, 1, 1, ?, ?)`,
        [uname, result.insertId, hashPin(data.pin), data.createdBy.toLowerCase(), new Date().toISOString()]
      );
      await connection.commit();
    } catch (error) {
      await connection.rollback();
      throw error;
    } finally {
      connection.release();
    }
    return { username: uname, status: 'VP_ONLY', vpOnly: true };
  }
  throw new Error('VP-only-Nutzer benötigen die gemeinsame Datenbank.');
}

export async function dbDeleteUser(username: string) {
  const uname = username.toLowerCase();
  if (isConnected && pool) {
    const [vpRows]: any = await pool.query("SHOW TABLES LIKE 'vp_users'");
    if (vpRows.length) {
      await pool.query('DELETE FROM vp_users WHERE LOWER(username) = LOWER(?)', [uname]);
    }
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

async function dbLoadPrivateCalendar(username: string): Promise<PrivateCalendarData> {
  const normalized = username.toLowerCase();
  if (isConnected && pool) {
    const [rows]: any = await pool.query('SELECT nonce, ciphertext, auth_tag FROM user_private_calendar_data WHERE username = ?', [normalized]);
    return rows.length ? decryptPrivateData(normalized, rows[0]) : { categories: [], events: [] };
  }
  return structuredClone(privateMemoryStore.get(normalized) || { categories: [], events: [] });
}

async function dbStorePrivateCalendar(username: string, data: PrivateCalendarData): Promise<void> {
  const normalized = username.toLowerCase();
  if (isConnected && pool) {
    const encrypted = encryptPrivateData(normalized, data);
    await pool.query(
      `INSERT INTO user_private_calendar_data(username, nonce, ciphertext, auth_tag, crypto_version)
       VALUES (?, ?, ?, ?, 1) ON DUPLICATE KEY UPDATE nonce=VALUES(nonce), ciphertext=VALUES(ciphertext), auth_tag=VALUES(auth_tag), crypto_version=1`,
      [normalized, encrypted.nonce, encrypted.ciphertext, encrypted.authTag]
    );
    return;
  }
  privateMemoryStore.set(normalized, structuredClone(data));
}

export async function dbGetPrivateCalendar(username: string): Promise<PrivateCalendarData> {
  return dbLoadPrivateCalendar(username);
}

export async function dbCreatePrivateCategory(username: string, category: any) {
  return withPrivateMutation(username, async () => {
    const data = await dbLoadPrivateCalendar(username);
    if (data.categories.length >= 5) throw new Error('CATEGORY_LIMIT');
    if (data.categories.some(c => c.name.toLocaleLowerCase('de-DE') === category.name.toLocaleLowerCase('de-DE'))) throw new Error('CATEGORY_DUPLICATE');
    data.categories.push({ ...category, isPrivate: true, sort_order: data.categories.length });
    await dbStorePrivateCalendar(username, data);
    return data.categories.at(-1);
  });
}

export async function dbDeletePrivateCategory(username: string, categoryId: string): Promise<boolean> {
  return withPrivateMutation(username, async () => {
    const data = await dbLoadPrivateCalendar(username);
    if (!data.categories.some(c => c.id === categoryId)) return false;
    data.categories = data.categories.filter(c => c.id !== categoryId);
    data.events = data.events.filter(e => e.type !== categoryId);
    await dbStorePrivateCalendar(username, data);
    return true;
  });
}

export async function dbCreatePrivateEvent(username: string, event: any) {
  return withPrivateMutation(username, async () => {
    const data = await dbLoadPrivateCalendar(username);
    if (!data.categories.some(c => c.id === event.type)) return null;
    data.events.push({ ...event, endDate: event.endDate || event.date, courseId: 'ALLGEMEIN', author: username.toLowerCase(), updatedAt: new Date().toISOString() });
    await dbStorePrivateCalendar(username, data);
    return data.events.at(-1);
  });
}

export async function dbUpdatePrivateEvent(username: string, id: string, update: any) {
  return withPrivateMutation(username, async () => {
    const data = await dbLoadPrivateCalendar(username);
    const index = data.events.findIndex(e => e.id === id);
    if (index < 0) return null;
    const nextType = update.type ?? data.events[index].type;
    if (!data.categories.some(c => c.id === nextType)) throw new Error('PRIVATE_CATEGORY_FORBIDDEN');
    const nextEvent = { ...data.events[index], ...update, id, courseId: 'ALLGEMEIN', author: username.toLowerCase(), updatedAt: new Date().toISOString() };
    if (!nextEvent.endDate || nextEvent.endDate < nextEvent.date) nextEvent.endDate = nextEvent.date;
    data.events[index] = nextEvent;
    await dbStorePrivateCalendar(username, data);
    return data.events[index];
  });
}

export async function dbDeletePrivateEvent(username: string, id: string): Promise<boolean> {
  return withPrivateMutation(username, async () => {
    const data = await dbLoadPrivateCalendar(username);
    const before = data.events.length;
    data.events = data.events.filter(e => e.id !== id);
    if (before === data.events.length) return false;
    await dbStorePrivateCalendar(username, data);
    return true;
  });
}

export async function dbCleanupExpiredEvents(): Promise<number> {
  const cutoff = new Date();
  cutoff.setMonth(cutoff.getMonth() - 18);
  const cutoffDate = cutoff.toISOString().slice(0, 10);
  let removed = 0;
  if (isConnected && pool) {
    const [result]: any = await pool.query(`DELETE FROM events WHERE COALESCE(NULLIF(end_date, ''), date) < ?`, [cutoffDate]);
    removed += result.affectedRows || 0;
    const [users]: any = await pool.query('SELECT username FROM user_private_calendar_data');
    for (const row of users) {
      const data = await dbLoadPrivateCalendar(row.username);
      const before = data.events.length;
      data.events = data.events.filter(e => (e.endDate || e.date) >= cutoffDate);
      if (data.events.length !== before) await dbStorePrivateCalendar(row.username, data);
      removed += before - data.events.length;
    }
    return removed;
  }
  const before = memoryStore.events.length;
  memoryStore.events = memoryStore.events.filter(e => (e.endDate || e.date) >= cutoffDate);
  removed += before - memoryStore.events.length;
  for (const [username, data] of privateMemoryStore) {
    const privateBefore = data.events.length;
    data.events = data.events.filter(e => (e.endDate || e.date) >= cutoffDate);
    privateMemoryStore.set(username, data);
    removed += privateBefore - data.events.length;
  }
  return removed;
}

// Event Operations (with Soft Delete)
export async function dbGetEvents(includeDeleted = false, visibleCourses?: string[]) {
  if (isConnected && pool) {
    const query = includeDeleted
      ? "SELECT events.*, DATE_FORMAT(updated_at, '%Y-%m-%dT%H:%i:%s.%fZ') AS updated_at_version FROM events ORDER BY date ASC, start_time ASC"
      : "SELECT events.*, DATE_FORMAT(updated_at, '%Y-%m-%dT%H:%i:%s.%fZ') AS updated_at_version FROM events WHERE deleted_at IS NULL ORDER BY date ASC, start_time ASC";
    const [rows]: any = await pool.query(query);
    const events = rows.map((r: any) => ({
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
      updatedAt: r.updated_at_version || (r.updated_at ? new Date(r.updated_at).toISOString() : undefined),
      deletedAt: r.deleted_at || undefined,
      deletedBy: r.deleted_by || undefined
    }));
    if (!visibleCourses) return events;
    const allowed = new Set(visibleCourses.map(String));
    return events.filter((event: any) => event.courseId === 'ALLGEMEIN' || allowed.has(String(event.courseId)));
  }
  const events = memoryStore.events
    .filter(e => includeDeleted || !e.deletedAt)
    .map(e => ({ ...e }));
  if (!visibleCourses) return events;
  const allowed = new Set(visibleCourses.map(String));
  return events.filter((event: any) => event.courseId === 'ALLGEMEIN' || allowed.has(String(event.courseId)));
}

export async function dbGetEventById(id: string) {
  if (isConnected && pool) {
    const [rows]: any = await pool.query("SELECT events.*, DATE_FORMAT(updated_at, '%Y-%m-%dT%H:%i:%s.%fZ') AS updated_at_version FROM events WHERE id = ?", [id]);
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
      updatedAt: r.updated_at_version || (r.updated_at ? new Date(r.updated_at).toISOString() : undefined),
      deletedAt: r.deleted_at || undefined,
      deletedBy: r.deleted_by || undefined
    };
  }
  const found = memoryStore.events.find(e => e.id === id);
  return found ? { ...found } : null;
}

export async function dbCreateEvent(event: any) {
  event.updatedAt = new Date().toISOString();
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

function mysqlTimestampFromIso(value: string): string | null {
  const preciseMatch = /^(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})\.(\d{1,6})Z$/.exec(value);
  if (preciseMatch) return `${preciseMatch[1].replace('T', ' ')}.${preciseMatch[2].padEnd(6, '0')}`;
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return null;
  return `${parsed.toISOString().slice(0, 23).replace('T', ' ')}000`;
}

export async function dbUpdateEvent(id: string, data: any, expectedUpdatedAt?: string) {
  if (isConnected && pool) {
    const [existing]: any = await pool.query("SELECT events.*, DATE_FORMAT(updated_at, '%Y-%m-%dT%H:%i:%s.%fZ') AS updated_at_version FROM events WHERE id = ?", [id]);
    if (existing.length === 0) return null;
    const current = existing[0];

    const title = data.title !== undefined ? data.title : current.title;
    const date = data.date !== undefined ? data.date : current.date;
    let endDate = data.endDate !== undefined ? data.endDate : current.end_date;
    const startTime = data.startTime !== undefined ? data.startTime : current.start_time;
    const endTime = data.endTime !== undefined ? data.endTime : current.end_time;
    const courseId = data.courseId !== undefined ? data.courseId : current.course_id;
    const type = data.type !== undefined ? data.type : current.type;
    const description = data.description !== undefined ? data.description : current.description;
    const attachments = data.attachments !== undefined ? JSON.stringify(data.attachments) : current.attachments;

    if (!endDate || endDate < date) endDate = date;

    const expectedTimestamp = expectedUpdatedAt ? mysqlTimestampFromIso(expectedUpdatedAt) : null;
    if (expectedUpdatedAt && !expectedTimestamp) return null;
    const [result]: any = await pool.query(
      `UPDATE events 
       SET title = ?, date = ?, end_date = ?, start_time = ?, end_time = ?, course_id = ?, type = ?, description = ?, attachments = ?, updated_at = CURRENT_TIMESTAMP(6)
       WHERE id = ?${expectedTimestamp ? ' AND updated_at = ?' : ''}`,
      expectedTimestamp
        ? [title, date, endDate, startTime, endTime, courseId, type, description, attachments, id, expectedTimestamp]
        : [title, date, endDate, startTime, endTime, courseId, type, description, attachments, id]
    );
    if (result.affectedRows !== 1) return null;

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
      attachments: typeof attachments === 'string' ? JSON.parse(attachments) : attachments,
      updatedAt: current.updated_at_version || (current.updated_at ? new Date(current.updated_at).toISOString() : new Date().toISOString())
    };
  }

  const idx = memoryStore.events.findIndex(e => e.id === id);
  if (idx !== -1) {
    const current = memoryStore.events[idx];
    if (expectedUpdatedAt && current.updatedAt && current.updatedAt !== expectedUpdatedAt) return null;
    const nextUpdateAt = new Date(Math.max(Date.now(), (current.updatedAt ? new Date(current.updatedAt).getTime() : 0) + 1)).toISOString();
    const nextEvent = { ...current, ...data, updatedAt: nextUpdateAt };
    if (!nextEvent.endDate || nextEvent.endDate < nextEvent.date) nextEvent.endDate = nextEvent.date;
    memoryStore.events[idx] = nextEvent;
    return memoryStore.events[idx];
  }
  return null;
}

// Soft Delete
export async function dbDeleteEvent(id: string, deletedBy: string) {
  if (isConnected && pool) {
    await pool.query('UPDATE events SET deleted_at = NOW(), deleted_by = ? WHERE id = ?', [deletedBy, id]);
    return true;
  }
  const event = memoryStore.events.find(e => e.id === id);
  if (event) {
    event.deletedAt = new Date().toISOString();
    event.deletedBy = deletedBy;
  }
  return true;
}

// Restore
export async function dbRestoreEvent(id: string) {
  if (isConnected && pool) {
    await pool.query('UPDATE events SET deleted_at = NULL, deleted_by = NULL WHERE id = ?', [id]);
    return true;
  }
  const event = memoryStore.events.find(e => e.id === id);
  if (event) {
    delete event.deletedAt;
    delete event.deletedBy;
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
    const [userAdmins]: any = await pool.query("SELECT username FROM users WHERE status = 'ADMIN'");
    userAdmins.forEach((r: any) => set.add(r.username.toLowerCase()));
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
    const [userRows]: any = await pool.query("SELECT status FROM users WHERE username = ?", [uname]);
    return userRows.length > 0 && userRows[0].status === 'ADMIN';
  }
  const memUser = memoryStore.users[uname];
  if (memUser && memUser.status === 'ADMIN') return true;
  return memoryStore.admins.map(a => a.toLowerCase()).includes(uname);
}

export async function dbAddAdmin(username: string): Promise<string[]> {
  if (!username) return dbGetAdmins();
  const uname = username.toLowerCase().trim();
  if (isConnected && pool) {
    await pool.query("UPDATE users SET status = 'ADMIN' WHERE username = ?", [uname]);
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
  if (isConnected && pool) {
    await pool.query("UPDATE users SET status = 'ACTIVE' WHERE username = ?", [uname]);
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
      // MariaDB-Treiber können TINYINT(1) je nach Konfiguration als Zahl oder
      // als String liefern. `!!'0'` wäre fälschlich wahr und würde jede
      // öffentliche Kategorie als gesperrt behandeln.
      locked: r.locked === true || r.locked === 1 || r.locked === '1',
      sort_order: r.sort_order
    }));
  }
  return [...memoryStore.categories].sort((a, b) => a.sort_order - b.sort_order);
}

export async function dbSaveCategory(category: { id: string; name: string; color: string; sort_order?: number; locked?: boolean }) {
  if (isConnected && pool) {
    const sortOrder = category.sort_order !== undefined ? category.sort_order : 999;
    await pool.query(
      `INSERT INTO event_categories (id, name, color, sort_order, locked)
       VALUES (?, ?, ?, ?, ?)
       ON DUPLICATE KEY UPDATE name = VALUES(name), color = VALUES(color), sort_order = VALUES(sort_order), locked = VALUES(locked)`,
      [category.id, category.name, category.color, sortOrder, category.locked ? 1 : 0]
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
