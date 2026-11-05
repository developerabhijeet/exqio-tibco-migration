"""Fake GitHub Enterprise Server: REST under ``/api/v3`` and git smart HTTP at ``/{o}/{r}.git``.
Every request needs ``Authorization: Bearer <token>`` (401 otherwise, git included)."""
from __future__ import annotations

import base64
import json
import os
import shutil
import subprocess
import tempfile
from collections.abc import Callable
from pathlib import Path

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import Response
from starlette.routing import Route

from api.tests.fakes.githttp import wsgi_http_backend
from api.tests.fakes.server import FakeService, json_response

GIT_IDENTITY = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.org",
                "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.org"}
COMMIT_FLAGS = ["-c", "commit.gpgsign=false", "-c", "core.hooksPath=/dev/null"]


def hermetic_git_env() -> dict[str, str]:
    """The environment for every git subprocess of the fakes and their tests: no user or system
    config, no credential or askpass helper (an IDE's would wait for a person), no prompts."""
    env = {k: v for k, v in os.environ.items() if "ASKPASS" not in k}
    return {**env, **GIT_IDENTITY, "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_TERMINAL_PROMPT": "0", "GIT_ASKPASS": "true"}


def git(*args: str, cwd: Path | None = None, input: bytes | None = None) -> bytes:
    proc = subprocess.run(["git", *args], cwd=cwd, input=input, capture_output=True, check=False,
                          env=hermetic_git_env(), timeout=60)
    if proc.returncode:
        raise RuntimeError(f"git {' '.join(args)}: {proc.stderr.decode(errors='replace')}")
    return proc.stdout


class FakeGitHub(FakeService):
    def __init__(self, token: str = "ghs-fake-token") -> None:
        self.token = token
        self.root = Path(tempfile.mkdtemp(prefix="fake-github-"))
        self.pulls: dict[str, list[dict]] = {}
        self.last_repo: str | None = None
        backend = wsgi_http_backend(self.root)
        app = Starlette(routes=[
            Route("/api/v3/user", self._guard(self._user)),
            Route("/api/v3/rate_limit", self._guard(self._rate_limit)),
            Route("/api/v3/repos/{owner}/{repo}", self._guard(self._repo)),
            Route("/api/v3/repos/{owner}/{repo}/branches", self._guard(self._branches)),
            Route("/api/v3/repos/{owner}/{repo}/branches/{branch:path}", self._guard(self._branch)),
            Route("/api/v3/repos/{owner}/{repo}/pulls", self._guard(self._pulls), methods=["GET", "POST"]),
            Route("/api/v3/repos/{owner}/{repo}/pulls/{number:int}", self._guard(self._pull),
                  methods=["GET", "PATCH"]),
            Route("/{owner}/{repo}.git/{rest:path}", self._guard(backend, git_http=True), methods=["GET", "POST"]),
        ])
        self._serve(app)

    def _authorized(self, request: Request, *, basic: bool) -> bool:
        got = request.headers.get("authorization", "")
        if got == f"Bearer {self.token}":
            return True
        if basic and got.startswith("Basic "):  # git over HTTP: x-access-token:<token>
            try:
                return base64.b64decode(got[6:]).decode() == f"x-access-token:{self.token}"
            except ValueError:
                return False
        return False

    def _guard(self, handler, *, git_http: bool = False):
        async def run(request: Request) -> Response:
            if not self._authorized(request, basic=git_http):
                headers = {"WWW-Authenticate": 'Basic realm="GitHub"'} if git_http else {}
                return Response(json.dumps({"message": "Bad credentials"}), 401, headers,
                                media_type="application/json")
            return await handler(request)

        return run

    # ---- REST
    async def _user(self, request: Request) -> Response:
        return json_response({"login": "exiqo-bot"})

    async def _rate_limit(self, request: Request) -> Response:
        return json_response({"resources": {"core": {"limit": 5000, "remaining": 4999, "reset": 0}},
                              "rate": {"limit": 5000, "remaining": 4999, "reset": 0}})

    def _full(self, request: Request) -> str:
        p = request.path_params
        return f"{p['owner']}/{p['repo']}"

    def _git_dir(self, full: str) -> Path:
        return self.root / f"{full}.git"

    def _missing(self) -> Response:
        return json_response({"message": "Not Found"}, 404)

    def _default_branch(self, full: str) -> str:
        return git("symbolic-ref", "--short", "HEAD", cwd=self._git_dir(full)).decode().strip()

    def _branch_names(self, full: str) -> list[str]:
        out = git("for-each-ref", "--format=%(refname:short)", "refs/heads", cwd=self._git_dir(full))
        return out.decode().split()

    def _branch_json(self, full: str, name: str) -> dict:
        sha = git("rev-parse", f"refs/heads/{name}", cwd=self._git_dir(full)).decode().strip()
        return {"name": name, "commit": {"sha": sha}, "protected": False}

    async def _repo(self, request: Request) -> Response:
        full = self._full(request)
        if not self._git_dir(full).exists():
            return self._missing()
        return json_response({"full_name": full, "name": full.split("/")[1], "private": True,
                              "default_branch": self._default_branch(full),
                              "html_url": f"{self.url}/{full}",
                              "clone_url": f"{self.url}/{full}.git",
                              "permissions": {"admin": False, "push": True, "pull": True}})

    def _page(self, request: Request, rows: list[dict]) -> Response:
        """``per_page``/``page`` (default 30, at most 100) and the ``Link`` header GitHub sends."""
        q = request.query_params
        per = min(max(int(q.get("per_page", 30)), 1), 100)
        page = max(int(q.get("page", 1)), 1)
        last = max((len(rows) + per - 1) // per, 1)
        links = []
        base = str(request.url.remove_query_params(["page", "per_page"]))
        sep = "&" if "?" in base else "?"
        for rel, n in (("next", page + 1 if page < last else 0), ("last", last if page < last else 0),
                       ("prev", page - 1 if page > 1 else 0), ("first", 1 if page > 1 else 0)):
            if n:
                links.append(f'<{base}{sep}per_page={per}&page={n}>; rel="{rel}"')
        response = json_response(rows[(page - 1) * per: page * per])
        if links:
            response.headers["Link"] = ", ".join(links)
        return response

    async def _branches(self, request: Request) -> Response:
        full = self._full(request)
        if not self._git_dir(full).exists():
            return self._missing()
        rows = [self._branch_json(full, n) for n in sorted(self._branch_names(full))]
        return self._page(request, rows)

    async def _branch(self, request: Request) -> Response:
        full, name = self._full(request), request.path_params["branch"]
        if not self._git_dir(full).exists() or name not in self._branch_names(full):
            return self._missing()
        return json_response(self._branch_json(full, name))

    async def _pulls(self, request: Request) -> Response:
        full = self._full(request)
        if not self._git_dir(full).exists():
            return self._missing()
        pulls = self.pulls.setdefault(full, [])
        if request.method == "GET":
            state = request.query_params.get("state", "open")
            head = request.query_params.get("head", "")
            rows = [p for p in pulls if state == "all" or p["state"] == state]
            if head:  # GitHub filters by ``owner:branch`` only; a bare branch name matches nothing
                rows = [p for p in rows if f"{full.split('/')[0]}:{p['head']['ref']}" == head]
            return self._page(request, rows)
        body = await request.json()
        owner = full.split("/")[0]
        head, base = str(body.get("head") or ""), str(body.get("base") or "")
        if not body.get("title"):
            return self._invalid("title is missing")
        branches = self._branch_names(full)
        if head.rpartition(":")[2] not in branches:
            return self._invalid(f"head branch {head} does not exist")
        if base not in branches:
            return self._invalid(f"base branch {base} does not exist")
        ref = head.rpartition(":")[2]
        if any(p["state"] == "open" and p["head"]["ref"] == ref and p["base"]["ref"] == base for p in pulls):
            return self._invalid(f"A pull request already exists for {owner}:{ref}.")
        n = len(pulls) + 1
        pull = {"number": n, "title": body["title"], "body": body.get("body", ""), "state": "open",
                "draft": bool(body.get("draft", False)),
                "html_url": f"{self.url}/{full}/pull/{n}",
                "head": {"ref": ref}, "base": {"ref": body["base"]}}
        pulls.append(pull)
        return json_response(pull, 201)

    @staticmethod
    def _invalid(message: str) -> Response:
        return json_response({"message": "Validation Failed", "errors": [{"message": message}]}, 422)

    async def _pull(self, request: Request) -> Response:
        full, n = self._full(request), request.path_params["number"]
        pull = next((p for p in self.pulls.get(full, []) if p["number"] == n), None)
        if pull is None:
            return self._missing()
        if request.method == "PATCH":
            body = await request.json()
            for key in ("title", "body", "state"):
                if key in body:
                    pull[key] = body[key]
        return json_response(pull)

    def stop(self) -> None:
        super().stop()
        shutil.rmtree(self.root, ignore_errors=True)

    # ---- test helpers
    def add_repo(self, full_name: str, branch: str = "main",
                 tree: Path | dict[str, str] | None = None) -> None:
        """A bare repository whose default ``branch`` holds ``tree`` (a directory or
        ``{path: text}``; empty when None) as its first commit."""
        gd = self._git_dir(full_name)
        gd.parent.mkdir(parents=True, exist_ok=True)
        git("init", "--bare", "-b", branch, str(gd))
        with tempfile.TemporaryDirectory(prefix="fake-github-seed-") as tmp:
            work = Path(tmp)
            git("init", "-b", branch, cwd=work)
            if isinstance(tree, Path):
                for src in sorted(p for p in tree.rglob("*") if p.is_file() and ".git" not in p.parts):
                    dst = work / src.relative_to(tree)
                    dst.parent.mkdir(parents=True, exist_ok=True)
                    dst.write_bytes(src.read_bytes())
            elif tree:
                self._write(work, tree)
            git("add", "-A", cwd=work)
            git(*COMMIT_FLAGS, "commit", "--allow-empty", "-m", "initial", cwd=work)
            git("push", str(gd), f"{branch}:{branch}", cwd=work)
        self.last_repo = full_name

    @staticmethod
    def _write(work: Path, files: dict[str, str]) -> None:
        for rel, text in files.items():
            path = work / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")

    def commit(self, branch: str, changes: dict[str, str | Callable[[str], str]],
               repo: str | None = None) -> str:
        """A commit on ``branch`` (created from the default branch when new) writing ``changes``;
        a callable gets the file's current text ("" when absent). Returns the new sha."""
        full = repo or self.last_repo
        if full is None:
            raise ValueError("add_repo first")
        gd = self._git_dir(full)
        with tempfile.TemporaryDirectory(prefix="fake-github-work-") as tmp:
            work = Path(tmp)
            start = branch if branch in self._branch_names(full) else self._default_branch(full)
            git("clone", "--quiet", "--branch", start, str(gd), str(work))
            if start != branch:
                git("checkout", "-b", branch, cwd=work)
            for rel, change in changes.items():
                path = work / rel
                old = path.read_text(encoding="utf-8") if path.exists() else ""
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(change(old) if callable(change) else change, encoding="utf-8")
            git("add", "-A", cwd=work)
            git(*COMMIT_FLAGS, "commit", "--allow-empty", "-m", f"change {branch}", cwd=work)
            git("push", "--quiet", "origin", f"{branch}:{branch}", cwd=work)
            return git("rev-parse", "HEAD", cwd=work).decode().strip()

    def branch_files(self, repo: str, branch: str) -> dict[str, str]:
        gd = self._git_dir(repo)
        names = git("ls-tree", "-r", "--name-only", "-z", f"refs/heads/{branch}", cwd=gd).decode()
        return {n: git("show", f"refs/heads/{branch}:{n}", cwd=gd).decode()
                for n in names.split("\0") if n}
