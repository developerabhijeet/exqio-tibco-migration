// One place that answers "which backend does the web call?". The ports live in the repo-root .env
// (API_PORT, WEB_PORT), which the Makefile reads too, so moving the API is a one-line change there
// and the web follows. next.config.ts bakes the resolved URL into the bundle, so a plain
// `npm run dev` in web/ reaches the same API as `make web` rather than a stale hardcoded port;
// playwright.config.ts and the e2e specs resolve it through the same module.
//
// Precedence: an explicit NEXT_PUBLIC_API_URL, then the run's ports (E2E_API_PORT/E2E_WEB_PORT,
// which `make e2e` sets to two free ports), then API_PORT/WEB_PORT from the environment or .env,
// then the defaults below. api/tests/test_dev_ports.py keeps these defaults and the Makefile's in
// step, so the two cannot drift apart again.
import { existsSync, readFileSync } from "node:fs";
import { dirname, join, resolve } from "node:path";

export const DEFAULT_API_PORT = "9000";
export const DEFAULT_WEB_PORT = "3000";

/** The repo-root .env, looked for from the working directory upwards (web/ or the repo root). */
function findEnvFile(): string | undefined {
  let dir = resolve(process.cwd());
  for (let depth = 0; depth < 4; depth++) {
    const candidate = join(dir, ".env");
    if (existsSync(candidate)) return candidate;
    const parent = dirname(dir);
    if (parent === dir) break;
    dir = parent;
  }
  return undefined;
}

/** A `KEY=value` line from that .env; undefined when the file, the key or the value is missing. */
function fromEnvFile(key: string): string | undefined {
  const file = findEnvFile();
  if (!file) return undefined;
  try {
    for (const line of readFileSync(file, "utf8").split("\n")) {
      const match = /^\s*([A-Za-z0-9_]+)\s*=\s*(.*?)\s*$/.exec(line);
      if (match && match[1] === key) {
        const value = match[2].replace(/^["']|["']$/g, "");
        if (value) return value;
      }
    }
  } catch {
    /* unreadable .env: the default applies */
  }
  return undefined;
}

function port(runKey: string, key: string, fallback: string): string {
  return process.env[runKey] ?? process.env[key] ?? fromEnvFile(key) ?? fallback;
}

export const apiPort = port("E2E_API_PORT", "API_PORT", DEFAULT_API_PORT);
export const webPort = port("E2E_WEB_PORT", "WEB_PORT", DEFAULT_WEB_PORT);
export const apiUrl = (process.env.NEXT_PUBLIC_API_URL ?? `http://localhost:${apiPort}`).replace(/\/$/, "");
