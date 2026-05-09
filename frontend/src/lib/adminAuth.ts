let sessionToken = "";
let currentUser: unknown = null;

export function getSessionToken(): string {
  return sessionToken;
}

export function setSession(token: string, user?: unknown): void {
  sessionToken = token;
  currentUser = token ? (user ?? currentUser) : null;
}

export function getCurrentUser<T = { username: string; role: string } | null>(): T | null {
  return (currentUser as T | null) ?? null;
}

export function installAdminFetchHeader(): void {
  // Kept as a no-op for older imports. New code calls apiFetch explicitly.
}

export function apiFetch(input: RequestInfo | URL, init?: RequestInit): Promise<Response> {
  const headers = new Headers(init?.headers ?? (input instanceof Request ? input.headers : undefined));
  const token = getSessionToken();
  const url = typeof input === "string" ? input : input instanceof URL ? input.toString() : input.url;
  const isApi =
    url.startsWith("/api") ||
    (typeof window !== "undefined" && url.startsWith(window.location.origin + "/api"));
  if (token && token !== "cookie-session" && isApi) {
    headers.set("Authorization", `Bearer ${token}`);
  }
  return fetch(input, { ...init, headers, credentials: init?.credentials ?? "same-origin" });
}
