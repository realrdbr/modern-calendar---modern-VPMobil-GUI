const meta = import.meta as ImportMeta & { env?: Record<string, string | undefined> };
type RuntimeConfig = { kalenderUrl?: string; vertretungsplanUrl?: string };
type RuntimeWindow = Window & { __CAL11_RUNTIME_CONFIG__?: RuntimeConfig };

const browserOrigin = typeof window !== 'undefined' ? window.location.origin : '';
const host = typeof window !== 'undefined' ? window.location.host : '';
const inferredVpOrigin = host.startsWith('cal11.')
  ? `${window.location.protocol}//vp.${host.substring('cal11.'.length)}`
  : '';
const runtimeConfig = typeof window !== 'undefined' ? (window as RuntimeWindow).__CAL11_RUNTIME_CONFIG__ : undefined;
const runtimeKalenderUrl = runtimeConfig?.kalenderUrl?.trim();
const runtimeVertretungsplanUrl = runtimeConfig?.vertretungsplanUrl?.trim();

export const KALENDER_URL =
  runtimeKalenderUrl || meta.env?.VITE_KALENDER_URL || browserOrigin || 'http://127.0.0.1:3000';
export const VERTRETUNGSPLAN_URL =
  runtimeVertretungsplanUrl || meta.env?.VITE_VERTRETUNGSPLAN_URL || inferredVpOrigin || 'http://127.0.0.1:8000';
