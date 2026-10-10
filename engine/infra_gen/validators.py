"""Validation of the generated infrastructure (design spec section 8).

Checks, in order: ``terraform fmt -check -recursive``; per module ``terraform init -backend=false``
then ``terraform validate``; ``terragrunt hcl fmt --check`` (``hclfmt --check`` before Terragrunt 0.78)
over ``infra/live``. ``InfraValidation.ok`` is true only when every tool was present and every check
ran and passed. A check that could not run is ``None`` and the reason is in ``notes``.

``terraform init`` downloads the azurerm provider (and kubernetes for ``aks_workload``) from the
provider registry. When the registry is unreachable (an air-gapped build agent, an egress allow list)
init fails, validate is skipped for the module and ``registry_unreachable`` is set so callers can
tell "cannot verify here" from "the module is wrong". To validate offline, point
``TF_CLI_CONFIG_FILE`` at a CLI configuration with a ``provider_installation { filesystem_mirror {...} }``
block (or set ``TF_PLUGIN_CACHE_DIR`` to a populated cache); both are inherited by the subprocesses.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

from pydantic import BaseModel, Field

LOG_LIMIT = 4000
TIMEOUT_S = 300
_ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")
# Only failures that mean "the provider registry or network cannot be reached". A bare "Forbidden" or
# "403" is deliberately not here: it can come from anywhere (a bad module source, a private registry
# credential) and must not hide a real configuration error behind "cannot verify here".
_REGISTRY_FAILURE = re.compile(
    r"could not connect to registry|"
    r"registry\.(?:terraform\.io|opentofu\.org)|"
    r"failed to request discovery document|"
    r"Failed to query available provider packages|Failed to resolve provider packages|"
    r"dial tcp|no such host|i/o timeout|connection refused|network is unreachable",
    re.IGNORECASE,
)


class ModuleValidation(BaseModel):
    name: str
    init_ok: bool | None = None
    validate_ok: bool | None = None
    log: str = ""
    registry_unreachable: bool = False


class InfraValidation(BaseModel):
    fmt_ok: bool | None = None
    modules: list[ModuleValidation] = Field(default_factory=list)
    hclfmt_ok: bool | None = None
    tools_present: dict[str, bool] = Field(default_factory=dict)
    ok: bool = False
    registry_unreachable: bool = False
    notes: list[str] = Field(default_factory=list)


def _present(tool: str | None) -> bool:
    return bool(tool) and (Path(str(tool)).exists() or shutil.which(str(tool)) is not None)


def _run(argv: list[str], cwd: Path, env: dict[str, str]) -> tuple[int, str]:
    try:
        proc = subprocess.run(
            argv, cwd=str(cwd), env=env, capture_output=True, text=True, timeout=TIMEOUT_S, check=False
        )
    except subprocess.TimeoutExpired:
        return 124, f"timed out after {TIMEOUT_S}s: {' '.join(argv)}"
    except OSError as exc:
        return 127, f"could not run {argv[0]}: {exc}"
    text = _ANSI.sub("", (proc.stdout or "") + (proc.stderr or "")).strip()
    return proc.returncode, text


def _tail(text: str) -> str:
    return text if len(text) <= LOG_LIMIT else "..." + text[-LOG_LIMIT:]


def _infra_dir(root: Path) -> Path:
    """``root`` is the solution root; accept the ``infra`` folder itself too."""
    if (root / "infra").is_dir():
        return root / "infra"
    return root


def _terragrunt_fmt_args(terragrunt: str, cwd: Path, env: dict[str, str]) -> list[str]:
    """``hcl fmt --check`` on Terragrunt 0.78+ (``hclfmt`` was removed in 1.0), else ``hclfmt --check``."""
    rc, _ = _run([terragrunt, "hcl", "fmt", "--help"], cwd, env)
    return ["hcl", "fmt", "--check"] if rc == 0 else ["hclfmt", "--check"]


def _validate_modules(
    terraform: str, modules_dir: Path, env: dict[str, str], result: InfraValidation
) -> None:
    names = sorted(p.name for p in modules_dir.iterdir() if p.is_dir()) if modules_dir.is_dir() else []
    if not names:
        result.notes.append(f"no modules found under {modules_dir}")
        return
    # Work on a copy: init writes .terraform and lock files that must not land in the solution.
    with tempfile.TemporaryDirectory(prefix="exiqo-tfvalidate-") as tmp:
        work = Path(tmp) / "modules"
        shutil.copytree(modules_dir, work, ignore=shutil.ignore_patterns(".terraform", ".terraform.lock.hcl"))
        run_env = dict(env)
        run_env.setdefault("TF_PLUGIN_CACHE_DIR", str(Path(tmp) / "plugin-cache"))
        Path(run_env["TF_PLUGIN_CACHE_DIR"]).mkdir(parents=True, exist_ok=True)
        run_env.setdefault("TF_PLUGIN_CACHE_MAY_BREAK_DEPENDENCY_LOCK_FILE", "true")
        first_failure: str | None = None
        for name in names:
            mod = ModuleValidation(name=name)
            if first_failure is not None:
                mod.init_ok = False
                mod.registry_unreachable = True
                mod.log = f"init skipped: provider registry unreachable (same failure as module {first_failure})"
                result.modules.append(mod)
                continue
            rc, log = _run([terraform, "init", "-backend=false", "-input=false", "-no-color"],
                           work / name, run_env)
            mod.init_ok = rc == 0
            if not mod.init_ok:
                mod.registry_unreachable = bool(_REGISTRY_FAILURE.search(log))
                mod.log = _tail(log)
                if mod.registry_unreachable:
                    first_failure = name
                result.modules.append(mod)
                continue
            rc, vlog = _run([terraform, "validate", "-no-color"], work / name, run_env)
            mod.validate_ok = rc == 0
            mod.log = _tail(vlog)
            result.modules.append(mod)


def validate_infra(root: Path, terraform: str | None, terragrunt: str | None) -> InfraValidation:
    """Run the infrastructure checks over ``root/infra`` with the given tool paths (``None`` = absent)."""
    root = Path(root)
    infra = _infra_dir(root)
    env = dict(os.environ)
    env.update({"TF_IN_AUTOMATION": "1", "TF_INPUT": "0", "CHECKPOINT_DISABLE": "1"})
    result = InfraValidation(
        tools_present={"terraform": _present(terraform), "terragrunt": _present(terragrunt)}
    )

    if result.tools_present["terraform"]:
        assert terraform is not None
        rc, log = _run([terraform, "fmt", "-check", "-recursive", "-no-color"], infra, env)
        result.fmt_ok = rc == 0
        if not result.fmt_ok:
            result.notes.append(f"terraform fmt -check failed: {_tail(log)}")
        _validate_modules(terraform, infra / "modules", env, result)
        if any(m.registry_unreachable for m in result.modules):
            result.registry_unreachable = True
            result.notes.append(
                "terraform init could not download providers (registry unreachable): module "
                "validate did not run. fmt and hclfmt checks are still meaningful."
            )
        for m in result.modules:
            if m.init_ok is False and not m.registry_unreachable:
                result.notes.append(f"{m.name}: terraform init failed: {_tail(m.log)}")
            elif m.validate_ok is False:
                result.notes.append(f"{m.name}: terraform validate failed: {_tail(m.log)}")
    else:
        result.notes.append("terraform (or tofu) not found: fmt, init and validate were not run")

    if result.tools_present["terragrunt"]:
        assert terragrunt is not None
        live = infra / "live"
        args = _terragrunt_fmt_args(terragrunt, live if live.is_dir() else infra, env)
        rc, log = _run([terragrunt, *args], live if live.is_dir() else infra, env)
        result.hclfmt_ok = rc == 0
        if not result.hclfmt_ok:
            result.notes.append(f"terragrunt {' '.join(args)} failed: {_tail(log)}")
    else:
        result.notes.append("terragrunt not found: hclfmt --check was not run")

    result.ok = bool(
        all(result.tools_present.values())
        and result.fmt_ok is True
        and result.hclfmt_ok is True
        and result.modules
        and all(m.init_ok is True and m.validate_ok is True for m in result.modules)
    )
    return result
