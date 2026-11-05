# Optional prerequisites for ExiQO Migrate on GMF's AKS (README.md). Providers are pinned to a minor
# line; .terraform.lock.hcl pins the exact builds. GMF's provider mirror goes in a CLI config
# (TF_CLI_CONFIG_FILE with a provider_installation network_mirror block), not here.

terraform {
  required_version = ">= 1.9"

  required_providers {
    azurerm = {
      source  = "hashicorp/azurerm"
      version = "~> 4.81"
    }
    random = {
      source  = "hashicorp/random"
      version = "~> 3.9"
    }
    time = {
      source  = "hashicorp/time"
      version = "~> 0.14"
    }
  }

  # Partial configuration: GMF supplies its state account at init, for example
  #   terraform init -backend-config=resource_group_name=... -backend-config=storage_account_name=... \
  #     -backend-config=container_name=tfstate -backend-config=key=exiqo-prereqs.tfstate \
  #     -backend-config=use_azuread_auth=true -backend-config=use_oidc=true
  # The state holds the generated Postgres password (also in Key Vault): keep it in a private,
  # encrypted container with access limited to the platform team.
  backend "azurerm" {}
}

provider "azurerm" {
  # ARM_SUBSCRIPTION_ID, ARM_TENANT_ID, ARM_CLIENT_ID and ARM_USE_OIDC=true from the environment.
  subscription_id = var.subscription_id

  # The backup account has shared keys disabled; data-plane calls use Entra ID.
  storage_use_azuread = true

  # GMF registers resource providers through its own subscription vending.
  resource_provider_registrations = "none"

  features {
    key_vault {
      # Purge protection is on: a destroyed vault stays soft-deleted for its retention period.
      purge_soft_delete_on_destroy    = false
      recover_soft_deleted_key_vaults = true
    }
  }
}
