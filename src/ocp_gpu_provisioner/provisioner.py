"""Core provisioning logic: detect AWS, transform MachineSets, apply GPU variants."""

from __future__ import annotations

import copy
import logging
import sys
from dataclasses import dataclass, field
from typing import Any

import yaml

from .oc_client import OCClient, OCClientError

log = logging.getLogger(__name__)

GPU_ROLE = "worker-gpu"
GPU_NODE_ROLE_LABEL = f"node-role.kubernetes.io/{GPU_ROLE}"
GPU_TAINT = {
    "key": "nvidia.com/gpu",
    "value": "True",
    "effect": "NoSchedule",
}

CLUSTER_MANAGED_FIELDS = (
    "uid",
    "resourceVersion",
    "creationTimestamp",
    "generation",
    "managedFields",
)


@dataclass
class ProvisionerConfig:
    instance_type: str = "g6.xlarge"
    replicas: int = 0
    volume_size: int = 250
    dry_run: bool = False
    verbose: bool = False


def _extract_cluster_id(ms: dict[str, Any]) -> str:
    return ms["metadata"]["labels"]["machine.openshift.io/cluster-api-cluster"]


def _extract_az(ms: dict[str, Any]) -> str:
    return ms["spec"]["template"]["spec"]["providerSpec"]["value"]["placement"]["availabilityZone"]


def _is_worker_machineset(ms: dict[str, Any]) -> bool:
    """Return True if the MachineSet is a regular worker (not already GPU, infra, etc.)."""
    tpl_labels = ms.get("spec", {}).get("template", {}).get("metadata", {}).get("labels", {})
    role = tpl_labels.get("machine.openshift.io/cluster-api-machine-role", "")
    return role == "worker"


def _has_active_replicas(ms: dict[str, Any]) -> bool:
    return ms.get("spec", {}).get("replicas", 0) >= 1


def _strip_cluster_managed_fields(ms: dict[str, Any]) -> None:
    meta = ms.get("metadata", {})
    for key in CLUSTER_MANAGED_FIELDS:
        meta.pop(key, None)
    ms.pop("status", None)


def _build_gpu_machineset(
    source: dict[str, Any],
    cfg: ProvisionerConfig,
) -> dict[str, Any]:
    """Deep-copy *source* worker MachineSet and transform it into a GPU variant."""
    gpu = copy.deepcopy(source)
    cluster_id = _extract_cluster_id(source)
    az = _extract_az(source)
    gpu_name = f"{cluster_id}-{GPU_ROLE}-{az}"

    _strip_cluster_managed_fields(gpu)

    # -- metadata --------------------------------------------------------
    gpu["metadata"]["name"] = gpu_name
    annotations = gpu["metadata"].setdefault("annotations", {})
    annotations["machine.openshift.io/GPU"] = "1"
    annotations.pop("machine.openshift.io/memoryMb", None)
    annotations.pop("machine.openshift.io/vCPU", None)

    meta_labels = gpu["metadata"].setdefault("labels", {})
    meta_labels["machine.openshift.io/cluster-api-cluster"] = cluster_id

    # -- spec.replicas ---------------------------------------------------
    gpu["spec"]["replicas"] = cfg.replicas

    # -- spec.selector ---------------------------------------------------
    sel = gpu["spec"]["selector"]["matchLabels"]
    sel["machine.openshift.io/cluster-api-cluster"] = cluster_id
    sel["machine.openshift.io/cluster-api-machine-role"] = GPU_ROLE
    sel["machine.openshift.io/cluster-api-machine-type"] = GPU_ROLE
    sel["machine.openshift.io/cluster-api-machineset"] = gpu_name

    # -- spec.template.metadata.labels -----------------------------------
    tpl_labels = gpu["spec"]["template"]["metadata"]["labels"]
    tpl_labels["machine.openshift.io/cluster-api-cluster"] = cluster_id
    tpl_labels["machine.openshift.io/cluster-api-machine-role"] = GPU_ROLE
    tpl_labels["machine.openshift.io/cluster-api-machine-type"] = GPU_ROLE
    tpl_labels["machine.openshift.io/cluster-api-machineset"] = gpu_name

    # -- spec.template.spec.metadata (node-role label) -------------------
    node_meta = gpu["spec"]["template"]["spec"].setdefault("metadata", {})
    node_labels = node_meta.setdefault("labels", {})
    node_labels[GPU_NODE_ROLE_LABEL] = ""

    # -- spec.template.spec.taints (GPU taint) ---------------------------
    taints = gpu["spec"]["template"]["spec"].setdefault("taints", [])
    if not any(t.get("key") == GPU_TAINT["key"] for t in taints):
        taints.append(dict(GPU_TAINT))

    # -- providerSpec.value ----------------------------------------------
    prov = gpu["spec"]["template"]["spec"]["providerSpec"]["value"]
    prov["instanceType"] = cfg.instance_type

    for bd in prov.get("blockDevices", []):
        ebs = bd.get("ebs", {})
        if ebs:
            ebs["volumeSize"] = cfg.volume_size

    return gpu


def run(cfg: ProvisionerConfig) -> int:
    """Main entry point.  Returns 0 on success, non-zero on failure."""
    try:
        oc = OCClient()
    except OCClientError as exc:
        log.error(str(exc))
        return 1

    # -- connectivity check ----------------------------------------------
    try:
        oc.check_connection()
    except OCClientError as exc:
        log.error(str(exc))
        return 1
    log.info("Connected to OpenShift cluster.")

    # -- AWS detection ---------------------------------------------------
    try:
        platform = oc.get_platform_type()
    except OCClientError as exc:
        log.error("Failed to detect cluster platform: %s", exc)
        return 1

    if platform.upper() != "AWS":
        log.error("Cluster platform is '%s', not AWS. Aborting.", platform)
        return 1
    log.info("Cluster platform confirmed: AWS.")

    # -- fetch MachineSets -----------------------------------------------
    try:
        all_machinesets = oc.get_machinesets()
    except OCClientError as exc:
        log.error("Failed to fetch MachineSets: %s", exc)
        return 1

    worker_ms = [ms for ms in all_machinesets if _is_worker_machineset(ms)]
    active_ms = [ms for ms in worker_ms if _has_active_replicas(ms)]

    if not active_ms:
        log.error(
            "No active worker MachineSets found (replicas >= 1). "
            "Cannot determine source configuration. Aborting."
        )
        if worker_ms:
            names = [ms["metadata"]["name"] for ms in worker_ms]
            log.error(
                "Found %d worker MachineSet(s) but all have replicas=0: %s",
                len(worker_ms),
                ", ".join(names),
            )
        return 1

    log.info(
        "Found %d active worker MachineSet(s): %s",
        len(active_ms),
        ", ".join(ms["metadata"]["name"] for ms in active_ms),
    )

    # -- check for existing GPU MachineSets (idempotency) ----------------
    existing_gpu = [ms for ms in all_machinesets if GPU_ROLE in ms["metadata"]["name"]]
    existing_gpu_azs = set()
    for ms in existing_gpu:
        try:
            existing_gpu_azs.add(_extract_az(ms))
        except (KeyError, TypeError):
            continue

    if existing_gpu_azs:
        log.info(
            "GPU MachineSets already exist in AZ(s): %s",
            ", ".join(sorted(existing_gpu_azs)),
        )

    # -- build GPU MachineSets for missing AZs ---------------------------
    to_create: list[dict[str, Any]] = []
    for ms in active_ms:
        az = _extract_az(ms)
        if az in existing_gpu_azs:
            log.info("Skipping AZ %s -- GPU MachineSet already exists.", az)
            continue
        gpu_ms = _build_gpu_machineset(ms, cfg)
        to_create.append(gpu_ms)

    if not to_create:
        log.info("GPU MachineSets are already provisioned for all active AZs. Nothing to do.")
        return 0

    # -- dry-run or apply ------------------------------------------------
    if cfg.dry_run:
        log.info("Dry-run mode -- printing generated YAML to stdout.")
        for ms in to_create:
            print("---")
            print(yaml.dump(ms, default_flow_style=False).rstrip())
        return 0

    for ms in to_create:
        name = ms["metadata"]["name"]
        log.info("Applying MachineSet %s ...", name)
        try:
            output = oc.apply_yaml(ms)
            log.info("  %s", output)
        except OCClientError as exc:
            log.error("Failed to apply MachineSet %s: %s", name, exc)
            return 1

    log.info(
        "GPU MachineSet provisioning complete. "
        "MachineSets are scaled to %d replica(s). "
        "Scale up when ready with: oc scale machineset <name> -n openshift-machine-api --replicas=<N>",
        cfg.replicas,
    )
    return 0
