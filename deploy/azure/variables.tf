# Inputs. Every resource group below is behind an enable_* switch, so GMF can create some of the
# prerequisites here and bring its own for the rest (then fill those values keys by hand). The
# network (VNet, subnets, private DNS for the private endpoints) and the AKS cluster are GMF's and
# come in as ids.

# ------------------------------------------------------------------------------------- scope

variable "subscription_id" {
  description = "Subscription for the prerequisites; null reads ARM_SUBSCRIPTION_ID."
  type        = string
  default     = null
}

variable "resource_group_name" {
  description = "Existing resource group for everything created here."
  type        = string
}

variable "location" {
  description = "Azure region, e.g. eastus2. Default: the resource group's."
  type        = string
  default     = null
}

variable "tags" {
  description = "Tags on every resource."
  type        = map(string)
  default     = {}
}

# ------------------------------------------------------------------------------------- switches

variable "enable_acr" {
  description = "Create the container registry (image.registry)."
  type        = bool
  default     = true
}

variable "enable_key_vault" {
  description = "Create the Key Vault (keyVault.name)."
  type        = bool
  default     = true
}

variable "enable_signing_key" {
  description = "Create the cosign signing key in the Key Vault (the release pipelines' COSIGN_KEY)."
  type        = bool
  default     = true
}

variable "enable_identities" {
  description = "Create exiqo-platform and exiqo-deploy-<environment> with federated credentials."
  type        = bool
  default     = true
}

variable "enable_postgres" {
  description = "Create the Postgres flexible server and write DATABASE_URL to the Key Vault."
  type        = bool
  default     = true
}

variable "enable_data_share" {
  description = "Create the premium Azure Files share for DATA_DIR (data.storageClass.*)."
  type        = bool
  default     = true
}

variable "enable_backup_storage" {
  description = "Create the Blob container for the Neo4j backup (neo4jBackup.*)."
  type        = bool
  default     = true
}

# ------------------------------------------------------------------------------------- names
# Globally unique names follow GMF's naming standard, so they are inputs. Required when the
# matching enable_* is true.

variable "acr_name" {
  description = "Container registry name (5-50 alphanumerics)."
  type        = string
  default     = null
}

variable "key_vault_name" {
  description = "Key Vault name (3-24: letters, digits, hyphens)."
  type        = string
  default     = null
}

variable "postgres_server_name" {
  description = "Postgres flexible server name (its host is <name>.postgres.database.azure.com)."
  type        = string
  default     = null
}

variable "data_storage_account_name" {
  description = "FileStorage account for the DATA_DIR share (3-24 lowercase alphanumerics)."
  type        = string
  default     = null
}

variable "backup_storage_account_name" {
  description = "StorageV2 account for the neo4j-backups container (3-24 lowercase alphanumerics)."
  type        = string
  default     = null
}

# ------------------------------------------------------------------------------------- cluster

variable "aks_oidc_issuer_url" {
  description = "OIDC issuer URL of GMF's AKS cluster (az aks show --query oidcIssuerProfile.issuerUrl)."
  type        = string
  default     = ""

  validation {
    condition     = var.aks_oidc_issuer_url == "" || startswith(var.aks_oidc_issuer_url, "https://")
    error_message = "aks_oidc_issuer_url must be an https URL."
  }
}

variable "namespace" {
  description = "Kubernetes namespace of the release (the federated credential subjects name it)."
  type        = string
  default     = "exiqo"
}

variable "deploy_environments" {
  description = "Deploy-step targets (deploy.environments[].name); one exiqo-deploy-<name> identity each."
  type        = list(string)
  default     = []

  validation {
    condition     = alltrue([for e in var.deploy_environments : can(regex("^[a-z0-9][a-z0-9-]{0,30}$", e))])
    error_message = "deploy_environments entries are lowercase letters, digits and hyphens (max 31)."
  }
}

variable "aks_kubelet_identity_object_id" {
  description = "Optional: the cluster's kubelet identity object id, granted AcrPull on the registry."
  type        = string
  default     = ""
}

variable "aks_cluster_identity_principal_id" {
  description = "Optional: the cluster's control-plane identity, granted Storage Account Contributor on the data account (the Azure Files CSI driver reads its key)."
  type        = string
  default     = ""
}

# The release pipelines use three identities, so no single stage holds every privilege: build/scan
# (push candidates), signer (sign and tag) and deployer (verify, then helm). Each is federated only
# to its own pipeline environment or service connection (README.md).

variable "build_principal_id" {
  description = "Optional: the release pipelines' build/scan identity, granted AcrPush on the registry (nothing else)."
  type        = string
  default     = ""
}

variable "signer_principal_id" {
  description = "Optional: the signing identity, granted AcrPush (signatures, attestations, the release tag) and Key Vault Crypto User on the signing key only."
  type        = string
  default     = ""
}

variable "deployer_principal_id" {
  description = "Optional: the deploying identity, granted AcrPull (to verify signatures). Its AKS role is GMF's call: see deployer_aks_role."
  type        = string
  default     = ""
}

variable "aks_cluster_id" {
  description = "Optional: the AKS cluster's resource id, the scope for deployer_aks_role."
  type        = string
  default     = ""
}

variable "deployer_aks_role" {
  description = "Opt-in: an AKS role for deployer_principal_id, e.g. \"Azure Kubernetes Service RBAC Cluster Admin\" (cluster scope: the chart creates a StorageClass) or \"Azure Kubernetes Service RBAC Admin\" with deployer_aks_role_scope = \"namespace\" and data.storageClass.create=false. Empty: no grant."
  type        = string
  default     = ""
}

variable "deployer_aks_role_scope" {
  description = "cluster, or namespace (the release namespace only)."
  type        = string
  default     = "cluster"

  validation {
    condition     = contains(["cluster", "namespace"], var.deployer_aks_role_scope)
    error_message = "deployer_aks_role_scope is cluster or namespace."
  }
}

variable "azure_openai_account_id" {
  description = "Optional: GMF's Azure OpenAI account id; exiqo-platform gets Cognitive Services OpenAI User on it."
  type        = string
  default     = ""
}

# ------------------------------------------------------------------------------------- network

variable "public_network_access_enabled" {
  description = "Public endpoints of the registry, vault and storage accounts. Off: reach them through private endpoints."
  type        = bool
  default     = false
}

variable "private_endpoint_subnet_id" {
  description = "Subnet for the private endpoints of the vault, registry and storage accounts; empty creates none."
  type        = string
  default     = ""
}

variable "private_dns_zone_ids" {
  description = "Optional private DNS zone ids by subresource (vault, registry, file, blob) for the endpoints' zone groups; GMF's DNS policy may register them instead."
  type        = map(string)
  default     = {}

  validation {
    condition     = alltrue([for k in keys(var.private_dns_zone_ids) : contains(["vault", "registry", "file", "blob"], k)])
    error_message = "private_dns_zone_ids keys are vault, registry, file and blob."
  }
}

variable "postgres_delegated_subnet_id" {
  description = "Subnet delegated to Microsoft.DBforPostgreSQL/flexibleServers."
  type        = string
  default     = ""
}

variable "postgres_delegated_subnet_cidr" {
  description = "That subnet's address range (postgres.cidrs, for the chart's NetworkPolicy)."
  type        = string
  default     = ""

  validation {
    condition     = var.postgres_delegated_subnet_cidr == "" || can(cidrhost(var.postgres_delegated_subnet_cidr, 0))
    error_message = "postgres_delegated_subnet_cidr must be a CIDR, e.g. 10.20.30.0/28."
  }
}

variable "postgres_private_dns_zone_id" {
  description = "Existing private DNS zone (name ending .postgres.database.azure.com); empty creates one linked to postgres_vnet_id."
  type        = string
  default     = ""
}

variable "postgres_vnet_id" {
  description = "VNet to link a created Postgres DNS zone to (the AKS VNet, or GMF's hub)."
  type        = string
  default     = ""
}

# ------------------------------------------------------------------------------------- sizing

variable "acr_zone_redundancy_enabled" {
  description = "Zone-redundant registry (Premium)."
  type        = bool
  default     = true
}

variable "key_vault_sku" {
  description = "standard or premium (premium keeps the signing key in an HSM)."
  type        = string
  default     = "premium"

  validation {
    condition     = contains(["standard", "premium"], var.key_vault_sku)
    error_message = "key_vault_sku is standard or premium."
  }
}

variable "postgres_sku_name" {
  description = "Flexible server SKU."
  type        = string
  default     = "GP_Standard_D4ds_v5"
}

variable "postgres_storage_mb" {
  description = "Server storage in MB."
  type        = number
  default     = 131072
}

variable "postgres_backup_retention_days" {
  description = "Point-in-time restore window (7-35)."
  type        = number
  default     = 14
}

variable "postgres_geo_redundant_backup_enabled" {
  description = "Geo-redundant backups (fixed at creation)."
  type        = bool
  default     = false
}

variable "postgres_high_availability" {
  description = "Zone-redundant standby."
  type        = bool
  default     = false
}

variable "postgres_zone" {
  description = "Availability zone of the primary; null lets Azure choose."
  type        = string
  default     = null
}

variable "postgres_admin_login" {
  description = "Administrator login; the app connects as this user (Entra auth is the documented alternative)."
  type        = string
  default     = "exiqoadmin"
}

variable "data_replication_type" {
  description = "LRS or ZRS for the premium file share."
  type        = string
  default     = "ZRS"

  validation {
    condition     = contains(["LRS", "ZRS"], var.data_replication_type)
    error_message = "data_replication_type is LRS or ZRS (premium files)."
  }
}

variable "data_share_quota_gb" {
  description = "Share size in GiB (premium: at least 100; data.size)."
  type        = number
  default     = 200

  validation {
    condition     = var.data_share_quota_gb >= 100
    error_message = "A premium share is at least 100 GiB."
  }
}

variable "backup_replication_type" {
  description = "Replication of the backup account."
  type        = string
  default     = "GRS"
}

variable "backup_retention_days" {
  description = "Neo4j dumps older than this are deleted by the lifecycle policy."
  type        = number
  default     = 30
}

# ------------------------------------------------------------------------------------- Key Vault objects
# Must match keyVault.secrets in the chart values (these are its defaults).

variable "secret_names" {
  description = "Key Vault secret names written here: database_url, test_database_url."
  type = object({
    database_url      = optional(string, "exiqo-database-url")
    test_database_url = optional(string, "exiqo-test-database-url")
  })
  default = {}
}

variable "write_test_database_url" {
  description = "Also create the exiqo_test database and its TEST_DATABASE_URL secret (keyVault.secrets.testDatabaseUrl)."
  type        = bool
  default     = true
}

variable "signing_key_name" {
  description = "Name of the cosign key in the Key Vault."
  type        = string
  default     = "exiqo-cosign"
}

variable "grant_runner_key_vault_access" {
  description = "Grant the identity running Terraform Key Vault Secrets Officer (and Crypto Officer for the signing key) so it can write the secrets."
  type        = bool
  default     = true
}
