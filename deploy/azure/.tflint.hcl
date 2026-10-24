# tflint for deploy/azure (make azure-validate). The azurerm ruleset checks SKUs, names and other
# values the provider only rejects at apply time.
config {
  call_module_type = "none"
}

plugin "terraform" {
  enabled = true
  preset  = "all"
}

plugin "azurerm" {
  enabled = true
  version = "0.32.0"
  source  = "github.com/terraform-linters/tflint-ruleset-azurerm"
}
