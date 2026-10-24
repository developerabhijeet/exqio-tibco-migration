"""The GMF guides in docs/deploy stay true to the chart, the Makefile and the code.

A platform engineer follows these guides line by line, and Help & Support serves them in the app,
so a values key, a Make target, a secret name or a file path that no longer exists is a defect.
The checks are mechanical: they read the guides, the chart's JSON schema, the Makefile and the
secret list in api/app/secrets.py, and fail on any name the guides use that those do not have.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parents[2]
DOCS = REPO / "docs" / "deploy"
GUIDES = ("GMF-INSTALL.md", "DEPLOY-STEP-SECRETS.md", "RUNBOOK.md")

# Spec section 3, "Install guide": the eleven sections, in order.
INSTALL_SECTIONS = [
    "1. Prerequisites checklist",
    "2. Mirror images and toolchains",
    "3. Entra app registration and role groups",
    "4. Key Vault secrets",
    "5. Deploy-step identities",
    "6. Values walk-through",
    "7. Install and upgrade",
    "8. Smoke test",
    "9. Backup and restore",
    "10. Secret rotation",
    "11. Troubleshooting",
]

# Topics the brief requires the runbook and the Deploy-step guide to cover (any heading level).
RUNBOOK_TOPICS = ["Scaling workers", "Draining", "A stuck job", "worker lost", "Backups and restore drills",
                  "Secret rotation", "Upgrades and rollback", "/readyz", "/system/status"]
DEPLOY_TOPICS = ["az identity create", "federated credential", "Contributor", "custom role",
                 "deploy.environments", "Key Vault", "Rotation"]

# Files named in the guides that live in GMF's own repository, not in this one.
GMF_REPO_PATHS = ("deploy/gmf/",)
# `<NAME>_FILE` variables that are not app secrets (pipeline and Terraform settings).
OTHER_FILE_VARS = {"COSIGN_PUBLIC_KEY_FILE", "TF_CLI_CONFIG_FILE", "AZURE_FEDERATED_TOKEN_FILE"}

FENCE = re.compile(r"^```(?P<info>[^\n]*)\n(?P<body>.*?)^```[ \t]*$", re.MULTILINE | re.DOTALL)
INLINE = re.compile(r"`([^`\n]+)`")


def read(name: str) -> str:
    return (DOCS / name).read_text(encoding="utf-8")


def prose(text: str) -> str:
    """The text without fenced code blocks."""
    return FENCE.sub("", text)


def inline_spans(text: str) -> list[str]:
    return INLINE.findall(prose(text))


def h2_headings(text: str) -> list[str]:
    return re.findall(r"^## (.+?)\s*$", prose(text), re.MULTILINE)


def schema() -> dict:
    return json.loads((REPO / "deploy/helm/exiqo/values.schema.json").read_text())


def schema_has(path: str) -> bool:
    """Whether the dotted values path names a key the chart's schema (or its Neo4j subchart
    pass-through) knows. A map with free keys (roleGroups, deployIdentities) accepts any name."""
    root = schema()

    def resolve(node: dict) -> dict:
        while "$ref" in node:
            node = root["definitions"][node["$ref"].rsplit("/", 1)[1]]
        return node

    node = root
    for seg in path.replace("[]", "").split("."):
        node = resolve(node)
        if node.get("type") == "array":
            node = resolve(node.get("items", {}))
        props = node.get("properties", {})
        extra = node.get("additionalProperties")
        if seg in props:
            node = props[seg]
        elif isinstance(extra, dict):
            node = extra
        else:
            # A free object, or one that allows more keys (the Neo4j subchart's values), accepts
            # the rest of the path; a strict object does not know the key.
            return extra is not False
    return True


def values_paths(text: str) -> list[str]:
    top = set(schema()["properties"])
    out = []
    for span in inline_spans(text):
        if re.fullmatch(r"[a-z][A-Za-z0-9]*(\.[A-Za-z0-9_-]+(\[\])?)+", span) and span.split(".")[0] in top:
            out.append(span)
    return out


def yaml_key_paths(node, prefix: str = "") -> list[str]:
    paths = []
    if isinstance(node, dict):
        for key, value in node.items():
            here = f"{prefix}.{key}" if prefix else str(key)
            paths.append(here)
            paths.extend(yaml_key_paths(value, here))
    elif isinstance(node, list):
        for item in node:
            paths.extend(yaml_key_paths(item, f"{prefix}[]"))
    return paths


def make_targets() -> set[str]:
    makefile = (REPO / "Makefile").read_text()
    return set(re.findall(r"^([a-z][a-z0-9-]*):", makefile, re.MULTILINE))


def test_guides_exist() -> None:
    for name in GUIDES:
        assert (DOCS / name).is_file(), f"docs/deploy/{name} is missing"


def test_install_guide_has_exactly_the_eleven_sections_in_order() -> None:
    assert h2_headings(read("GMF-INSTALL.md")) == INSTALL_SECTIONS


@pytest.mark.parametrize("topic", RUNBOOK_TOPICS)
def test_runbook_covers(topic: str) -> None:
    assert topic.lower() in read("RUNBOOK.md").lower()


@pytest.mark.parametrize("topic", DEPLOY_TOPICS)
def test_deploy_step_guide_covers(topic: str) -> None:
    assert topic.lower() in read("DEPLOY-STEP-SECRETS.md").lower()


@pytest.mark.parametrize("name", GUIDES)
def test_values_keys_exist_in_the_schema(name: str) -> None:
    text = read(name)
    bad = [p for p in values_paths(text) if not schema_has(p)]
    assert not bad, f"{name} names values keys that values.schema.json does not have: {bad}"


@pytest.mark.parametrize("name", GUIDES)
def test_values_snippets_use_schema_keys(name: str) -> None:
    """A fenced block tagged ``yaml values`` is a values file fragment: every key must exist."""
    top = set(schema()["properties"])
    bad = []
    for m in FENCE.finditer(read(name)):
        if m.group("info").strip() != "yaml values":
            continue
        for path in yaml_key_paths(yaml.safe_load(m.group("body")) or {}):
            if path.split(".")[0].replace("[]", "") not in top or not schema_has(path):
                bad.append(path)
    assert not bad, f"{name}: values snippet keys not in the schema: {bad}"


@pytest.mark.parametrize("name", GUIDES)
def test_make_targets_exist(name: str) -> None:
    text = read(name)
    named = {m.group(1) for span in inline_spans(text) if (m := re.match(r"make ([a-z][a-z0-9-]*)", span))}
    for m in FENCE.finditer(text):
        named |= set(re.findall(r"^\s*(?:\$ )?make ([a-z][a-z0-9-]*)", m.group("body"), re.MULTILINE))
    missing = named - make_targets()
    assert not missing, f"{name} names Make targets the Makefile does not have: {sorted(missing)}"


@pytest.mark.parametrize("name", GUIDES)
def test_repo_paths_exist(name: str) -> None:
    bad = []
    for span in inline_spans(read(name)):
        if not re.match(r"(deploy|docs|scripts|api|web|engine)/", span) or re.search(r"[<>*${}\s]", span):
            continue
        if span.startswith(GMF_REPO_PATHS):
            continue
        if not (REPO / span.rstrip("/")).exists():
            bad.append(span)
    assert not bad, f"{name} names repository paths that do not exist: {bad}"


@pytest.mark.parametrize("name", GUIDES)
def test_secret_file_variables_exist(name: str) -> None:
    from api.app.secrets import SECRET_FILE_FIELDS

    known = {f"{f.upper()}_FILE" for f in SECRET_FILE_FIELDS} | OTHER_FILE_VARS
    bad = [s for s in inline_spans(read(name)) if re.fullmatch(r"[A-Z][A-Z0-9_]*_FILE", s) and s not in known]
    assert not bad, f"{name} names *_FILE variables the app does not read: {bad}"


def test_key_vault_table_lists_every_file_secret_with_owner_and_reader() -> None:
    from api.app.secrets import SECRET_FILE_FIELDS

    text = read("GMF-INSTALL.md")
    section = text.split("## 4. Key Vault secrets", 1)[1].split("\n## 5.", 1)[0]
    rows = [line for line in section.splitlines() if line.startswith("|")]
    assert "Owner" in rows[0] and "Reader" in rows[0], rows[0]
    for field in SECRET_FILE_FIELDS:
        env = field.upper()
        assert any(f"`{env}`" in row for row in rows), f"the Key Vault table has no row for {env}"
    assert any("neo4j-auth" in row or "neo4jAuth" in row for row in rows), "no row for the Neo4j auth object"


@pytest.mark.parametrize("name", GUIDES)
def test_no_vendor_name_in_the_guides(name: str) -> None:
    """The installed product carries no link back to its vendor (and the guides ship in the app)."""
    for number, line in enumerate(read(name).splitlines(), 1):
        lowered = line.lower()
        assert "r systems" not in lowered and "rsystems" not in lowered, f"{name}:{number}: {line}"


def test_demo_check_takes_base_url_and_token() -> None:
    source = (REPO / "scripts" / "demo_check.py").read_text()
    assert '"--base-url"' in source and '"--token"' in source
    guide = read("GMF-INSTALL.md")
    assert "scripts/demo_check.py --base-url" in guide and "--token" in guide


def test_readme_links_the_guides() -> None:
    readme = (REPO / "README.md").read_text()
    for name in GUIDES:
        assert f"docs/deploy/{name}" in readme
