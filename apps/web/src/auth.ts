// OIDC Authorization Code + PKCE login for the Baryon AX web client.
//
// Active only when the build sets VITE_AUTH_MODE=oidc. In the default dev mode
// nothing here runs: apiFetch is a plain fetch and the role switcher keeps
// sending the X-User-Id / X-Roles development headers.
//
// Tokens live in sessionStorage (per tab, cleared when the tab closes). The API
// remains the authority: it validates every bearer token and derives the
// user, organization, and roles from it.

export type AuthSession = {
  userId: string;
  organizationId: string;
  roles: string[];
};

type TokenSet = {
  access_token: string;
  id_token?: string;
  refresh_token?: string;
  expires_at: number; // epoch ms
};

type Discovery = {
  authorization_endpoint: string;
  token_endpoint: string;
  end_session_endpoint?: string;
};

const env = import.meta.env;
export const OIDC_ENABLED = env.VITE_AUTH_MODE === "oidc";
const ISSUER = String(env.VITE_OIDC_ISSUER ?? "").replace(/\/$/, "");
const CLIENT_ID = String(env.VITE_OIDC_CLIENT_ID ?? "");
const SCOPE = String(env.VITE_OIDC_SCOPE ?? "openid profile email");
const API = "/api";

const TOKENS_KEY = "baryon-oidc-tokens";
const PKCE_KEY = "baryon-oidc-pkce";
const EXPIRY_SKEW_MS = 30_000;

let discovery: Discovery | null = null;
let refreshing: Promise<string | null> | null = null;
let redirecting = false;

function redirectUri(): string {
  return String(env.VITE_OIDC_REDIRECT_URI ?? "") || `${window.location.origin}/`;
}

function readJson<T>(storage: Storage, key: string): T | null {
  try {
    const raw = storage.getItem(key);
    return raw ? (JSON.parse(raw) as T) : null;
  } catch {
    return null;
  }
}

function base64Url(bytes: Uint8Array): string {
  let binary = "";
  bytes.forEach(byte => { binary += String.fromCharCode(byte); });
  return btoa(binary).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

function randomString(): string {
  const bytes = new Uint8Array(32);
  crypto.getRandomValues(bytes);
  return base64Url(bytes);
}

async function challengeFor(verifier: string): Promise<string> {
  const digest = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(verifier));
  return base64Url(new Uint8Array(digest));
}

async function getDiscovery(): Promise<Discovery> {
  if (discovery) return discovery;
  const response = await fetch(`${ISSUER}/.well-known/openid-configuration`);
  if (!response.ok) throw new Error(`OIDC discovery failed (${response.status})`);
  discovery = (await response.json()) as Discovery;
  return discovery;
}

function storeTokens(payload: Record<string, unknown>, previous?: TokenSet | null): TokenSet {
  const tokens: TokenSet = {
    access_token: String(payload.access_token),
    id_token: (payload.id_token as string | undefined) ?? previous?.id_token,
    refresh_token: (payload.refresh_token as string | undefined) ?? previous?.refresh_token,
    expires_at: Date.now() + Number(payload.expires_in ?? 300) * 1000,
  };
  sessionStorage.setItem(TOKENS_KEY, JSON.stringify(tokens));
  return tokens;
}

async function tokenRequest(body: Record<string, string>): Promise<Record<string, unknown>> {
  const { token_endpoint } = await getDiscovery();
  const response = await fetch(token_endpoint, {
    method: "POST",
    headers: { "Content-Type": "application/x-www-form-urlencoded" },
    body: new URLSearchParams({ client_id: CLIENT_ID, ...body }),
  });
  if (!response.ok) throw new Error(`token request failed (${response.status})`);
  return (await response.json()) as Record<string, unknown>;
}

/** Redirect to the IdP login page. The returned promise never resolves. */
export async function login(): Promise<never> {
  if (!redirecting) {
    redirecting = true;
    sessionStorage.removeItem(TOKENS_KEY);
    const { authorization_endpoint } = await getDiscovery();
    const verifier = randomString();
    const state = randomString();
    const returnTo = window.location.pathname + window.location.search + window.location.hash;
    sessionStorage.setItem(PKCE_KEY, JSON.stringify({ verifier, state, returnTo }));
    const url = new URL(authorization_endpoint);
    url.search = new URLSearchParams({
      response_type: "code",
      client_id: CLIENT_ID,
      redirect_uri: redirectUri(),
      scope: SCOPE,
      state,
      code_challenge: await challengeFor(verifier),
      code_challenge_method: "S256",
    }).toString();
    window.location.assign(url.toString());
  }
  return new Promise<never>(() => undefined);
}

async function handleCallback(params: URLSearchParams): Promise<void> {
  const pending = readJson<{ verifier: string; state: string; returnTo: string }>(sessionStorage, PKCE_KEY);
  sessionStorage.removeItem(PKCE_KEY);
  if (params.get("error")) throw new Error(`login failed: ${params.get("error_description") || params.get("error")}`);
  if (!pending || pending.state !== params.get("state")) throw new Error("login failed: state mismatch");
  const payload = await tokenRequest({
    grant_type: "authorization_code",
    code: params.get("code") ?? "",
    redirect_uri: redirectUri(),
    code_verifier: pending.verifier,
  });
  storeTokens(payload);
  window.history.replaceState(null, "", pending.returnTo || "/");
}

async function refresh(tokens: TokenSet): Promise<string | null> {
  if (!tokens.refresh_token) return null;
  try {
    const payload = await tokenRequest({ grant_type: "refresh_token", refresh_token: tokens.refresh_token });
    return storeTokens(payload, tokens).access_token;
  } catch {
    return null;
  }
}

/** A valid access token, refreshing it when it is about to expire; re-login otherwise. */
export async function accessToken(): Promise<string> {
  const tokens = readJson<TokenSet>(sessionStorage, TOKENS_KEY);
  if (tokens && tokens.expires_at - EXPIRY_SKEW_MS > Date.now()) return tokens.access_token;
  if (tokens) {
    refreshing ??= refresh(tokens).finally(() => { refreshing = null; });
    const next = await refreshing;
    if (next) return next;
  }
  return login();
}

/** fetch() for API calls: adds the bearer token in OIDC mode and re-logs in on 401. */
export async function apiFetch(input: string, init: RequestInit = {}): Promise<Response> {
  if (!OIDC_ENABLED) return fetch(input, init);
  const headers = new Headers(init.headers);
  headers.set("Authorization", `Bearer ${await accessToken()}`);
  const response = await fetch(input, { ...init, headers });
  if (response.status === 401) return login();
  return response;
}

/**
 * Completes a pending login callback or restores the stored session. Redirects
 * to the IdP when there is no usable session. Returns null in dev mode.
 */
export async function initAuth(): Promise<AuthSession | null> {
  if (!OIDC_ENABLED) return null;
  if (!ISSUER || !CLIENT_ID) throw new Error("VITE_OIDC_ISSUER and VITE_OIDC_CLIENT_ID are required when VITE_AUTH_MODE=oidc");
  const params = new URLSearchParams(window.location.search);
  const isCallback = params.has("state") && (params.has("code") || params.has("error"));
  if (isCallback) await handleCallback(params);
  // POST /v1/auth/login records auth.login once per sign-in; GET /v1/auth/me restores a session.
  const response = await apiFetch(`${API}/v1/auth/${isCallback ? "login" : "me"}`, { method: isCallback ? "POST" : "GET" });
  if (!response.ok) throw new Error(`could not load the signed-in user (${response.status})`);
  const body = (await response.json()) as { user_id: string; organization_id: string; roles: string[] };
  return { userId: body.user_id, organizationId: body.organization_id, roles: body.roles };
}

/** Records auth.logout, clears the local session, and ends the IdP session. */
export async function logout(): Promise<void> {
  const tokens = readJson<TokenSet>(sessionStorage, TOKENS_KEY);
  if (tokens && tokens.expires_at > Date.now()) {
    await fetch(`${API}/v1/auth/logout`, { method: "POST", headers: { Authorization: `Bearer ${tokens.access_token}` } }).catch(() => undefined);
  }
  sessionStorage.removeItem(TOKENS_KEY);
  const { end_session_endpoint } = await getDiscovery();
  if (!end_session_endpoint) {
    window.location.assign("/");
    return;
  }
  const url = new URL(end_session_endpoint);
  url.search = new URLSearchParams({
    client_id: CLIENT_ID,
    post_logout_redirect_uri: redirectUri(),
    ...(tokens?.id_token ? { id_token_hint: tokens.id_token } : {}),
  }).toString();
  window.location.assign(url.toString());
}
