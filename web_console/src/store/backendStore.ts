/**
 * Backend URL store — manages the Hermes backend URL in localStorage.
 *
 * When running locally (dev server or self-hosted), uses relative paths
 * so everything works as before. When running on a hosted domain (e.g.
 * hermes.gary-labs.online), reads the user-configured backend URL.
 */

const STORAGE_KEY = 'hermes-backend-url';
const DEFAULT_URL = 'http://localhost:8642';

/** True when the frontend is served from the same host as the backend. */
export function isLocalMode(): boolean {
  const host = window.location.hostname;
  return host === 'localhost' || host === '127.0.0.1' || window.location.port === '5173';
}

/**
 * Get the backend base URL (no trailing slash).
 *
 * - Local mode → returns '' (empty string = relative paths, same-origin)
 * - Hosted mode → returns stored URL or default
 */
export function getBackendUrl(): string {
  if (isLocalMode()) return '';
  return localStorage.getItem(STORAGE_KEY) || DEFAULT_URL;
}

/** Persist a new backend URL. */
export function setBackendUrl(url: string): void {
  // Normalize: strip trailing slash
  const clean = url.replace(/\/+$/, '');
  localStorage.setItem(STORAGE_KEY, clean);
}

/** Clear the stored backend URL (reverts to default). */
export function clearBackendUrl(): void {
  localStorage.removeItem(STORAGE_KEY);
}

/** Check if a stored URL exists in localStorage. */
export function hasSavedBackendUrl(): boolean {
  return !!localStorage.getItem(STORAGE_KEY);
}
