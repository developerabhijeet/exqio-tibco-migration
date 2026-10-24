"""Pins what the exiqo chart assumes about the official Neo4j subchart's output.

    helm template ... -f values.gmf.example.yaml -f ../tests/values/backup-enabled.yaml \
        | python -I deploy/helm/tests/subchart_contract.py

helm-unittest cannot run the subchart's StatefulSet template on its own (its YAML parse of that
template fails although helm renders it), so `make chart-check` runs this on the full render:
- the Neo4j pod carries the label our NetworkPolicies and the backup's podAffinity select on;
- the bolt Service is named as NEO4J_URI expects (bolt://<service>:7687) and is ClusterIP;
- the backup mounts the claim the StatefulSet creates (<claim template>-<statefulset>-0);
- the subchart renders no ServiceAccount (and no LoadBalancer), so ours is the only exiqo-neo4j.
"""
from __future__ import annotations

import json
import sys

import yaml


def main() -> int:
    docs = [d for d in yaml.safe_load_all(sys.stdin) if d]
    kinds: dict[str, list[dict]] = {}
    for d in docs:
        kinds.setdefault(d["kind"], []).append(d)
    errors: list[str] = []

    def check(ok: bool, message: str) -> None:
        if not ok:
            errors.append(message)

    (sts,) = kinds["StatefulSet"]
    pod_labels = sts["spec"]["template"]["metadata"]["labels"]
    pod_spec = sts["spec"]["template"]["spec"]
    name = sts["metadata"]["name"]

    policies = {p["metadata"]["name"]: p for p in kinds["NetworkPolicy"]}
    neo4j_selector = policies["exiqo-neo4j-ingress"]["spec"]["podSelector"]["matchLabels"]
    check(neo4j_selector.items() <= pod_labels.items(),
          f"exiqo-neo4j-ingress selects {neo4j_selector}, the Neo4j pod has {pod_labels}")
    egress_to = policies["exiqo-neo4j-egress"]["spec"]["egress"][0]["to"][0]["podSelector"]
    check(egress_to["matchLabels"].items() <= pod_labels.items(),
          f"exiqo-neo4j-egress targets {egress_to}, the Neo4j pod has {pod_labels}")

    cm = next(c for c in kinds["ConfigMap"] if c["metadata"]["name"] == "exiqo-platform")
    uri = cm["data"]["NEO4J_URI"]
    host, _, port = uri.split("://", 1)[1].partition(":")
    service = next((s for s in kinds["Service"] if s["metadata"]["name"] == host), None)
    check(service is not None, f"NEO4J_URI {uri} names Service {host}, which is not rendered")
    if service:
        check(service["spec"].get("type", "ClusterIP") == "ClusterIP", f"Service {host} is not ClusterIP")
        check(any(p["port"] == int(port or 7687) for p in service["spec"]["ports"]),
              f"Service {host} has no port {port}")
        check(service["spec"]["selector"].items() <= pod_labels.items(),
              f"Service {host} selects {service['spec']['selector']}, not the Neo4j pod")
    check(all(s["spec"].get("type", "ClusterIP") == "ClusterIP" for s in kinds["Service"]),
          "a Service other than ClusterIP is rendered")

    (cron,) = kinds.get("CronJob", [None]) or [None]
    check(cron is not None, "render with the backup enabled (tests/values/backup-enabled.yaml)")
    if cron:
        job_pod = cron["spec"]["jobTemplate"]["spec"]["template"]["spec"]
        claim_template = sts["spec"]["volumeClaimTemplates"][0]["metadata"]["name"]
        claims = [v["persistentVolumeClaim"]["claimName"] for v in job_pod["volumes"]
                  if "persistentVolumeClaim" in v]
        check(claims == [f"{claim_template}-{name}-0"],
              f"the backup mounts {claims}, the StatefulSet's claim is {claim_template}-{name}-0")
        data_mount = next(m for m in pod_spec["containers"][0]["volumeMounts"]
                          if m["mountPath"] == "/data")
        check(data_mount["name"] == claim_template and data_mount.get("subPathExpr") == "data",
              f"Neo4j's /data is {data_mount}, the backup's dump mounts subPath data")
        affinity = job_pod["affinity"]["podAffinity"]["requiredDuringSchedulingIgnoredDuringExecution"]
        selector = affinity[0]["labelSelector"]["matchLabels"]
        check(selector.items() <= pod_labels.items(),
              f"the backup's podAffinity selects {selector}, the Neo4j pod has {pod_labels}")
        role = next(r for r in kinds["Role"] if r["metadata"]["name"] == "exiqo-neo4j-backup")
        names = {n for rule in role["rules"] for n in rule.get("resourceNames", [])}
        check(names == {name, f"{name}-0"}, f"the backup Role names {names}, not {name} / {name}-0")

    accounts = [a["metadata"]["name"] for a in kinds["ServiceAccount"]]
    check(len(accounts) == len(set(accounts)), f"duplicate ServiceAccounts: {accounts}")
    check(pod_spec.get("serviceAccountName") in accounts and "exiqo-neo4j" in accounts,
          f"Neo4j runs as {pod_spec.get('serviceAccountName')}; rendered accounts: {accounts}")

    if errors:
        print("subchart contract: FAIL\n  " + "\n  ".join(errors), file=sys.stderr)
        return 1
    print("subchart contract: ok " + json.dumps({"pod label app": pod_labels.get("app"),
                                                  "service": host, "claim": claims[0] if cron else None,
                                                  "accounts": sorted(accounts)}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
