// Which sign-in this API runs, read once at start from GET /auth/me:
//
// - 200: AUTH_MODE=oidc and signed in with Entra ID. The answer is the user (name, UPN, role
//   keys); the persona switcher is hidden and permissions come from the roles (lib/personas.ts).
// - 401: AUTH_MODE=oidc and not signed in (or the session ran out): off to the API's sign-in.
// - 404 or no answer: AUTH_MODE=dev, which has no /auth routes. Behaviour is what it always was:
//   the persona chosen in the top bar is sent as X-User.
//
// The session itself is an HttpOnly cookie the API set; nothing here reads or stores a token.
import { isOidc, loginUrl, redirectToLogin, requireApiUrl, setAuthMode } from "./api";

export interface Principal {
  oid: string;
  upn: string;
  name: string;
  /** Role keys: admin, viewer, sme, architect, engineer, qa, ops. */
  roles: string[];
}

export type AuthState =
  | { mode: "unknown"; me: null }
  | { mode: "dev"; me: null }
  | { mode: "oidc"; me: Principal | null };

async function fetchMe(): Promise<Response | null> {
  try {
    return await fetch(`${requireApiUrl()}/auth/me`, { credentials: "include" });
  } catch {
    return null;
  }
}

let probed: Promise<AuthState> | null = null;

/** Ask the API which mode it runs; a 401 also starts the sign-in (except on /signin). */
export function probeAuth(): Promise<AuthState> {
  probed = probe();
  return probed;
}

async function probe(): Promise<AuthState> {
  const res = await fetchMe();
  if (res?.status === 200) {
    setAuthMode("oidc");
    return { mode: "oidc", me: (await res.json()) as Principal };
  }
  if (res?.status === 401) {
    setAuthMode("oidc");
    redirectToLogin();
    return { mode: "oidc", me: null };
  }
  setAuthMode("dev");
  return { mode: "dev", me: null };
}

/**
 * For callers that cannot see a status code, such as an EventSource that failed: in oidc mode, ask
 * /auth/me whether the session is still good and go to the sign-in when it is not.
 */
export async function checkSession(): Promise<void> {
  await probed; // a stream can fail before the mode is known
  if (!isOidc()) return;
  const res = await fetchMe();
  if (res?.status === 401) redirectToLogin();
}

/** Sign out of this application (the Entra session stays), then to the sign-in screen. */
export async function signOutOidc(): Promise<void> {
  try {
    await fetch(`${requireApiUrl()}/auth/logout`, { method: "POST", credentials: "include" });
  } finally {
    window.location.assign("/signin");
  }
}

export { loginUrl };
