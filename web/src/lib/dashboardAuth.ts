const SESSION_STORAGE_KEY = "hermes.dashboard.session";
const TOKEN_STORAGE_KEY = "hermes.dashboard.bootstrapToken";

export const DASHBOARD_SESSION_HEADER = "X-Hermes-Dashboard-Session";
export const LEGACY_SESSION_HEADER = "X-Hermes-Session-Token";

export type DashboardAuthMode = "none" | "token" | "password" | "trusted-proxy" | "tailscale";

export interface DashboardIdentity {
  user?: string | null;
  email?: string | null;
  name?: string | null;
  profile_pic?: string | null;
  source?: string | null;
}

export interface DashboardAuthStatus {
  mode: DashboardAuthMode;
  required: boolean;
  authenticated: boolean;
  supports_password_login: boolean;
  supports_token_login: boolean;
  identity?: DashboardIdentity | null;
}

export interface DashboardLoginResponse {
  ok: boolean;
  session_token?: string | null;
  identity?: DashboardIdentity | null;
}

declare global {
  interface Window {
    __HERMES_SESSION_TOKEN__?: string;
  }
}

function canUseSessionStorage(): boolean {
  try {
    return typeof window !== "undefined" && Boolean(window.sessionStorage);
  } catch {
    return false;
  }
}

export function getDashboardSessionToken(): string | null {
  if (canUseSessionStorage()) {
    const stored = window.sessionStorage.getItem(SESSION_STORAGE_KEY);
    if (stored) return stored;
  }
  return window.__HERMES_SESSION_TOKEN__ ?? null;
}

export function setDashboardSessionToken(token: string | null): void {
  if (!canUseSessionStorage()) return;
  if (token) window.sessionStorage.setItem(SESSION_STORAGE_KEY, token);
  else window.sessionStorage.removeItem(SESSION_STORAGE_KEY);
}

export function getBootstrapToken(): string | null {
  if (canUseSessionStorage()) {
    const stored = window.sessionStorage.getItem(TOKEN_STORAGE_KEY);
    if (stored) return stored;
  }
  return null;
}

export function clearBootstrapToken(): void {
  if (canUseSessionStorage()) window.sessionStorage.removeItem(TOKEN_STORAGE_KEY);
}

export function consumeTokenFromUrlFragment(): string | null {
  if (typeof window === "undefined") return null;
  const rawHash = window.location.hash.startsWith("#")
    ? window.location.hash.slice(1)
    : window.location.hash;
  if (!rawHash) return null;

  const params = new URLSearchParams(rawHash);
  const token = params.get("token") || params.get("auth_token");
  if (!token) return null;

  if (canUseSessionStorage()) window.sessionStorage.setItem(TOKEN_STORAGE_KEY, token);
  params.delete("token");
  params.delete("auth_token");
  const cleanHash = params.toString();
  const nextUrl = `${window.location.pathname}${window.location.search}${cleanHash ? `#${cleanHash}` : ""}`;
  window.history.replaceState(window.history.state, document.title, nextUrl);
  return token;
}

export function dashboardAuthHeaders(init?: HeadersInit): Headers {
  const headers = new Headers(init);
  const token = getDashboardSessionToken();
  if (token) {
    headers.set(DASHBOARD_SESSION_HEADER, token);
    headers.set(LEGACY_SESSION_HEADER, token);
  }
  return headers;
}

async function parseError(res: Response): Promise<string> {
  try {
    const data = await res.json();
    return String(data.detail || data.error || res.statusText);
  } catch {
    return res.statusText;
  }
}

export async function fetchAuthStatus(): Promise<DashboardAuthStatus> {
  const res = await fetch("/api/auth/status", { headers: dashboardAuthHeaders() });
  if (!res.ok) throw new Error(await parseError(res));
  return res.json();
}

export async function loginWithToken(token: string): Promise<DashboardLoginResponse> {
  const res = await fetch("/api/auth/login", {
    method: "POST",
    headers: dashboardAuthHeaders({ "Content-Type": "application/json" }),
    body: JSON.stringify({ token }),
  });
  if (!res.ok) throw new Error(await parseError(res));
  const data = (await res.json()) as DashboardLoginResponse;
  if (data.session_token) setDashboardSessionToken(data.session_token);
  clearBootstrapToken();
  return data;
}

export async function loginWithPassword(password: string): Promise<DashboardLoginResponse> {
  const res = await fetch("/api/auth/login", {
    method: "POST",
    headers: dashboardAuthHeaders({ "Content-Type": "application/json" }),
    body: JSON.stringify({ password }),
  });
  if (!res.ok) throw new Error(await parseError(res));
  const data = (await res.json()) as DashboardLoginResponse;
  if (data.session_token) setDashboardSessionToken(data.session_token);
  return data;
}

export async function logoutDashboard(): Promise<void> {
  await fetch("/api/auth/logout", { method: "POST", headers: dashboardAuthHeaders() }).catch(() => undefined);
  setDashboardSessionToken(null);
  clearBootstrapToken();
}

export function buildAuthenticatedWsUrl(path: string, params?: Record<string, string | null | undefined>): string {
  const proto = window.location.protocol === "https:" ? "wss:" : "ws:";
  const qs = new URLSearchParams();
  const token = getDashboardSessionToken();
  if (token) qs.set("token", token);
  for (const [key, value] of Object.entries(params ?? {})) {
    if (value != null && value !== "") qs.set(key, value);
  }
  const query = qs.toString();
  return `${proto}//${window.location.host}${path}${query ? `?${query}` : ""}`;
}
