# A complete input set, to copy and fill in (every id, address and name is a placeholder, the same
# ones values.gmf.example.yaml uses). Not loaded automatically: pass -var-file=gmf.tfvars.
# make azure-validate lints the configuration with this file, so tflint checks these names.

subscription_id     = "2c3d4e5f-6a7b-4c8d-9e0f-1a2b3c4d5e6f"
resource_group_name = "rg-gmf-exiqo-prod"
location            = "eastus2"
tags = {
  cost-center = "platform-engineering"
}

acr_name                    = "gmfacr"
key_vault_name              = "kv-gmf-exiqo-prod"
postgres_server_name        = "gmf-exiqo-pg"
data_storage_account_name   = "stgmfexiqodata"
backup_storage_account_name = "stgmfexiqobackup"

# az aks show -g <rg> -n <cluster> --query oidcIssuerProfile.issuerUrl -o tsv
aks_oidc_issuer_url = "https://eastus2.oic.prod-aks.azure.com/3b2f8a64-1c2d-4e5f-9a8b-7c6d5e4f3a2b/5d6e7f80-1a2b-4c3d-8e9f-0a1b2c3d4e5f/"
namespace           = "exiqo"
deploy_environments = ["dev", "prod"]

# az aks show ... --query identityProfile.kubeletidentity.objectId / identity.principalId
aks_kubelet_identity_object_id    = "9a8b7c6d-5e4f-4a3b-9c2d-1e0f9a8b7c6d"
aks_cluster_identity_principal_id = "1f2e3d4c-5b6a-4978-8d6c-5b4a3f2e1d0c"
build_principal_id                = "4c5d6e7f-8091-4a2b-b3c4-d5e6f7a8b9c0"
signer_principal_id               = "5d6e7f80-91a2-4b3c-84d5-e6f7a8b9c0d1"
deployer_principal_id             = "6e7f8091-a2b3-4c4d-95e6-f7a8b9c0d1e2"
aks_cluster_id                    = "/subscriptions/2c3d4e5f-6a7b-4c8d-9e0f-1a2b3c4d5e6f/resourceGroups/rg-gmf-aks/providers/Microsoft.ContainerService/managedClusters/aks-gmf-prod"
deployer_aks_role                 = "" # GMF's call; e.g. "Azure Kubernetes Service RBAC Cluster Admin"
azure_openai_account_id           = "/subscriptions/2c3d4e5f-6a7b-4c8d-9e0f-1a2b3c4d5e6f/resourceGroups/rg-gmf-ai/providers/Microsoft.CognitiveServices/accounts/gmf-exiqo-aoai"

public_network_access_enabled = false
private_endpoint_subnet_id    = "/subscriptions/2c3d4e5f-6a7b-4c8d-9e0f-1a2b3c4d5e6f/resourceGroups/rg-gmf-network/providers/Microsoft.Network/virtualNetworks/vnet-gmf-aks/subnets/snet-private-endpoints"
private_dns_zone_ids = {
  vault    = "/subscriptions/2c3d4e5f-6a7b-4c8d-9e0f-1a2b3c4d5e6f/resourceGroups/rg-gmf-dns/providers/Microsoft.Network/privateDnsZones/privatelink.vaultcore.azure.net"
  registry = "/subscriptions/2c3d4e5f-6a7b-4c8d-9e0f-1a2b3c4d5e6f/resourceGroups/rg-gmf-dns/providers/Microsoft.Network/privateDnsZones/privatelink.azurecr.io"
  file     = "/subscriptions/2c3d4e5f-6a7b-4c8d-9e0f-1a2b3c4d5e6f/resourceGroups/rg-gmf-dns/providers/Microsoft.Network/privateDnsZones/privatelink.file.core.windows.net"
  blob     = "/subscriptions/2c3d4e5f-6a7b-4c8d-9e0f-1a2b3c4d5e6f/resourceGroups/rg-gmf-dns/providers/Microsoft.Network/privateDnsZones/privatelink.blob.core.windows.net"
}

postgres_delegated_subnet_id   = "/subscriptions/2c3d4e5f-6a7b-4c8d-9e0f-1a2b3c4d5e6f/resourceGroups/rg-gmf-network/providers/Microsoft.Network/virtualNetworks/vnet-gmf-aks/subnets/snet-postgres"
postgres_delegated_subnet_cidr = "10.20.30.0/28"
postgres_vnet_id               = "/subscriptions/2c3d4e5f-6a7b-4c8d-9e0f-1a2b3c4d5e6f/resourceGroups/rg-gmf-network/providers/Microsoft.Network/virtualNetworks/vnet-gmf-aks"
