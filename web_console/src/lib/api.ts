import { getBackendUrl } from '../store/backendStore';

export interface ApiClientOptions {
  baseUrl?: string;
}

export class ApiClient {
  private readonly _baseUrlOverride?: string;

  constructor(options: ApiClientOptions = {}) {
    this._baseUrlOverride = options.baseUrl;
  }

  /** Resolve the API base URL dynamically so hosted-mode URL changes are picked up. */
  private get baseUrl(): string {
    if (this._baseUrlOverride) return this._baseUrlOverride;
    const backend = getBackendUrl();
    return backend ? `${backend}/api/gui` : '/api/gui';
  }

  async get<T>(path: string): Promise<T> {
    const response = await fetch(`${this.baseUrl}${path}`);
    if (!response.ok) {
      let detail = '';
      try { const j = await response.json(); detail = j?.error?.message ?? JSON.stringify(j); } catch { /* */ }
      throw new Error(`GET ${path} failed (${response.status}): ${detail}`);
    }
    return response.json() as Promise<T>;
  }

  async post<T>(path: string, body?: unknown): Promise<T> {
    const response = await fetch(`${this.baseUrl}${path}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: body === undefined ? undefined : JSON.stringify(body)
    });
    if (!response.ok) {
      let detail = '';
      try { const j = await response.json(); detail = j?.error?.message ?? JSON.stringify(j); } catch { /* */ }
      throw new Error(`POST ${path} failed (${response.status}): ${detail}`);
    }
    return response.json() as Promise<T>;
  }

  async upload<T>(path: string, file: File): Promise<T> {
    const formData = new FormData();
    formData.append('file', file);
    const response = await fetch(`${this.baseUrl}${path}`, {
      method: 'POST',
      body: formData,
    });
    if (!response.ok) {
      throw new Error(`UPLOAD ${path} failed with ${response.status}`);
    }
    return response.json() as Promise<T>;
  }

  async patch<T>(path: string, body?: unknown): Promise<T> {
    const response = await fetch(`${this.baseUrl}${path}`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: body === undefined ? undefined : JSON.stringify(body),
    });
    if (!response.ok) {
      throw new Error(`PATCH ${path} failed with ${response.status}`);
    }
    return response.json() as Promise<T>;
  }

  async del<T>(path: string, body?: unknown): Promise<T> {
    const response = await fetch(`${this.baseUrl}${path}`, {
      method: 'DELETE',
      headers: { 'Content-Type': 'application/json' },
      body: body === undefined ? undefined : JSON.stringify(body),
    });
    if (!response.ok) {
      throw new Error(`DELETE ${path} failed with ${response.status}`);
    }
    return response.json() as Promise<T>;
  }
}

export const apiClient = new ApiClient();
