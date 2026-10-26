import { defineConfig, devices } from "@playwright/test";
import { execFileSync } from "node:child_process";
import { existsSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { apiPort as devApiPort, apiUrl as devApiUrl, webPort as devWebPort } from "./scripts/root-env";

// Chromium is preinstalled under PLAYWRIGHT_BROWSERS_PATH; use its binary when the pinned
// @playwright/test expects a different build.
const preinstalled = "/opt/pw-browsers/chromium";
const executablePath = process.env.PW_CHROMIUM ?? (existsSync(preinstalled) ? preinstalled : undefined);

// Ports: `make e2e` picks two free ports (E2E_API_PORT, E2E_WEB_PORT) so a server left running on
// the dev ports by something else is never mistaken for this run's. Playwright starts both servers
// and refuses a busy port instead of reusing whatever answers there; E2E_REUSE_SERVERS=1 reuses
// servers you started yourself (they must run this checkout, the fake provider and a migrated DB).
// Without those variables the dev ports come from scripts/root-env.ts (the repo-root .env).
const apiPort = Number(devApiPort);
const apiUrl = devApiUrl;
const webPort = Number(devWebPort);
const reuse = process.env.E2E_REUSE_SERVERS === "1";

// The connector fakes (scripts/fake_connectors.py) on free loopback ports, for Settings →
// Connectors. The ports and the token directory are chosen once, in the runner, and kept in the
// environment so the worker processes that load this file again see the same values.
// E2E_FAKE_PORTS ("github=…,confluence=…,jira=…,figma=…,figma-images=…") pins them instead.
const FAKE_SERVICES = ["github", "confluence", "jira", "figma", "figma-images"] as const;
if (!process.env.E2E_FAKE_PORTS) {
  const probe =
    "const net=require('net');const s=[];let n=" + FAKE_SERVICES.length + ";" +
    "for(let i=0;i<" + FAKE_SERVICES.length + ";i++){const x=net.createServer();s.push(x);x.listen(0,'127.0.0.1',()=>{" +
    "if(--n===0){process.stdout.write(s.map(y=>y.address().port).join(','));s.forEach(y=>y.close());}});}";
  const free = execFileSync(process.execPath, ["-e", probe]).toString().split(",");
  process.env.E2E_FAKE_PORTS = FAKE_SERVICES.map((name, i) => `${name}=${free[i]}`).join(",");
}
process.env.E2E_FAKE_TOKENS ??= join(tmpdir(), `exiqo-e2e-fakes-${process.pid}`);
const fakePorts = Object.fromEntries(process.env.E2E_FAKE_PORTS.split(",").map((p) => p.split("="))) as Record<
  (typeof FAKE_SERVICES)[number],
  string
>;
const fakeTokens = process.env.E2E_FAKE_TOKENS;
const fakeHost = (name: (typeof FAKE_SERVICES)[number]) => `127.0.0.1:${fakePorts[name]}`;
// The install layer (the chart's connectors.* values): GitHub and Atlassian (Data Center) on the
// fakes, Figma left off, so the page shows both install states.
const CONNECTORS = {
  github: {
    enabled: true,
    baseUrl: `http://${fakeHost("github")}`,
    apiUrl: `http://${fakeHost("github")}/api/v3`,
    allowedHosts: [fakeHost("github")],
    commitEmail: "exiqo-bot@example.org",
  },
  atlassian: {
    enabled: true,
    flavor: "datacenter",
    confluenceUrl: `http://${fakeHost("confluence")}`,
    jiraUrl: `http://${fakeHost("jira")}`,
    allowedHosts: [fakeHost("confluence"), fakeHost("jira")],
  },
  figma: { enabled: false },
};

export default defineConfig({
  testDir: "./e2e",
  globalSetup: "./e2e/global-setup.ts",
  timeout: 120_000,
  expect: { timeout: 20_000 },
  fullyParallel: false,
  workers: 1,
  reporter: [["list"]],
  use: {
    baseURL: `http://localhost:${webPort}`,
    trace: "retain-on-failure",
    viewport: { width: 1440, height: 1000 },
    launchOptions: executablePath ? { executablePath } : {},
  },
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"], viewport: { width: 1440, height: 1000 } } }],
  webServer: [
    {
      // The connector fakes; the token files are written before the ports open.
      command: `.venv/bin/python scripts/fake_connectors.py --ports ${process.env.E2E_FAKE_PORTS} --token-dir "${fakeTokens}"`,
      cwd: "..",
      // Unauthenticated, the fake answers 401, which Playwright counts as up.
      url: `http://${fakeHost("github")}/api/v3/user`,
      reuseExistingServer: reuse,
      timeout: 30_000,
    },
    {
      // The API with the fake provider; DATABASE_URL must point at a migrated database (make db).
      command: `.venv/bin/uvicorn api.app.main:app --port ${apiPort}`,
      cwd: "..",
      url: `${apiUrl}/health`,
      reuseExistingServer: reuse,
      timeout: 60_000,
      env: {
        PROVIDER: process.env.PROVIDER ?? "fake",
        // Offline and deterministic: fastembed would try to download its model on first use.
        EMBEDDINGS_BACKEND: process.env.EMBEDDINGS_BACKEND ?? "hash",
        DATABASE_URL: process.env.DATABASE_URL ?? "postgresql+asyncpg://exiqo:exiqo@localhost:5432/exiqo",
        // The memory graph (`make neo4j`): the scan and every phase are ingested into it, and the
        // /memory explorer reads it.
        NEO4J_URI: process.env.NEO4J_URI ?? "bolt://localhost:7687",
        NEO4J_PASSWORD: process.env.NEO4J_PASSWORD ?? "exiqo-dev-neo4j",
        // Infra validation's `terraform init` needs the provider registry (or a mirror), which a dev
        // machine or CI without one cannot reach: skip init and module validate (recorded as skipped).
        INFRA_VALIDATE_INIT: process.env.INFRA_VALIDATE_INIT ?? "false",
        // The specs assert the fake provider and an unconfigured Claude: a developer's own model
        // credentials must not leak in from the shell.
        CLAUDE_CODE_OAUTH_TOKEN: "",
        AZURE_OPENAI_ENDPOINT: "",
        AZURE_OPENAI_API_KEY: "",
        // The browser calls the API directly: allow this run's web origin.
        CORS_ORIGINS: process.env.CORS_ORIGINS ?? `http://localhost:${webPort}`,
        // Settings → Connectors against the fakes above: http on 127.0.0.1 needs the loopback flag
        // (tests and local fakes only), and the credentials are the fakes' token files.
        CONNECTORS: JSON.stringify(CONNECTORS),
        CONNECTORS_ALLOW_INSECURE_LOOPBACK: "true",
        GITHUB_TOKEN_FILE: join(fakeTokens, "github"),
        ATLASSIAN_TOKEN_FILE: join(fakeTokens, "atlassian"),
        // The API passes CORS_ORIGINS on to the generated service it starts (Cors__Origins__<i>),
        // so the Verify tab's dev-token call from this run's web origin is allowed there too.
      },
    },
    {
      command: process.env.E2E_WEB_CMD ?? `npm run dev -- -p ${webPort}`,
      url: `http://localhost:${webPort}`,
      reuseExistingServer: reuse,
      timeout: 180_000,
      env: { NEXT_PUBLIC_API_URL: apiUrl },
    },
  ],
});
