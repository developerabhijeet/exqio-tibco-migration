# Configure the Deploy step's Azure access

The Deploy step runs `terragrunt run --all -- plan` over the infrastructure code the platform
generated for a migrated service, against one of GM Financial's Azure subscriptions. This guide
sets up the access that plan uses. It never involves a secret stored by the platform: GMF's
install does not accept `DEPLOY_CREDENTIAL_MODE=local` (the chart's schema rejects it).

The platform plans only. Applying the plan goes through the generated pipeline with its own
identity, not through this one; the roles below are therefore sized for planning.

Two ways to authenticate, one per environment:

| Mode (`auth`) | How it works | Secret to manage |
|---|---|---|
| `workload_identity` (default) | The worker pod's Kubernetes service account `exiqo-worker` is federated to a user-assigned managed identity for that environment. Terraform runs with `ARM_USE_OIDC=true`, `ARM_CLIENT_ID` set to that identity and the projected token file; Entra exchanges the token. | None |
| `keyvault_sp` | A service principal whose secret is a Key Vault secret. The worker reads it at plan time with its own identity, hands it to the Terraform child process only, and never stores or logs it. | The service principal secret, in Key Vault |

Use Workload Identity wherever the target tenant allows federation from the AKS cluster's OIDC
issuer. Use the Key Vault service principal where it does not.

## Per-environment identity (Workload Identity)

Create one identity per target environment (for example `dev` and `prod`), so a plan for one
environment cannot reach another. If you use `deploy/azure`, its `enable_identities` switch and
`deploy_environments` variable create the identity and the federated credential for you (it grants
no role on the target subscription); the commands below are the manual equivalent.

```sh
# 1. The identity (any resource group GMF keeps platform identities in)
az identity create -g <identity-rg> -n exiqo-deploy-dev

# 2. A federated credential trusting the worker's service account on this cluster
az identity federated-credential create -g <identity-rg> --identity-name exiqo-deploy-dev \
  --name exiqo-worker-exiqo \
  --issuer "$(az aks show -g <aks-rg> -n <aks> --query oidcIssuerProfile.issuerUrl -o tsv)" \
  --subject system:serviceaccount:exiqo:exiqo-worker \
  --audiences api://AzureADTokenExchange

# 3. The ids you need next
az identity show -g <identity-rg> -n exiqo-deploy-dev --query '{clientId:clientId, principalId:principalId}'
```

The subject is `system:serviceaccount:<namespace>:exiqo-worker`: the namespace of the install,
and the fixed service account name. Entra matches the subject exactly, so a second namespace (or a
renamed one) needs its own credential. The worker pod has a single projected token; Terraform
exchanges it for whichever identity `ARM_CLIENT_ID` names, and Entra accepts the exchange because
that identity trusts the `exiqo-worker` subject. That is why every deploy identity is federated to
the same account.

The consequence: whatever runs in the worker pod can make that exchange, and the worker runs
generated code (`dotnet test`, `dotnet run`, and the generated Terraform providers and modules of
the plan) under its own user. That code can read the projected token and obtain a token for any
environment's deploy identity and for `exiqo-platform`, as well as read the secrets mounted in the
worker (GMF-INSTALL.md, "Known trust boundary: generated code on the worker"). Every deploy
identity must therefore hold only what a plan needs (Reader, or the custom role below, plus the
state and data-plane reads), never write or role-assignment rights; one identity per environment
still keeps a plan's configuration from naming another environment by mistake, but it is not an
isolation boundary against code on the worker. Moving the untrusted execution into a separate pod
without mounted secrets or a platform identity is the planned hardening.

### Role assignment

Grant the identity access on the target subscription (or a narrower scope). Start with the narrow
custom role and fall back to Contributor only when a custom role is impractical.

**Option 1, a narrow custom role (preferred).** A plan reads existing resources and registers
nothing else, so read access plus the provider registration action is enough:

```sh
cat > exiqo-deploy-plan-role.json <<'JSON'
{
  "Name": "ExiQO deploy plan (dev)",
  "Description": "Read access for terragrunt plan of ExiQO-generated infrastructure",
  "Actions": [
    "*/read",
    "*/register/action"
  ],
  "AssignableScopes": ["/subscriptions/<dev subscription>"]
}
JSON
az role definition create --role-definition @exiqo-deploy-plan-role.json
az role assignment create --assignee-object-id <principalId> --assignee-principal-type ServicePrincipal \
  --role "ExiQO deploy plan (dev)" --scope /subscriptions/<dev subscription>
```

The registration action is there because the generated provider block is `features {}` with
the azurerm provider's default behavior, which tries to register resource providers on the
subscription. Either keep `*/register/action` in the role, or list only the providers the generated
modules use (`Microsoft.Resources`, `Microsoft.ManagedIdentity`, `Microsoft.Authorization`,
`Microsoft.KeyVault`, `Microsoft.ContainerRegistry`, `Microsoft.App`,
`Microsoft.DBforPostgreSQL`, `Microsoft.ServiceBus`, `Microsoft.OperationalInsights`,
`Microsoft.Insights`, `Microsoft.ContainerService`), each as `<Namespace>/register/action`, or turn
auto-registration off in the
generated provider configuration (`resource_provider_registrations = "none"` in the azurerm 4.x
provider block, in the generated `root.hcl`) and register the providers once yourself. Without one
of the two, the first plan fails with a provider registration error.

Also needed, whatever the option:

- **State access.** The generated Terragrunt keeps state in an Azure Storage account with Azure AD
  authentication. The identity needs Storage Blob Data Contributor on the state container (a plan
  takes the state lock, a blob lease).
- **Data-plane reads.** Where a resource's data plane is under RBAC (Key Vault secrets, for
  example), a refresh needs a data role such as Key Vault Secrets User on that vault.

If GMF's own pipeline will reuse this identity to apply, it needs write actions too. The example
role in `deploy/azure/README.md` ("Deploy-target RBAC") lists the write actions for the resource
providers the generated modules use (resource groups, Web, Service Bus, Application Insights),
and the generated modules also create role assignments, which Contributor cannot do: add
`Microsoft.Authorization/roleAssignments/write` and `.../delete` (or User Access Administrator or
Role Based Access Control Administrator, scoped as tightly as possible) for that identity.

**Option 2, Contributor on a resource group.**

```sh
az role assignment create --assignee-object-id <principalId> --assignee-principal-type ServicePrincipal \
  --role Contributor --scope /subscriptions/<sub>/resourceGroups/<resource-group>
```

Scope it to a resource group, never the subscription, and note the limit: the role can only be
assigned to a group that already exists, while the generated `resource_group` module creates the
group. A resource-group scope works when the group is pre-created (and imported into state) by the
platform team.

Role assignments and federated credentials can take a few minutes to take effect.

## Declare the environment in the Helm values

The Deploy tab offers only the environments declared in `deploy.environments`, and each needs the
identity's client ID, either in `identity.deployIdentities` or as `clientId` on the entry:

```yaml values
deploy:
  credentialMode: workload_identity
  environments:
    - name: dev
      subscription: 2c3d4e5f-6a7b-4c8d-9e0f-1a2b3c4d5e6f
      auth: workload_identity
    - name: prod
      subscription: 5e6f7a8b-9c0d-4e1f-8a2b-3c4d5e6f7a8b
      auth: workload_identity
identity:
  deployIdentities:
    dev: 0d9e8f7a-6b5c-4d3e-a2f1-0e9d8c7b6a50
    prod: 7c6b5a4f-3e2d-4c1b-a0f9-e8d7c6b5a4f3
```

- `name`: lower-case letters, digits and hyphens. It must match an environment the generated
  infrastructure has (`infra/live/<name>` in the build); otherwise the plan stops with "deploy
  environment ... is not among the generated live environments".
- `tenant` defaults to `auth.tenantId`; set it only for a target in another tenant.
- `subscription` is the target subscription's GUID.
- If you use `deploy/azure`, make the names match its `deploy_environments` variable: its
  `helm_values` output fills `identity.deployIdentities` from the same names.

Apply with `helm upgrade` (`GMF-INSTALL.md`, section 7). The worker pods restart with the new
`DEPLOY_ENVIRONMENTS` setting. `GET /api/deploy/environments` returns the declared environments
(where each secret lives, never a value).

In the app, the Deploy tab's Connect Azure form offers only the declared environments and shows
the tenant, subscription, client ID and authentication mode from the declaration (and, for the Key
Vault mode, the secret name). Run the plan from the same tab.

## The Key Vault service principal alternative

Use this when the target tenant cannot federate with the cluster. The platform never stores the
secret: at each plan the worker reads it from Key Vault, passes it to the Terraform child process
as `ARM_CLIENT_SECRET`, and redacts it from the plan output.

1. Create the service principal with a role from the previous section, and store its secret in Key
   Vault (the vault may be the platform's or another one):

   The secret goes through a mode-600 temporary file and `--file`, never `--value`, so it is not
   visible in the process list:

   ```sh
   umask 077
   f="$(mktemp)"
   sp="$(az ad sp create-for-rbac --name exiqo-deploy-prod-sp --years 1 \
     --role "ExiQO deploy plan (prod)" --scopes /subscriptions/<prod subscription> -o json)"
   printf '%s' "$sp" | python3 -c 'import json,sys; print(json.load(sys.stdin)["password"], end="")' >"$f"
   az keyvault secret set --vault-name <vault> -n exiqo-deploy-prod-sp --file "$f" --output none
   printf '%s' "$sp" | python3 -c 'import json,sys; print(json.load(sys.stdin)["appId"])'  # the clientId
   unset sp
   shred -u "$f" 2>/dev/null || rm -f "$f"; unset f
   ```

2. Let the worker read it. The worker reads Key Vault with its Workload Identity, which is the
   `exiqo-platform` identity: grant it Key Vault Secrets User on that vault (it already has it on
   `keyVault.name`). The vault's host must be reachable (`*.vault.azure.net` is in `egress.fqdns`).
3. Declare it:

   ```yaml values
   deploy:
     environments:
       - name: prod
         subscription: 5e6f7a8b-9c0d-4e1f-8a2b-3c4d5e6f7a8b
         auth: keyvault_sp
         clientId: 11111111-2222-4333-8444-555555555555
         keyVaultUri: https://kv-gmf-exiqo-prod.vault.azure.net
         secretName: exiqo-deploy-prod-sp
   ```

   The schema requires `keyVaultUri` (https) and `secretName` for this mode. `clientId` is the
   service principal's application ID.

An environment may use either mode; they can be mixed in one install.

## Rotation and revocation

- **Managed identities have no secret.** Nothing expires. Review the role assignments on a schedule
  and remove an identity's federated credential or role assignment to revoke access at once.
- **Key Vault service principal.** Before the service principal's secret expires, add a second
  secret and write it to Key Vault as a new version, again through a mode-600 file:

  ```sh
  umask 077; f="$(mktemp)"
  az ad app credential reset --id <clientId> --append --years 1 --query password -o tsv >"$f"
  tr -d '\n' <"$f" >"$f.v" && mv "$f.v" "$f"
  az keyvault secret set --vault-name <vault> -n exiqo-deploy-prod-sp --file "$f" --output none
  shred -u "$f" 2>/dev/null || rm -f "$f"; unset f
  ```

  The next plan reads the latest version (nothing is cached), so no restart is needed. Then delete
  the old credential, naming it by key ID:

  ```sh
  az ad app credential list --id <clientId> --query '[].{keyId:keyId, end:endDateTime}' -o table
  az ad app credential delete --id <clientId> --key-id <old keyId>
  ```
- **Moving the install** to another namespace or cluster: create a new federated credential with
  the new subject (and issuer, for a new cluster) on each deploy identity before cutting over.
- **Removing an environment:** delete it from `deploy.environments` and `identity.deployIdentities`,
  run `helm upgrade`, then delete the federated credential and role assignment.

## Troubleshooting

| Message or symptom | Cause |
|---|---|
| "Workload Identity is not configured for this pod (AZURE_FEDERATED_TOKEN_FILE missing)" | The worker pod does not carry the projected token. Check Workload Identity on the cluster and the pod label `azure.workload.identity/use`. |
| Authentication error AADSTS70021 ("no matching federated identity record") | The credential's issuer or subject does not match the cluster's issuer URL and `system:serviceaccount:<namespace>:exiqo-worker`, or it is not yet propagated. |
| Authorization error (`AuthorizationFailed`) during plan | The identity lacks a role on that scope (read, or a data-plane role), or on the state container. |
| Provider registration error | Add the registration action to the role or disable auto-registration (see above). |
| "deploy environment ... is no longer declared" | The environment was removed or renamed after the plan was queued; connect the Deploy tab again and plan. |
| "cannot read secret ... from Key Vault" | The worker's identity lacks Key Vault Secrets User on that vault, the vault is unreachable from the cluster, or the secret name is wrong. The message names the vault and secret, never a value. |
