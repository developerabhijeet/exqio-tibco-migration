"""deploy/azure creates the federated credentials of one identity one at a time.

Azure answers parallel credential writes on one managed identity with 409
ConcurrentFederatedIdentityCredentialsWritesForSingleManagedIdentity, so a first ``terraform
apply`` that creates them in parallel (a ``for_each`` or ``count`` > 1) fails. ``terraform test``
cannot see the dependency graph, so this reads main.tf: per identity, every credential resource
must be single-instance and depend on the one before it.
"""
from __future__ import annotations

import re
from pathlib import Path

MAIN = Path(__file__).resolve().parents[2] / "deploy" / "azure" / "main.tf"
RESOURCE = re.compile(r'^resource "azurerm_federated_identity_credential" "(\w+)" \{\n(.*?)^\}',
                      re.MULTILINE | re.DOTALL)


def _credentials() -> list[tuple[str, str]]:
    return RESOURCE.findall(MAIN.read_text(encoding="utf-8"))


def test_platform_credentials_are_chained_in_order():
    platform = [(name, body) for name, body in _credentials()
                if "azurerm_user_assigned_identity.platform[0].id" in body]
    assert len(platform) == 5
    for i, (name, body) in enumerate(platform):
        assert "for_each" not in body, f"{name}: for_each creates credentials in parallel"
        assert re.search(r"count\s*=\s*var\.enable_identities \? 1 : 0", body), name
        assert f"local.platform_service_accounts[{i}]" in body, name
        if i:
            prev = platform[i - 1][0]
            assert re.search(rf"depends_on\s*=\s*\[azurerm_federated_identity_credential\.{prev}\]",
                             body), f"{name} must depend on {prev}"


def test_other_credentials_have_one_per_identity():
    """``deploy`` iterates over identities, one credential each: no two writes share an identity."""
    others = {name: body for name, body in _credentials()
              if "azurerm_user_assigned_identity.platform[0].id" not in body}
    assert set(others) == {"deploy"}
    assert "azurerm_user_assigned_identity.deploy[each.key].id" in others["deploy"]
