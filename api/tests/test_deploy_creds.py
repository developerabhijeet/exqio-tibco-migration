"""Deploy-step credentials: Workload Identity, Key Vault references and the local Fernet path (Task 8).

No test reaches Azure: the Key Vault reader is injected (``secret_reader``) or ``SecretClient`` is
replaced, and ``terragrunt`` is a shell script that records the environment it was given and
prints a plan line (and, to prove the redaction, the secret it received).
"""
from __future__ import annotations

import json
import stat
from pathlib import Path
from types import SimpleNamespace

import pytest
from cryptography.fernet import Fernet
from sqlalchemy import select

from api.app.config import DeployEnvironment, Settings
from api.app.models import AzureConnection, Build, Job, JobEvent, Plan
from api.app.services import build as build_svc
from api.app.services import deploy_creds
from api.app.services.deploy_creds import terraform_env
from api.tests.test_hosting import make_build
from api.tests.test_job_registry import wait_terminal

OPS = {"X-User": "ops.sam"}
TOKEN_FILE = "/var/run/secrets/azure/tokens/azure-identity-token"
WI_ENV = DeployEnvironment(name="dev", tenant="t-wi", subscription="s-wi", client_id="c-wi",
                           auth="workload_identity")
KV_ENV = DeployEnvironment(name="test", tenant="t-kv", subscription="s-kv", client_id="c-kv",
                           auth="keyvault_sp", key_vault_uri="https://kv-gmf.vault.azure.net/",
                           secret_name="deploy-sp-test")


def fail_if_called(uri: str, name: str) -> str:
    raise AssertionError("the Key Vault reader must not be called")


def conn_for(env: DeployEnvironment) -> AzureConnection:
    return AzureConnection(project_id="p", tenant=env.tenant, subscription=env.subscription, settings={},
                           environment=env.name, auth_mode=env.auth, client_id=env.client_id,
                           key_vault_uri=env.key_vault_uri or None, secret_name=env.secret_name or None)


@pytest.fixture
def wi_settings(settings: Settings) -> Settings:
    settings.deploy_credential_mode = "workload_identity"
    settings.deploy_environments = [WI_ENV, KV_ENV]
    return settings


@pytest.fixture
def token_file(monkeypatch):
    monkeypatch.setenv("AZURE_FEDERATED_TOKEN_FILE", TOKEN_FILE)


# ------------------------------------------------------------------ terraform_env
def test_workload_identity_env_has_no_secret(wi_settings, token_file):
    env = terraform_env(conn_for(WI_ENV), wi_settings, secret_reader=fail_if_called)
    assert env["ARM_USE_OIDC"] == "true" and "ARM_CLIENT_SECRET" not in env
    assert env["ARM_USE_AKS_WORKLOAD_IDENTITY"] == "true"
    assert env["ARM_TENANT_ID"] == "t-wi" and env["ARM_SUBSCRIPTION_ID"] == "s-wi"
    assert env["ARM_CLIENT_ID"] == "c-wi" and env["ARM_OIDC_TOKEN_FILE_PATH"] == TOKEN_FILE


def test_workload_identity_without_token_file_fails(wi_settings, monkeypatch):
    monkeypatch.delenv("AZURE_FEDERATED_TOKEN_FILE", raising=False)
    with pytest.raises(deploy_creds.DeployCredentialError,
                       match=r"Workload Identity is not configured for this pod \(AZURE_FEDERATED_TOKEN_FILE missing\)"):
        terraform_env(conn_for(WI_ENV), wi_settings, secret_reader=fail_if_called)


def test_keyvault_secret_read_each_plan(wi_settings, token_file):  # Review Focus 3
    values = iter(["v1", "v2"])
    calls: list[tuple[str, str]] = []

    def reader(uri: str, name: str) -> str:
        calls.append((uri, name))
        return next(values)

    conn = conn_for(KV_ENV)
    first = terraform_env(conn, wi_settings, secret_reader=reader)
    assert first["ARM_CLIENT_SECRET"] == "v1" and first["ARM_CLIENT_ID"] == "c-kv"
    assert "ARM_USE_OIDC" not in first
    assert terraform_env(conn, wi_settings, secret_reader=reader)["ARM_CLIENT_SECRET"] == "v2"
    assert calls == [(KV_ENV.key_vault_uri, KV_ENV.secret_name)] * 2


def test_local_mode_uses_the_decrypted_secret(settings):
    fernet = Fernet(Fernet.generate_key())
    conn = AzureConnection(project_id="p", tenant="t", subscription="s", settings={"client_id": "c"},
                           auth_mode="local", secret_enc=fernet.encrypt(b"local-secret").decode())
    env = terraform_env(conn, settings, secret_reader=fail_if_called, fernet=fernet)
    assert env == {"ARM_TENANT_ID": "t", "ARM_SUBSCRIPTION_ID": "s", "ARM_CLIENT_ID": "c",
                   "ARM_CLIENT_SECRET": "local-secret"}


def test_local_connection_refused_in_workload_identity_install(wi_settings):
    conn = AzureConnection(project_id="p", tenant="t", subscription="s", settings={}, auth_mode="local")
    with pytest.raises(deploy_creds.DeployCredentialError, match="declared environment"):
        terraform_env(conn, wi_settings, secret_reader=fail_if_called, fernet=Fernet(Fernet.generate_key()))


# ------------------------------------------------------------------ keyvault_secret
def test_keyvault_secret_reads_through_secret_client(monkeypatch):
    seen: dict = {}

    class FakeClient:
        def __init__(self, vault_url, credential):
            seen["vault"] = vault_url

        def get_secret(self, name):
            seen.setdefault("names", []).append(name)
            return SimpleNamespace(value=f"value-{len(seen['names'])}")

    monkeypatch.setattr(deploy_creds, "SecretClient", FakeClient)
    monkeypatch.setattr(deploy_creds, "DefaultAzureCredential", lambda: object())
    assert deploy_creds.keyvault_secret("https://kv.vault.azure.net/", "sp") == "value-1"
    assert deploy_creds.keyvault_secret("https://kv.vault.azure.net/", "sp") == "value-2"  # no cache
    assert seen == {"vault": "https://kv.vault.azure.net/", "names": ["sp", "sp"]}


def test_keyvault_secret_error_names_vault_and_secret_only(monkeypatch, caplog):
    class Boom:
        def __init__(self, vault_url, credential):
            pass

        def get_secret(self, name):
            raise RuntimeError("upstream said hunter2-token")

    monkeypatch.setattr(deploy_creds, "SecretClient", Boom)
    monkeypatch.setattr(deploy_creds, "DefaultAzureCredential", lambda: object())
    with pytest.raises(deploy_creds.DeployCredentialError) as info:
        deploy_creds.keyvault_secret("https://kv.vault.azure.net/", "sp")
    msg = str(info.value)
    assert "https://kv.vault.azure.net/" in msg and "'sp'" in msg and "hunter2" not in msg
    assert info.value.__cause__ is None and info.value.__suppress_context__
    assert "hunter2" not in caplog.text


def test_keyvault_secret_empty_value_fails(monkeypatch):
    class Empty:
        def __init__(self, vault_url, credential):
            pass

        def get_secret(self, name):
            return SimpleNamespace(value=None)

    monkeypatch.setattr(deploy_creds, "SecretClient", Empty)
    monkeypatch.setattr(deploy_creds, "DefaultAzureCredential", lambda: object())
    with pytest.raises(deploy_creds.DeployCredentialError, match="empty"):
        deploy_creds.keyvault_secret("https://kv.vault.azure.net/", "sp")


# ------------------------------------------------------------------ API
async def _project_of(app, build_id: str) -> str:
    from api.app.models import Process

    async with app.state.sessionmaker() as s:
        build = await s.get(Build, build_id)
        return (await s.get(Process, build.process_id)).project_id


async def test_environments_listed_without_secret_values(wi_settings, client):
    r = await client.get("/deploy/environments", headers=OPS)
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["mode"] == "workload_identity"
    assert [e["name"] for e in out["environments"]] == ["dev", "test"]
    kv = out["environments"][1]
    assert kv == {"name": "test", "tenant": "t-kv", "subscription": "s-kv", "client_id": "c-kv",
                  "auth": "keyvault_sp", "key_vault_uri": KV_ENV.key_vault_uri,
                  "secret_name": "deploy-sp-test"}


async def test_brand_reports_deploy_credential_mode(client, wi_settings):
    assert (await client.get("/system/brand")).json()["deploy_credential_mode"] == "workload_identity"


async def test_undeclared_environment_rejected(wi_settings, app, client):
    pid = await _project_of(app, await make_build(app))
    r = await client.put(f"/projects/{pid}/azure", json={"environment": "nope"}, headers=OPS)
    assert r.status_code == 422 and "nope" in r.text
    # the local form's fields are not accepted in this mode: no secret can be posted
    r = await client.put(f"/projects/{pid}/azure", headers=OPS,
                         json={"environment": "dev", "client_secret": "x"})
    assert r.status_code == 422


async def test_declared_environment_copied_from_settings(wi_settings, app, client):
    pid = await _project_of(app, await make_build(app))
    r = await client.put(f"/projects/{pid}/azure", json={"environment": "test"}, headers=OPS)
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["connected"] and out["environment"] == "test" and out["auth_mode"] == "keyvault_sp"
    assert out["tenant"] == "t-kv" and out["subscription"] == "s-kv" and out["client_id"] == "c-kv"
    assert out["key_vault_uri"] == KV_ENV.key_vault_uri and out["secret_name"] == "deploy-sp-test"
    assert out["secret_stored"] is False
    async with app.state.sessionmaker() as s:
        conn = await s.scalar(select(AzureConnection).where(AzureConnection.project_id == pid))
    assert conn.secret_enc is None and conn.auth_mode == "keyvault_sp"


async def test_local_mode_keeps_the_existing_body(app, client):
    pid = await _project_of(app, await make_build(app))
    r = await client.put(f"/projects/{pid}/azure", json={"environment": "dev"}, headers=OPS)
    assert r.status_code == 422  # tenant and subscription are required in local mode
    body = {"tenant": "t", "subscription": "s", "client_id": "c", "client_secret": "local-s"}
    r = await client.post(f"/projects/{pid}/azure", json=body, headers=OPS)
    assert r.status_code == 200 and r.json()["auth_mode"] == "local" and r.json()["secret_stored"]


# ------------------------------------------------------------------ plan job
def fake_terragrunt(tmp_path: Path, envs: tuple[str, ...] = ("dev", "test", "prod")) -> tuple[Path, Path]:
    """A ``terragrunt`` that records its environment and prints a plan line plus the secret."""
    record = tmp_path / "child-env.txt"
    script = tmp_path / "terragrunt"
    script.write_text(f"#!/bin/sh\nenv > '{record}'\npwd >> '{record}'\n"
                      "echo \"[app] using secret $ARM_CLIENT_SECRET\"\n"
                      "echo \"[app] Plan: 1 to add, 0 to change, 0 to destroy.\"\n")
    script.chmod(script.stat().st_mode | stat.S_IEXEC)
    out = tmp_path / "out"
    for env in envs:
        (out / "infra" / "live" / env).mkdir(parents=True)
    return script, record


def tools(script: Path) -> dict:
    return {t: {"present": True, "path": str(script) if t == "terragrunt" else t}
            for t in ("terragrunt", "terraform", "az", "dotnet")}


async def _job_dump(app, job_id: str, plan_id: str) -> str:
    async with app.state.sessionmaker() as s:
        job = await s.get(Job, job_id)
        events = (await s.execute(select(JobEvent).where(JobEvent.job_id == job_id))).scalars().all()
        plan = await s.get(Plan, plan_id)
    return json.dumps({"payload": job.payload, "result": job.result, "error": job.error,
                       "events": [{"event": e.event, "text": e.text, "data": e.data} for e in events],
                       "plan_output": plan.output, "plan_summary": plan.summary}, default=str)


async def test_plan_payload_and_events_hold_no_secret(wi_settings, app, client, tmp_path, token_file,
                                                     monkeypatch):
    script, record = fake_terragrunt(tmp_path)
    monkeypatch.setattr(build_svc, "toolchain", lambda: tools(script))
    reads: list[tuple[str, str]] = []

    def reader(uri: str, name: str) -> str:
        reads.append((uri, name))
        return "s3cr3t"

    monkeypatch.setattr(deploy_creds, "keyvault_secret", reader)
    build_id = await make_build(app, output_dir=str(tmp_path / "out"))
    pid = await _project_of(app, build_id)
    assert (await client.put(f"/projects/{pid}/azure", json={"environment": "test"}, headers=OPS)).status_code == 200
    r = await client.post(f"/builds/{build_id}/plan", headers=OPS)
    assert r.status_code == 200 and r.json()["connected"] is True, r.text
    job_id, plan_id = r.json()["job_id"], r.json()["plan_id"]
    job = await wait_terminal(app, job_id, timeout=30)
    assert job.status == "done", job.error
    assert job.payload == {"build_id": build_id, "plan_id": plan_id, "environment": "test", "iteration": 1}
    dump = await _job_dump(app, job_id, plan_id)
    assert "s3cr3t" not in dump
    assert "1 to add" in dump  # the plan output is stored, redacted
    child = record.read_text()
    assert "ARM_CLIENT_SECRET=s3cr3t" in child and child.rstrip().endswith("/infra/live/test")
    assert reads == [(KV_ENV.key_vault_uri, KV_ENV.secret_name)]


async def test_plan_workload_identity_reaches_terragrunt_with_oidc(wi_settings, app, client, tmp_path,
                                                                  token_file, monkeypatch):
    script, record = fake_terragrunt(tmp_path)
    monkeypatch.setattr(build_svc, "toolchain", lambda: tools(script))
    monkeypatch.setattr(deploy_creds, "keyvault_secret", fail_if_called)
    build_id = await make_build(app, output_dir=str(tmp_path / "out"))
    pid = await _project_of(app, build_id)
    await client.put(f"/projects/{pid}/azure", json={"environment": "dev"}, headers=OPS)
    r = await client.post(f"/builds/{build_id}/plan", headers=OPS)
    job = await wait_terminal(app, r.json()["job_id"], timeout=30)
    assert job.status == "done", job.error
    child = record.read_text()
    assert "ARM_USE_OIDC=true" in child and f"ARM_OIDC_TOKEN_FILE_PATH={TOKEN_FILE}" in child
    assert "ARM_CLIENT_SECRET" not in child and child.rstrip().endswith("/infra/live/dev")


async def test_plan_fails_clearly_for_an_environment_not_generated(wi_settings, app, client, tmp_path,
                                                                  token_file, monkeypatch):
    script, _ = fake_terragrunt(tmp_path, envs=("dev",))
    monkeypatch.setattr(build_svc, "toolchain", lambda: tools(script))
    monkeypatch.setattr(deploy_creds, "keyvault_secret", lambda uri, name: "s3cr3t")
    build_id = await make_build(app, output_dir=str(tmp_path / "out"))
    pid = await _project_of(app, build_id)
    await client.put(f"/projects/{pid}/azure", json={"environment": "test"}, headers=OPS)
    r = await client.post(f"/builds/{build_id}/plan", headers=OPS)
    job = await wait_terminal(app, r.json()["job_id"], timeout=30)
    assert job.status == "failed" and "not among the generated live environments" in job.error
    async with app.state.sessionmaker() as s:
        plan = await s.get(Plan, r.json()["plan_id"])
    assert plan.status == "failed" and "dev" in plan.output and "s3cr3t" not in plan.output


async def test_plan_fails_without_token_file_in_workload_identity(wi_settings, app, client, tmp_path,
                                                                 monkeypatch):
    monkeypatch.delenv("AZURE_FEDERATED_TOKEN_FILE", raising=False)
    script, _ = fake_terragrunt(tmp_path)
    monkeypatch.setattr(build_svc, "toolchain", lambda: tools(script))
    build_id = await make_build(app, output_dir=str(tmp_path / "out"))
    pid = await _project_of(app, build_id)
    await client.put(f"/projects/{pid}/azure", json={"environment": "dev"}, headers=OPS)
    r = await client.post(f"/builds/{build_id}/plan", headers=OPS)
    job = await wait_terminal(app, r.json()["job_id"], timeout=30)
    assert job.status == "failed" and "AZURE_FEDERATED_TOKEN_FILE missing" in job.error
    async with app.state.sessionmaker() as s:
        assert (await s.get(Plan, r.json()["plan_id"])).status == "failed"


async def test_plan_local_mode_default_environment_and_secret_redacted(app, client, tmp_path, monkeypatch):
    script, record = fake_terragrunt(tmp_path)
    monkeypatch.setattr(build_svc, "toolchain", lambda: tools(script))
    build_id = await make_build(app, output_dir=str(tmp_path / "out"))
    pid = await _project_of(app, build_id)
    body = {"tenant": "t", "subscription": "s", "client_id": "c", "client_secret": "local-s3cr3t"}
    assert (await client.post(f"/projects/{pid}/azure", json=body, headers=OPS)).status_code == 200
    r = await client.post(f"/builds/{build_id}/plan", headers=OPS)
    job = await wait_terminal(app, r.json()["job_id"], timeout=30)
    assert job.status == "done", job.error
    assert job.payload["environment"] == "dev"
    assert "local-s3cr3t" not in await _job_dump(app, r.json()["job_id"], r.json()["plan_id"])
    assert "ARM_CLIENT_SECRET=local-s3cr3t" in record.read_text()


async def test_plan_carries_tf_cli_config_from_settings_in_workload_identity(
        wi_settings, app, client, tmp_path, token_file, monkeypatch):
    monkeypatch.delenv("TF_CLI_CONFIG_FILE", raising=False)  # set by settings only, not the process
    wi_settings.tf_cli_config_file = "/etc/gmf/terraform.rc"
    script, record = fake_terragrunt(tmp_path)
    monkeypatch.setattr(build_svc, "toolchain", lambda: tools(script))
    monkeypatch.setattr(deploy_creds, "keyvault_secret", fail_if_called)
    build_id = await make_build(app, output_dir=str(tmp_path / "out"))
    pid = await _project_of(app, build_id)
    await client.put(f"/projects/{pid}/azure", json={"environment": "dev"}, headers=OPS)
    r = await client.post(f"/builds/{build_id}/plan", headers=OPS)
    job = await wait_terminal(app, r.json()["job_id"], timeout=30)
    assert job.status == "done", job.error
    assert "TF_CLI_CONFIG_FILE=/etc/gmf/terraform.rc" in record.read_text()


async def test_plan_carries_tf_cli_config_from_settings_in_local_mode(app, client, tmp_path, monkeypatch):
    monkeypatch.delenv("TF_CLI_CONFIG_FILE", raising=False)
    app.state.settings.tf_cli_config_file = "/etc/gmf/terraform.rc"
    script, record = fake_terragrunt(tmp_path)
    monkeypatch.setattr(build_svc, "toolchain", lambda: tools(script))
    build_id = await make_build(app, output_dir=str(tmp_path / "out"))
    pid = await _project_of(app, build_id)
    body = {"tenant": "t", "subscription": "s", "client_id": "c", "client_secret": "local-s3cr3t"}
    assert (await client.post(f"/projects/{pid}/azure", json=body, headers=OPS)).status_code == 200
    r = await client.post(f"/builds/{build_id}/plan", headers=OPS)
    job = await wait_terminal(app, r.json()["job_id"], timeout=30)
    assert job.status == "done", job.error
    assert "TF_CLI_CONFIG_FILE=/etc/gmf/terraform.rc" in record.read_text()


# ------------------------------------------------------------------ Azure OpenAI managed identity
def test_azure_openai_managed_identity_uses_token_provider(settings, monkeypatch):
    import openai

    from api.app.services import providers

    settings.provider = "auto"
    settings.claude_code_oauth_token = ""
    settings.azure_openai_endpoint = "https://aoai.example/"
    settings.azure_openai_api_key = ""
    settings.azure_openai_auth = "managed_identity"
    settings.azure_openai_deployment_strong = "gpt-strong"
    made: list[tuple] = []

    def fake_provider(credential, scope):
        made.append((credential, scope))
        return lambda: "token"

    monkeypatch.setattr(providers, "DefaultAzureCredential", lambda: "cred")
    monkeypatch.setattr(providers, "get_bearer_token_provider", fake_provider)
    assert settings.resolved_provider() == "azure"
    choice = providers.build_provider(settings)
    assert choice.name == "azure" and choice.available
    provider = choice.provider.primary
    client = provider._get_client()
    assert isinstance(client, openai.AsyncAzureOpenAI)
    assert made == []  # no credential (no IMDS) until the first request needs a token
    assert client._azure_ad_token_provider() == "token"
    assert made == [("cred", "https://cognitiveservices.azure.com/.default")]
    assert client._azure_ad_token_provider() == "token" and len(made) == 1  # made once


# ------------------------------------------------------------------ review fixes
def test_plan_ready_needs_az_only_for_local():
    from api.app.services.infra import plan_ready

    no_az = {"terragrunt": {"present": True}, "terraform": {"present": True}, "az": {"present": False}}
    assert plan_ready(conn_for(WI_ENV), no_az) == (True, None)
    assert plan_ready(conn_for(KV_ENV), no_az) == (True, None)
    local = AzureConnection(project_id="p", tenant="t", subscription="s", settings={}, auth_mode="local")
    ready, reason = plan_ready(local, no_az)
    assert ready is False and "az" in reason


async def _queued_plan(app, build_id: str, environment: str) -> tuple[str, str]:
    """A plan queued for ``environment`` the way the router queues one (payload fixed at submit)."""
    from api.app.services import infra as infra_svc

    async with app.state.sessionmaker() as s:
        build = await s.get(Build, build_id)
        plan = await infra_svc.record_connect_later(s, build, "")
        plan.connected, plan.status = True, "running"
        await s.commit()
        plan_id = plan.id
    job_id = await app.state.runner.submit("plan", {"build_id": build_id, "plan_id": plan_id,
                                                    "environment": environment})
    return job_id, plan_id


async def test_plan_uses_the_queued_environment_after_a_reconnect(wi_settings, app, client, tmp_path,
                                                                  token_file, monkeypatch):
    script, record = fake_terragrunt(tmp_path)
    monkeypatch.setattr(build_svc, "toolchain", lambda: tools(script))
    monkeypatch.setattr(deploy_creds, "keyvault_secret", lambda uri, name: "kv-secret")
    build_id = await make_build(app, output_dir=str(tmp_path / "out"))
    pid = await _project_of(app, build_id)
    # queued for "test" (keyvault_sp); the project is reconnected to "dev" (workload identity) since
    await client.put(f"/projects/{pid}/azure", json={"environment": "dev"}, headers=OPS)
    job_id, _ = await _queued_plan(app, build_id, "test")
    job = await wait_terminal(app, job_id, timeout=30)
    assert job.status == "done", job.error
    child = record.read_text()
    assert "ARM_TENANT_ID=t-kv" in child and "ARM_SUBSCRIPTION_ID=s-kv" in child
    assert "ARM_CLIENT_ID=c-kv" in child and "ARM_CLIENT_SECRET=kv-secret" in child
    assert "ARM_USE_OIDC" not in child and child.rstrip().endswith("/infra/live/test")


async def test_plan_fails_when_its_environment_is_no_longer_declared(wi_settings, app, client, tmp_path,
                                                                    token_file, monkeypatch):
    script, record = fake_terragrunt(tmp_path)
    monkeypatch.setattr(build_svc, "toolchain", lambda: tools(script))
    build_id = await make_build(app, output_dir=str(tmp_path / "out"))
    pid = await _project_of(app, build_id)
    await client.put(f"/projects/{pid}/azure", json={"environment": "dev"}, headers=OPS)
    wi_settings.deploy_environments = [KV_ENV]  # "dev" removed after the plan was queued
    job_id, plan_id = await _queued_plan(app, build_id, "dev")
    job = await wait_terminal(app, job_id, timeout=30)
    assert job.status == "failed" and "'dev' is no longer declared" in job.error
    async with app.state.sessionmaker() as s:
        plan = await s.get(Plan, plan_id)
    assert plan.status == "failed" and "no longer declared" in plan.output
    assert not record.exists()  # terragrunt never ran


async def test_local_plan_fails_when_the_connection_changed_environment(app, client, tmp_path, monkeypatch):
    script, record = fake_terragrunt(tmp_path)
    monkeypatch.setattr(build_svc, "toolchain", lambda: tools(script))
    build_id = await make_build(app, output_dir=str(tmp_path / "out"))
    pid = await _project_of(app, build_id)
    body = {"tenant": "t", "subscription": "s", "client_id": "c", "client_secret": "local-s3cr3t",
            "environment": "prod"}
    assert (await client.post(f"/projects/{pid}/azure", json=body, headers=OPS)).status_code == 200
    job_id, plan_id = await _queued_plan(app, build_id, "dev")  # queued before the reconnect to prod
    job = await wait_terminal(app, job_id, timeout=30)
    assert job.status == "failed" and "now for environment 'prod', not 'dev'" in job.error
    assert "local-s3cr3t" not in await _job_dump(app, job_id, plan_id)
    assert not record.exists()


async def test_plan_exception_reaches_jobs_error_redacted(wi_settings, app, client, tmp_path, token_file,
                                                         monkeypatch, caplog):
    """An unexpected failure whose message carries the Key Vault service principal's secret is
    stored redacted in jobs.error, the error event, the plan row and the log."""
    from api.app.services import infra as infra_svc

    script, _ = fake_terragrunt(tmp_path)
    monkeypatch.setattr(build_svc, "toolchain", lambda: tools(script))
    monkeypatch.setattr(deploy_creds, "keyvault_secret", lambda uri, name: "s3cr3t-in-exc")

    async def boom(ctx, build_id, plan_id, env, *args):
        raise RuntimeError(f"terragrunt crashed with ARM_CLIENT_SECRET={env['ARM_CLIENT_SECRET']}")

    monkeypatch.setattr(infra_svc, "_run_plan", boom)
    build_id = await make_build(app, output_dir=str(tmp_path / "out"))
    pid = await _project_of(app, build_id)
    await client.put(f"/projects/{pid}/azure", json={"environment": "test"}, headers=OPS)
    r = await client.post(f"/builds/{build_id}/plan", headers=OPS)
    job = await wait_terminal(app, r.json()["job_id"], timeout=30)
    assert job.status == "failed"
    assert "s3cr3t-in-exc" not in job.error and "ARM_CLIENT_SECRET=[redacted]" in job.error
    assert "s3cr3t-in-exc" not in await _job_dump(app, r.json()["job_id"], r.json()["plan_id"])
    assert "s3cr3t-in-exc" not in caplog.text
