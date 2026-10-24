# Azure prerequisites (optional Terraform)

Creates what the `deploy/helm/exiqo` chart expects to find in GMF's Azure subscription. It is
optional: each part is behind an `enable_*` variable, so the platform team can create some parts
here and bring its own for the rest. For anything not created here, fill the matching values keys
in `values.gmf.yaml` by hand.

| Switch | Creates |
|---|---|
| `enable_acr` | Container registry, Premium, admin user off, public access per `public_network_access_enabled` |
| `enable_key_vault` | Key Vault in RBAC mode, purge protection on, 90-day soft delete, public access per variable |
| `enable_signing_key` | `exiqo-cosign`: a P-256 key in that vault (an HSM key with the premium SKU), which the release pipelines sign with |
| `enable_identities` | `exiqo-platform`, federated for `exiqo-api`, `exiqo-worker`, `exiqo-migrate`, `exiqo-neo4j` and `exiqo-neo4j-backup`; one `exiqo-deploy-<env>` per `deploy_environments` entry, federated for `exiqo-worker`. The issuer is `aks_oidc_issuer_url` and each subject is `system:serviceaccount:<namespace>:<account>` |
| `enable_postgres` | PostgreSQL flexible server 16 with private access (a delegated subnet and a private DNS zone), `azure.extensions = VECTOR`, the databases `exiqo` and `exiqo_test`, and the secrets `exiqo-database-url` and `exiqo-test-database-url` in the vault |
| `enable_data_share` | FileStorage account (Premium, SMB, NFS off) with the `exiqo-data` share for `DATA_DIR` |
| `enable_backup_storage` | StorageV2 account (shared keys off) with the `neo4j-backups` container and a lifecycle rule that deletes old dumps. A FileStorage account cannot hold blobs, so this is a second account |

Out of scope by design:

- The AKS cluster and its network. These come in as ids: the OIDC issuer, the subnets and the
  DNS zones.
- RBAC on the Deploy step's target subscriptions. GMF decides what each `exiqo-deploy-<env>`
  identity may change (see the role assignments below).

Role assignments made here: `exiqo-platform` gets Key Vault Secrets User on the vault and Storage
Blob Data Contributor on the backup container. It also gets Cognitive Services OpenAI User when
`azure_openai_account_id` is set. The following are made only when their principal ids are given:

| Variable | Role | Scope |
|---|---|---|
| `aks_kubelet_identity_object_id` | AcrPull | the registry |
| `aks_cluster_identity_principal_id` | Storage Account Contributor | the data account (the Azure Files CSI driver reads its key) |
| `release_principal_id` | AcrPush | the registry |
| `release_principal_id` | Key Vault Crypto User | the signing key |

## Run it

```sh
cd deploy/azure
cp gmf.example.tfvars gmf.tfvars          # fill in GMF's names and ids
export ARM_USE_OIDC=true ARM_TENANT_ID=... ARM_CLIENT_ID=... ARM_SUBSCRIPTION_ID=...
export TF_CLI_CONFIG_FILE=...             # GMF's provider mirror (provider_installation block)
terraform init -backend-config=resource_group_name=... -backend-config=storage_account_name=... \
  -backend-config=container_name=tfstate -backend-config=key=exiqo-prereqs.tfstate \
  -backend-config=use_azuread_auth=true -backend-config=use_oidc=true
terraform plan -var-file=gmf.tfvars -out=prereqs.plan
terraform apply prereqs.plan
```

Run it from a machine that can reach the private endpoints: the Key Vault secrets and the signing
key are written through the vault's data plane. The identity running Terraform is granted Key Vault
Secrets Officer, and Crypto Officer if `enable_signing_key` is on. To skip that grant, set
`grant_runner_key_vault_access = false` and grant those roles yourself. Shared keys are off on the
backup account, so the provider reaches that account's data plane with Entra ID
(`storage_use_azuread`). If a plan fails there with 403, give the runner Storage Blob Data
Contributor and Storage Queue Data Contributor on that account. The state contains the
generated Postgres password, the same value that is in the vault. Keep the state container
private and encrypted.

`make azure-validate` checks this directory without Azure credentials. It runs `terraform fmt`,
`init -backend=false`, `validate` and `terraform test`. The tests use mocked providers and check
that the outputs map onto the chart's values. It then runs tflint with the azurerm ruleset,
against `gmf.example.tfvars`.

## Outputs to Helm values

`helm_values` is a values file. Its keys are the chart's values paths, so nothing is retyped:

```sh
terraform output -json helm_values > values.azure.json
helm upgrade --install exiqo deploy/helm/exiqo -n exiqo -f values.gmf.yaml -f values.azure.json
```

| `helm_values` key | Source |
|---|---|
| `image.registry` | the registry's login server |
| `keyVault.name` | the vault's name |
| `keyVault.secrets.databaseUrl` | `secret_names.database_url` (`exiqo-database-url`), written with `postgresql+asyncpg://<admin>:<password>@<host>:5432/exiqo?ssl=require` |
| `keyVault.secrets.testDatabaseUrl` | `secret_names.test_database_url` (`exiqo-test-database-url`), the `exiqo_test` database (if `write_test_database_url` is on) |
| `identity.platformClientId` | `exiqo-platform`'s client id |
| `identity.deployIdentities.<env>` | each `exiqo-deploy-<env>`'s client id |
| `postgres.host` | the server's FQDN |
| `postgres.cidrs` | `[postgres_delegated_subnet_cidr]` |
| `data.size` | the share quota, `<n>Gi` |
| `data.storageClass.storageAccount` | the data account |
| `data.storageClass.resourceGroup` | `resource_group_name` |
| `data.storageClass.shareName` | `exiqo-data` |
| `neo4jBackup.storageAccount` | the backup account (the backup itself stays off: `neo4jBackup.enabled`) |
| `neo4jBackup.container` | `neo4j-backups` |

Only resources created here appear in `helm_values`: a switch that is off leaves its keys to
`values.gmf.yaml`. Helm merges maps key by key, so `values.gmf.yaml` keeps the rest. That includes
`deploy.environments`, whose names must match `deploy_environments`, and the other
`keyVault.secrets`. Lists are replaced, not merged. That is why `egress.fqdns` is not in
`helm_values`: add the backup account's blob host (the `egress_fqdns` output) to the list in
`values.gmf.yaml`. `tests/helm_values.expected.json` holds the full output for the example inputs.
`terraform test` compares the real output with it, and `make chart-check` renders the chart with
it, so a key the chart does not know fails one of the two.

Other outputs:

- `platform_identity_principal_id` and `deploy_identity_principal_ids`: the identities' object
  ids, for role assignments.
- `cosign_key`: the pipelines' `COSIGN_KEY`.
- `egress_fqdns`: hosts to add to `egress.fqdns` and to GMF's firewall.

## Not created here

- **Secrets the app needs that are not database URLs.** Create these in the same vault, with the
  names in `keyVault.secrets`, for example:

  ```sh
  az keyvault secret set --vault-name <vault> -n exiqo-app-secret \
    --value "$(python3 -c 'import base64, os; print(base64.urlsafe_b64encode(os.urandom(32)).decode())')"
  az keyvault secret set --vault-name <vault> -n exiqo-session-secret --value "$(openssl rand -hex 32)"
  az keyvault secret set --vault-name <vault> -n exiqo-internal-token --value "$(openssl rand -hex 32)"
  pw="$(openssl rand -hex 24)"
  az keyvault secret set --vault-name <vault> -n exiqo-neo4j-password --value "$pw"
  az keyvault secret set --vault-name <vault> -n exiqo-neo4j-auth --value "neo4j/$pw"
  ```

  These are kept out of Terraform so they never land in its state. The database password cannot
  be kept out, because Terraform sets it on the server.
- **Deploy-target RBAC.** Grant each `exiqo-deploy-<env>` identity what its Terraform needs on its
  subscription. Contributor is the broadest; a custom role limited to the generated modules'
  resource types is narrower:

  ```sh
  az role assignment create --assignee-object-id <deploy_identity_principal_ids.dev> \
    --assignee-principal-type ServicePrincipal --role Contributor \
    --scope /subscriptions/<dev subscription>
  ```

- **Entra authentication for Postgres.** This configuration uses password authentication,
  because the app connects with the password in `DATABASE_URL`. Entra-only authentication
  (`active_directory_auth_enabled = true`, `password_auth_enabled = false`, `exiqo-platform` as a
  Postgres Entra administrator or role) would need the app to fetch tokens for each connection.
  That is a later change.
