"""Thin wrapper around the ``oc`` CLI executed via :mod:`subprocess`."""

from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any

import yaml


class OCClientError(Exception):
    """Raised when an ``oc`` command fails."""


class OCClient:
    """Execute ``oc`` commands and return parsed output."""

    def __init__(self, oc_path: str | None = None) -> None:
        self._oc = oc_path or shutil.which("oc")
        if self._oc is None:
            raise OCClientError(
                "oc binary not found on PATH. "
                "Install it from https://mirror.openshift.com/pub/openshift-v4/clients/ocp/stable/"
            )

    def _run(self, args: list[str], *, check: bool = True) -> subprocess.CompletedProcess[str]:
        cmd = [self._oc, *args]
        try:
            return subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                check=check,
            )
        except subprocess.CalledProcessError as exc:
            raise OCClientError(
                f"oc command failed: {' '.join(cmd)}\nstderr: {exc.stderr.strip()}"
            ) from exc
        except FileNotFoundError as exc:
            raise OCClientError(f"oc binary not found at {self._oc}") from exc

    # ------------------------------------------------------------------
    # Connectivity
    # ------------------------------------------------------------------

    def check_connection(self) -> None:
        """Verify that ``oc`` can reach the cluster."""
        result = self._run(["whoami"], check=False)
        if result.returncode != 0:
            raise OCClientError(
                "Not logged in to an OpenShift cluster. Run 'oc login' first.\n"
                f"stderr: {result.stderr.strip()}"
            )

    # ------------------------------------------------------------------
    # Infrastructure
    # ------------------------------------------------------------------

    def get_platform_type(self) -> str:
        """Return the infrastructure platform type (e.g. ``AWS``, ``GCP``)."""
        result = self._run([
            "get", "infrastructure", "cluster",
            "-o", "jsonpath={.status.platformStatus.type}",
        ])
        return result.stdout.strip()

    # ------------------------------------------------------------------
    # MachineSets
    # ------------------------------------------------------------------

    def get_machinesets(self, namespace: str = "openshift-machine-api") -> list[dict[str, Any]]:
        """Return all MachineSets in *namespace* as parsed dicts."""
        result = self._run([
            "get", "machinesets",
            "-n", namespace,
            "-o", "yaml",
        ])
        data = yaml.safe_load(result.stdout)
        return data.get("items", [])

    def apply_yaml(self, manifest: dict[str, Any]) -> str:
        """Apply a single resource manifest via ``oc apply -f``."""
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".yaml", delete=False
        ) as tmp:
            yaml.dump(manifest, tmp, default_flow_style=False)
            tmp_path = Path(tmp.name)
        try:
            result = self._run(["apply", "-f", str(tmp_path)])
            return result.stdout.strip()
        finally:
            tmp_path.unlink(missing_ok=True)
