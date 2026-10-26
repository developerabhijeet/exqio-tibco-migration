"""GitHub (Enterprise Server, or Enterprise Cloud / github.com at the ``apiUrl`` GMF configures):
the connection test, branches, a shallow clone, pushing a build to an ``exiqo/`` branch and the
pull request for it.

REST goes through the allowlisting ``ConnectorClient`` (``Authorization: Bearer``); clone and push
go through the hermetic ``git`` CLI (``gitcli``) to ``<baseUrl>/<owner>/<repo>.git``, a URL whose
host is checked against the same allowlist first.

A push builds a fresh repository in a temporary folder (removed afterwards): the base branch is
fetched shallowly, the build's shipped files (``build_tree_files``: regular files only, symbolic
links not followed, no ``.git`` path, the download's exclusions) are written over it, and the
commit is pushed with ``--force-with-lease`` to a branch under ``exiqo/``, never anywhere else.
"""
from __future__ import annotations

import asyncio
import os
import re
import shutil
import tempfile
import uuid
from collections.abc import Iterable, Mapping
from pathlib import Path, PurePosixPath
from typing import Any, cast
from urllib.parse import quote, urlsplit

import httpx

from api.app.connectors.base import ConnectionTest, ConnectorError
from api.app.connectors.config import GithubInstall
from api.app.connectors.gitcli import git_env, run_git
from api.app.connectors.http import ConnectorClient, header_secrets, redact
from api.app.connectors.registry import BaseConnector, register
from api.app.services.build_tree import DOWNLOAD_EXCLUDED, build_tree_files

PUSH_PREFIX = "exiqo/"
PUSH_REFUSED = "pushes go to exiqo/ branches only"
MAX_BRANCHES = 1000
PER_PAGE = 100
GIT_TIMEOUT_S = 300
RATE_LIMIT_OFF = "rate limiting disabled on this server"
DEFAULT_COMMIT_EMAIL = "exiqo-migrate@invalid"  # RFC 2606: no vendor address in GMF's history
API_HEADERS = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}

_REPO = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,99}/[A-Za-z0-9_.][A-Za-z0-9_.-]{0,99}")
_BRANCH_CHARS = re.compile(r"[A-Za-z0-9._/+-]+")
_LINK_NEXT = re.compile(r'<([^>]+)>\s*;\s*rel="next"')
_EXECUTABLE = (".sh",)


#: Files of the base branch a mirrored push keeps (the repository's own, not the build's): the
#: workflows folder, and README*/LICENSE* at the root. A pull ignores them when they are not the build's.
MIRROR_KEEP_PREFIXES = (".github/",)
MIRROR_KEEP_ROOT = ("README", "LICENSE")
BRANCH_MOVED = "the branch has commits since the last push: pull the reviewer's edits first"


class BranchMoved(ConnectorError):
    """The push branch's head is not the commit the push was leased on (HTTP 409)."""

    def __init__(self, branch: str) -> None:
        super().__init__(f"{branch}: {BRANCH_MOVED}", 409)


def repository_kept(path: str) -> bool:
    """A file of the repository itself that a mirrored push keeps from the base branch."""
    if path.startswith(MIRROR_KEEP_PREFIXES):
        return True
    return "/" not in path and path.upper().startswith(MIRROR_KEEP_ROOT)


def valid_repo(repo: str) -> bool:
    """``owner/name``, GitHub's characters only (nothing a URL or a git option could hide in)."""
    if not _REPO.fullmatch(repo or ""):
        return False
    name = repo.split("/")[1]
    return name not in (".", "..") and not name.lower().endswith(".git")


def valid_branch(name: str) -> bool:
    """A branch name git accepts and no command line can mistake for an option."""
    if not name or not _BRANCH_CHARS.fullmatch(name) or len(name) > 200:
        return False
    if name.startswith(("-", "/", ".")) or name.endswith(("/", ".", ".lock")):
        return False
    return not any(bad in name for bad in ("..", "//", "/.", "@{"))


def valid_push_branch(name: str) -> bool:
    return valid_branch(name) and name.startswith(PUSH_PREFIX) and len(name) > len(PUSH_PREFIX)


def slug(text: str) -> str:
    """``AccountLookup v2`` -> ``accountlookup-v2`` (branch names)."""
    return re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")[:60].strip("-") or "process"


def default_push_branch(process_name: str, build_id: str) -> str:
    return f"{PUSH_PREFIX}{slug(process_name)}/build-{build_id[:8]}"


def _check_repo(repo: str) -> None:
    if not valid_repo(repo):
        raise ValueError(f"not a repository name (owner/name): {repo!r}")


def _check_branch(branch: str) -> None:
    if not valid_branch(branch):
        raise ValueError(f"not a branch name: {branch!r}")


def _git_parts(rel: str) -> tuple[str, ...]:
    """The parts of a relative posix path to write into the repository; ``ValueError`` for an
    absolute path, ``..``, or any ``.git`` component (in any case: macOS and Windows fold it)."""
    path = PurePosixPath(rel)
    parts = path.parts
    if not parts or path.is_absolute() or any(p in ("", ".", "..") or p.lower() == ".git" for p in parts):
        raise ValueError(f"refused to write {rel!r} into the repository")
    return parts


def _read_regular(path: Path) -> bytes:
    """A regular file's bytes, refusing a symbolic link even if one replaced the file since it
    was listed."""
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(fd, "rb") as fh:
        return fh.read()


def _write_into(repo_dir: Path, rel: str, data: bytes) -> None:
    """Write ``data`` at ``rel`` inside ``repo_dir`` and nowhere else: a symbolic link (or a file)
    the base branch has where a folder or the file goes is replaced, never followed."""
    parts = _git_parts(rel)
    current = repo_dir
    for part in parts[:-1]:
        nxt = current / part
        if nxt.is_symlink() or (nxt.exists() and not nxt.is_dir()):
            nxt.unlink()
        if not nxt.exists():
            nxt.mkdir()
        current = nxt
    target = current / parts[-1]
    if target.is_symlink():
        target.unlink()
    elif target.is_dir():
        shutil.rmtree(target)
    mode = 0o755 if target.suffix in _EXECUTABLE else 0o644
    fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, mode)
    with os.fdopen(fd, "wb") as fh:
        fh.write(data)
    os.chmod(target, mode, follow_symlinks=False)


def _error_message(response: httpx.Response) -> str:
    try:
        data = response.json()
    except ValueError:
        return response.text[:300]
    if not isinstance(data, dict):
        return str(data)[:300]
    errors = "; ".join(str(e.get("message") or e) for e in data.get("errors") or [] if e)
    return f"{data.get('message', '')}{': ' + errors if errors else ''}"[:500]


class GitHubConnector(BaseConnector):
    # ---------------------------------------------------------------- configuration
    @property
    def base_url(self) -> str:
        return self.config.url("base_url")

    @property
    def api_url(self) -> str:
        """``apiUrl`` when set (Enterprise Cloud), else Enterprise Server's ``<baseUrl>/api/v3``."""
        explicit = self.config.url("api_url")
        if explicit:
            return explicit
        if not self.base_url:
            raise ConnectorError("the GitHub connector has no base URL (connectors.github.baseUrl)")
        return f"{self.base_url}/api/v3"

    def remote_url(self, repo: str) -> str:
        """The git URL of ``repo``, refused (``HostNotAllowed``) unless its host is allowlisted."""
        _check_repo(repo)
        if not self.base_url:
            raise ConnectorError("the GitHub connector has no base URL (connectors.github.baseUrl)")
        url = f"{self.base_url}/{repo}.git"
        self.client(self.base_url).check(url)
        return url

    def commit_identity(self) -> tuple[str, str]:
        email = cast(GithubInstall, self.config.install).commit_email or DEFAULT_COMMIT_EMAIL
        return f"{self.settings.brand_name} Migrate", email

    def _git_env(self) -> dict[str, str]:
        return git_env(self.credential(), allow_http_loopback=self.config.allow_insecure_loopback,
                       remote_root=self.base_url, ca_bundle=self.settings.connectors_ca_bundle or None)

    # ---------------------------------------------------------------- REST
    def _api(self) -> ConnectorClient:
        return self.client(self.api_url)

    async def _call(self, method: str, path_or_url: str, **kw: Any) -> httpx.Response:
        """One REST call; ``ConnectorError`` (redacted, with the status) for an error answer."""
        self.ready()
        response = await self._api().request(method, path_or_url, headers=API_HEADERS, **kw)
        if response.status_code >= 400:
            secrets = header_secrets(self.auth_header())
            where = f"{method} {response.request.url.path}"
            raise ConnectorError(redact(f"GitHub {where}: HTTP {response.status_code}: "
                                        f"{_error_message(response)}", secrets), response.status_code)
        return response

    async def test(self) -> ConnectionTest:
        user = await self._call("GET", "/user")
        login = (user.json() or {}).get("login")
        scopes = [s.strip() for s in user.headers.get("x-oauth-scopes", "").split(",") if s.strip()]
        notes = []
        if accepted := user.headers.get("x-accepted-github-permissions", "").strip():
            notes.append(f"accepted permissions: {accepted}")
        try:  # best effort: GitHub Enterprise Server answers 404 when rate limiting is off
            core = ((await self._call("GET", "/rate_limit")).json() or {}).get("resources", {}).get("core", {})
        except ConnectorError:
            core = None
        if not core or "remaining" not in core:
            notes.append(RATE_LIMIT_OFF)
        else:
            notes.append(f"rate limit: {core['remaining']} of {core.get('limit', '?')} requests left")
        return ConnectionTest(ok=True, account=login, site=self.base_url or self.api_url, scopes=scopes,
                              message="; ".join(notes))

    async def repository(self, repo: str) -> dict[str, Any]:
        _check_repo(repo)
        return (await self._call("GET", f"/repos/{repo}")).json()

    async def default_branch(self, repo: str) -> str:
        return str((await self.repository(repo)).get("default_branch") or "main")

    async def base_of(self, repo: str, base: str | None) -> tuple[str, bool]:
        """The base branch (``base``, else the default branch) and whether it exists. A new,
        empty repository has none (GitHub answers 409 "Git Repository is empty", or 404 for its
        default branch): the push is then its first commit. ``size`` is not used: GitHub computes
        it lazily and a small repository with commits can still report 0."""
        info = await self.repository(repo)
        default = str(info.get("default_branch") or "main")
        branch = base or default
        try:
            await self.head(repo, branch)
        except ConnectorError as exc:
            if exc.status == 409 or (exc.status == 404 and branch == default):
                return branch, False
            raise
        return branch, True

    async def head(self, repo: str, branch: str) -> str:
        _check_repo(repo)
        _check_branch(branch)
        data = (await self._call("GET", f"/repos/{repo}/branches/{quote(branch, safe='/')}")).json()
        return str(data["commit"]["sha"])

    async def list_branches(self, repo: str) -> list[str]:
        """Every branch name (``Link`` pagination, 100 a page), at most 1 000."""
        _check_repo(repo)
        names: list[str] = []
        url: str | None = f"/repos/{repo}/branches"
        params: dict[str, Any] | None = {"per_page": PER_PAGE}
        while url and len(names) < MAX_BRANCHES:
            response = await self._call("GET", url, params=params)
            names += [str(b["name"]) for b in response.json() or []]
            match = _LINK_NEXT.search(response.headers.get("link", ""))
            url, params = (match.group(1) if match else None), None  # the next link carries its query
        return names[:MAX_BRANCHES]

    def trusted_link(self, url: object) -> str:
        """``url`` when it is an https link (http only for the test loopback) on exactly the
        configured GitHub host, without credentials; else ``""``: a link the API returned is never
        stored or shown on trust."""
        base, got = urlsplit(self.base_url), urlsplit(str(url or ""))
        secure = got.scheme == "https" or (got.scheme == "http" and self.settings.connectors_allow_insecure_loopback)
        if (not secure or got.scheme != base.scheme or got.username or got.password or not got.hostname
                or got.netloc.lower() != base.netloc.lower()):
            return ""
        return str(url)

    async def _open_pull_for(self, repo: str, head: str) -> dict[str, Any] | None:
        owner = repo.split("/")[0]
        rows = (await self._call("GET", f"/repos/{repo}/pulls",
                                 params={"head": f"{owner}:{head}", "state": "open", "per_page": PER_PAGE})).json()
        return rows[0] if rows else None

    async def open_pull_request(self, repo: str, head: str, base: str, title: str, body: str) -> dict[str, Any]:
        """The open pull request of ``head`` into ``base``: the existing one for ``head`` (its body
        updated) or a new one. ``{"number", "url"}``."""
        _check_repo(repo)
        _check_branch(head)
        _check_branch(base)
        pull = await self._open_pull_for(repo, head)
        if pull is None:
            try:
                pull = (await self._call("POST", f"/repos/{repo}/pulls",
                                         json={"title": title, "head": head, "base": base, "body": body})).json()
                return {"number": pull["number"], "url": self.trusted_link(pull.get("html_url"))}
            except ConnectorError as exc:  # opened in between: GitHub answers 422 "already exists"
                if exc.status != 422 or "already exists" not in exc.message:
                    raise
                pull = await self._open_pull_for(repo, head)
                if pull is None:
                    raise
        patch: dict[str, Any] = {"body": body}
        if (pull.get("base") or {}).get("ref") != base:  # opened against another base: retarget it
            patch["base"] = base
        pull = (await self._call("PATCH", f"/repos/{repo}/pulls/{pull['number']}", json=patch)).json()
        return {"number": pull["number"], "url": self.trusted_link(pull.get("html_url"))}

    # ---------------------------------------------------------------- git
    async def clone(self, repo: str, branch: str, dest: Path, *, depth: int = 1, timeout_s: int = GIT_TIMEOUT_S) -> str:
        """A shallow, single-branch, tag-less clone of ``branch`` into ``dest`` (absent or empty);
        the commit it checked out."""
        _check_branch(branch)
        self.ready()
        url = self.remote_url(repo)
        dest = Path(dest)
        if dest.exists() and (not dest.is_dir() or any(dest.iterdir())):
            raise ValueError(f"clone destination {dest} is not empty")
        dest.parent.mkdir(parents=True, exist_ok=True)
        env = self._git_env()

        def run() -> str:
            run_git(["clone", "--quiet", "--depth", str(max(1, int(depth))), "--single-branch",
                     "--branch", branch, "--no-tags", "--", url, str(dest)], dest.parent, env, timeout_s)
            return run_git(["rev-parse", "HEAD"], dest, env, timeout_s).strip()

        return await asyncio.to_thread(run)

    async def push_build(self, build_root: Path, repo: str, branch: str, message: str, *,
                         base_branch: str | None = None, extra_files: Mapping[str, bytes] | None = None,
                         excluded: Iterable[str] = DOWNLOAD_EXCLUDED, timeout_s: int = GIT_TIMEOUT_S,
                         lease: str | None = None, mirror: bool = False) -> str:
        """Commit the build's shipped files (plus ``extra_files``, e.g. ``EXPORTED_FROM.md``) on
        top of ``base_branch`` (or as a first commit) and push it to ``branch`` (``exiqo/...``
        only) with ``--force-with-lease``. The pushed commit.

        ``lease``: the commit the branch must still be at (``""``: it must not exist); a branch that
        moved raises :class:`BranchMoved` and nothing is pushed. Without it the lease is the head
        read just before the push (sub-project 7's per-build branches, which nobody commits to).
        ``mirror``: the commit holds exactly the build's files (and ``extra_files``): files of the
        base branch the build does not ship are removed, except the repository's own
        (:func:`repository_kept`)."""
        if not valid_push_branch(branch):
            raise ValueError(PUSH_REFUSED)
        if base_branch is not None:
            _check_branch(base_branch)
        extras = dict(extra_files or {})
        for rel in extras:
            _git_parts(rel)
        self.ready()
        url = self.remote_url(repo)
        env = {**self._git_env()}
        name, email = self.commit_identity()
        env.update({"GIT_AUTHOR_NAME": name, "GIT_AUTHOR_EMAIL": email,
                    "GIT_COMMITTER_NAME": name, "GIT_COMMITTER_EMAIL": email})
        root = Path(build_root)
        skip = frozenset(excluded)

        def run() -> str:
            work = Path(tempfile.gettempdir()) / f"exiqo-push-{uuid.uuid4().hex}"
            work.mkdir(mode=0o700)
            try:
                def git(*args: str) -> str:
                    return run_git(list(args), work, env, timeout_s)

                git("init", "--quiet", "--initial-branch", branch)
                git("remote", "add", "origin", url)
                if base_branch:
                    git("fetch", "--quiet", "--depth", "1", "--no-tags", "origin", f"refs/heads/{base_branch}")
                    git("checkout", "--quiet", "-B", branch, "FETCH_HEAD")
                remote = git("ls-remote", "--heads", "origin", f"refs/heads/{branch}").split()
                head = remote[0] if remote else ""  # empty: the branch does not exist yet
                if lease is not None and head != lease:
                    raise BranchMoved(branch)
                expected = head if lease is None else lease
                shipped = build_tree_files(root, skip)
                if mirror and base_branch:
                    keep = set(shipped) | set(extras)
                    tracked = [p for p in git("ls-files", "-z").split("\0") if p]
                    stale = [p for p in tracked if p not in keep and not repository_kept(p)]
                    for i in range(0, len(stale), 200):
                        git("rm", "--quiet", "--cached", "-r", "--", *stale[i:i + 200])
                    for rel in stale:
                        (work / rel).unlink(missing_ok=True)
                for rel in shipped:
                    _write_into(work, rel, _read_regular(root / rel))
                for rel, data in extras.items():
                    _write_into(work, rel, data)
                git("add", "--all", "--force")
                git("commit", "--quiet", "--no-verify", "--allow-empty", "-m", message)
                git("push", "--quiet", "--no-verify", f"--force-with-lease=refs/heads/{branch}:{expected}",
                    "origin", f"HEAD:refs/heads/{branch}")
                return git("rev-parse", "HEAD").strip()
            finally:
                shutil.rmtree(work, ignore_errors=True)

        return await asyncio.to_thread(run)


register("github", GitHubConnector)
