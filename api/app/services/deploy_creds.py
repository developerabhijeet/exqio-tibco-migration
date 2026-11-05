"""Deploy-step credentials: the Terraform environment for one plan, without storing a secret.

In GMF's install no client secret is kept anywhere ExiQO controls. ``DEPLOY_ENVIRONMENTS`` declares
each Azure target (tenant, subscription, client id) and how the worker authenticates to it:

* ``workload_identity``: AKS Workload Identity. The webhook mounts a federated token into the pod
  (``AZURE_FEDERATED_TOKEN_FILE``) and Terraform exchanges it for the target's managed identity
  (``ARM_USE_OIDC``); there is no secret at all.
* ``keyvault_sp``: a service principal whose secret lives in Key Vault. It is read at plan time,
  each plan (no cache, so a rotation takes effect on the next plan), handed to the Terraform child
  process only, and never logged, stored or put in a job payload or exception.

``local`` (``DEPLOY_CREDENTIAL_MODE=local``, local development only) keeps the Connect Azure form's
Fernet-encrypted secret.
"""
from __future__ import annotations

import os
from collections.abc import Callable, Mapping
from typing import Any

from azure.identity import DefaultAzureCredential
from azure.keyvault.secrets import SecretClient
from cryptography.fernet import Fernet

from api.app.config import DeployEnvironment, Settings
from api.app.models import AzureConnection
from api.app.services.infra import decrypt_secret, plan_env

SecretReader = Callable[[str, str], str]
TOKEN_FILE_ENV = "AZURE_FEDERATED_TOKEN_FILE"
NO_TOKEN_FILE = f"Workload Identity is not configured for this pod ({TOKEN_FILE_ENV} missing)"


class DeployCredentialError(Exception):
    """The plan cannot get Azure credentials; the message names no secret value."""


def keyvault_secret(vault_uri: str, name: str) -> str:
    """The current value of secret ``name`` in the vault, read with the pod's identity.

    Errors name the vault and the secret only: the SDK's exception (which may quote a response) is
    dropped, not chained, so neither its text nor the value reaches a log or a job row."""
    try:
        value = SecretClient(vault_url=vault_uri, credential=DefaultAzureCredential()).get_secret(name).value
    except Exception as exc:  # noqa: BLE001  any SDK or auth failure, reported without its text
        raise DeployCredentialError(f"cannot read secret {name!r} from Key Vault {vault_uri} "
                                    f"({type(exc).__name__})") from None
    if not value:
        raise DeployCredentialError(f"secret {name!r} in Key Vault {vault_uri} is empty")
    return value


def declared_environment(settings: Settings, name: str) -> DeployEnvironment | None:
    return next((e for e in settings.deploy_environments if e.name == name), None)


def environments_out(settings: Settings) -> dict[str, Any]:
    """``GET /deploy/environments``: where each secret lives (vault and name), never a value."""
    return {"mode": settings.deploy_credential_mode,
            "environments": [e.model_dump() for e in settings.deploy_environments]}


def connection_for_plan(conn: AzureConnection, settings: Settings, environment: str) -> AzureConnection:
    """The connection a plan of ``environment`` runs with, resolved when the job runs.

    The payload fixes the environment at submit; the row may have been reconnected to another one
    since. Outside local mode the ids, auth and Key Vault reference therefore come from the declared
    environment of that name (a transient row, never saved). In local mode the stored row must
    still be for that environment."""
    if settings.deploy_credential_mode == "local":
        stored = conn.environment or "dev"
        if stored != environment:
            raise DeployCredentialError(f"the Azure connection is now for environment {stored!r}, not "
                                        f"{environment!r} as when this plan was queued: plan again")
        return conn
    env = declared_environment(settings, environment)
    if env is None:
        raise DeployCredentialError(f"deploy environment {environment!r} is no longer declared "
                                    "(DEPLOY_ENVIRONMENTS): connect Azure again and plan")
    return AzureConnection(project_id=conn.project_id, tenant=env.tenant, subscription=env.subscription,
                           settings={}, environment=env.name, auth_mode=env.auth, client_id=env.client_id,
                           key_vault_uri=env.key_vault_uri or None, secret_name=env.secret_name or None)


def terraform_env(conn: AzureConnection, settings: Settings, *, secret_reader: SecretReader,
                  fernet: Fernet | None = None, environ: Mapping[str, str] | None = None) -> dict[str, str]:
    """The ``ARM_*`` variables for ``terragrunt plan`` with this connection (the child env only)."""
    mode = conn.auth_mode or "local"
    if mode == "local":
        if settings.deploy_credential_mode != "local":
            raise DeployCredentialError("this Azure connection holds a local credential, which this install "
                                        "does not use: connect Azure again with a declared environment")
        if fernet is None:
            raise DeployCredentialError("no key to decrypt the stored Azure client secret")
        return plan_env(conn, decrypt_secret(fernet, conn))
    if not conn.client_id:
        raise DeployCredentialError(f"the Azure connection ({mode}) has no client id")
    env = {"ARM_TENANT_ID": conn.tenant, "ARM_SUBSCRIPTION_ID": conn.subscription,
           "ARM_CLIENT_ID": conn.client_id}
    if mode == "workload_identity":
        token_file = (os.environ if environ is None else environ).get(TOKEN_FILE_ENV)
        if not token_file:
            raise DeployCredentialError(NO_TOKEN_FILE)
        return {**env, "ARM_USE_OIDC": "true", "ARM_USE_AKS_WORKLOAD_IDENTITY": "true",
                "ARM_OIDC_TOKEN_FILE_PATH": token_file}
    if mode == "keyvault_sp":
        if not (conn.key_vault_uri and conn.secret_name):
            raise DeployCredentialError("the Azure connection (keyvault_sp) names no Key Vault secret")
        return {**env, "ARM_CLIENT_SECRET": secret_reader(conn.key_vault_uri, conn.secret_name)}
    raise DeployCredentialError(f"unknown Azure connection auth mode {mode!r}")
