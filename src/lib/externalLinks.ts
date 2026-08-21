const meta = import.meta as ImportMeta & { env?: Record<string, string | undefined> };

const browserOrigin = typeof window !== 'undefined' ? window.location.origin : '';
const host = typeof window !== 'undefined' ? window.location.host : '';
const inferredVpOrigin = host.startsWith('cal11.')
  ? `${window.location.protocol}//vp.${host.substring('cal11.'.length)}`
  : '';

export const KALENDER_URL = meta.env?.VITE_KALENDER_URL || browserOrigin || 'http://127.0.0.1:3000';
export const VERTRETUNGSPLAN_URL =
  meta.env?.VITE_VERTRETUNGSPLAN_URL || inferredVpOrigin || 'http://127.0.0.1:8000';
