const SESSION_DAYS = 7;

export interface StoredSession {
  username: string;
  sessionToken: string;
  timestamp: number;
}

export function getCookie(name: string): string | null {
  if (typeof document === 'undefined') return null;
  const nameEQ = name + "=";
  const ca = document.cookie.split(';');
  for (let i = 0; i < ca.length; i++) {
    let c = ca[i];
    while (c.charAt(0) === ' ') c = c.substring(1, c.length);
    if (c.indexOf(nameEQ) === 0) return decodeURIComponent(c.substring(nameEQ.length, c.length));
  }
  return null;
}

export function setCookie(name: string, value: string, days: number = SESSION_DAYS) {
  if (typeof document === 'undefined') return;
  const date = new Date();
  date.setTime(date.getTime() + (days * 24 * 60 * 60 * 1000));
  const expires = "; expires=" + date.toUTCString();
  document.cookie = `${name}=${encodeURIComponent(value)}${expires}; path=/; SameSite=Lax`;
}

export function deleteCookie(name: string) {
  if (typeof document === 'undefined') return;
  document.cookie = `${name}=; expires=Thu, 01 Jan 1970 00:00:00 UTC; path=/; SameSite=Lax`;
}

export function getStoredSession(username: string): StoredSession | null {
  if (!username) return null;
  const key = `cal11_session_${username.toLowerCase()}`;
  
  try {
    localStorage.removeItem(key);
    localStorage.removeItem('cal11_session_token');
  } catch (e) {}

  deleteCookie(key);
  deleteCookie('cal11_active_token');
  return null;
}

export function saveStoredSession(username: string, sessionToken?: string) {
  if (!username) return;
  const key = `cal11_session_${username.toLowerCase()}`;
  
  try {
    localStorage.removeItem(key);
    localStorage.removeItem('cal11_session_token');
  } catch (e) {}

  // Sitzungen werden ausschließlich als HttpOnly-Cookie vom Server verwaltet.
  deleteCookie(key);
  deleteCookie('cal11_active_token');
}

export function clearStoredSession(username: string) {
  if (!username) return;
  const key = `cal11_session_${username.toLowerCase()}`;
  
  try {
    localStorage.removeItem(key);
    localStorage.removeItem('cal11_session_token');
  } catch (e) {}
  
  deleteCookie(key);
  deleteCookie('cal11_active_token');
}
