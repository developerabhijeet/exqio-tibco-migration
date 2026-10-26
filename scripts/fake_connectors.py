"""Start the fake GitHub, Confluence, Jira and Figma servers on fixed loopback ports, for the e2e
run and manual tries. Writes the token files to a temp directory and prints the settings to use
(``CONNECTORS`` plus the ``*_FILE`` variables). Stops on Ctrl-C.

    python scripts/fake_connectors.py --repo gmf/svc=samples/synthetic-gmf --ports github=9711,confluence=9712,jira=9713,figma=9714,figma-images=9715

``--token-dir`` writes the token files to a fixed directory (``github``, ``atlassian``, ``figma``),
which is how ``web/playwright.config.ts`` hands them to the e2e API.
"""
from __future__ import annotations

import argparse
import json
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from api.tests.fakes.confluence import FakeConfluence
from api.tests.fakes.figma import FakeFigma, images_server
from api.tests.fakes.github import FakeGitHub
from api.tests.fakes.jira import FakeJira

DEFAULT_PORTS = "github=9711,confluence=9712,jira=9713,figma=9714,figma-images=9715"
#: Seeded when no ``--repo`` is given: ``gmf/credit`` is where the e2e run pushes its build.
DEFAULT_REPOS = ["gmf/svc", "gmf/credit"]


def parse_ports(text: str) -> dict[str, int]:
    ports = {k: int(v) for k, v in (item.split("=") for item in text.split(",") if item)}
    unknown = set(ports) - {"github", "confluence", "jira", "figma", "figma-images"}
    if unknown:
        raise SystemExit(f"unknown service(s): {', '.join(sorted(unknown))}")
    return ports


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--ports", default=DEFAULT_PORTS)
    parser.add_argument("--flavor", choices=("datacenter", "cloud"), default="datacenter")
    parser.add_argument("--repo", action="append", default=[], metavar="OWNER/NAME[=DIR]",
                        help="seed a GitHub repository (main branch from DIR, else empty); repeatable "
                             f"(default: {', '.join(DEFAULT_REPOS)})")
    parser.add_argument("--space", default="GMF", help="the Confluence space key to seed")
    parser.add_argument("--token-dir", type=Path, default=None,
                        help="where to write the token files (default: a new temp directory)")
    args = parser.parse_args(argv)
    ports = parse_ports(args.ports)

    images = images_server()
    images.port = ports.get("figma-images", 0) or images.port
    github, confluence = FakeGitHub(), FakeConfluence(args.flavor)
    jira, figma = FakeJira(args.flavor), FakeFigma(images)
    for service, name in ((github, "github"), (confluence, "confluence"), (jira, "jira"), (figma, "figma")):
        if name in ports:
            service.server.port = ports[name]
    # The token files first: a caller that waits for a port (the e2e run) finds them in place.
    tokens = args.token_dir or Path(tempfile.mkdtemp(prefix="fake-connectors-"))
    tokens.mkdir(parents=True, exist_ok=True)
    files = {"GITHUB_TOKEN_FILE": ("github", github.token), "ATLASSIAN_TOKEN_FILE": ("atlassian", confluence.token),
             "FIGMA_TOKEN_FILE": ("figma", figma.token)}
    if args.flavor == "cloud":
        files["ATLASSIAN_USER_FILE"] = ("atlassian-user", confluence.user)
    env = {}
    for var, (name, value) in files.items():
        (tokens / name).write_text(value, encoding="utf-8")
        env[var] = str(tokens / name)

    servers = [images, github.server, confluence.server, jira.server, figma.server]
    for srv in servers:
        srv.start()

    for spec in args.repo or DEFAULT_REPOS:
        name, _, tree = spec.partition("=")
        github.add_repo(name, tree=Path(tree) if tree else None)
    parent = confluence.create_page(args.space, "ExiQO", "<p>ExiQO pages live under this page.</p>")

    connectors = {
        "github": {"enabled": True, "baseUrl": github.url, "apiUrl": f"{github.url}/api/v3",
                   "allowedHosts": [github.host], "commitEmail": "exiqo-bot@example.org"},
        "atlassian": {"enabled": True, "flavor": args.flavor, "confluenceUrl": confluence.url,
                      "jiraUrl": jira.url, "allowedHosts": [confluence.host, jira.host]},
        "figma": {"enabled": True, "apiUrl": figma.url, "webUrl": "https://www.figma.example",
                  "allowedHosts": [figma.host], "imageHosts": [images.host]},
    }
    print(f"CONNECTORS='{json.dumps(connectors)}'")
    print("CONNECTORS_ALLOW_INSECURE_LOOPBACK=true")
    for var, path in env.items():
        print(f"{var}={path}")
    print("GITHUB_REPOS=" + ",".join(r.partition("=")[0] for r in args.repo or DEFAULT_REPOS))
    print(f"CONFLUENCE_SPACE={args.space} CONFLUENCE_PARENT_PAGE_ID={parent} JIRA_PROJECT={jira.project}")
    print("Fake connectors are up; Ctrl-C to stop.", flush=True)
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        pass
    finally:
        for srv in servers:
            srv.stop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
