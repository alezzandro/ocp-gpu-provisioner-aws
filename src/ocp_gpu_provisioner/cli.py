"""Command-line interface for ocp-gpu-provisioner."""

from __future__ import annotations

import argparse
import logging
import sys

from .provisioner import ProvisionerConfig, run


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ocp-gpu-provisioner",
        description=(
            "Provision GPU-enabled worker MachineSets on an AWS-backed "
            "OpenShift cluster.  Reads existing worker MachineSets via the "
            "'oc' CLI and creates GPU variants across all availability zones."
        ),
    )
    parser.add_argument(
        "--instance-type",
        default="g6.xlarge",
        help="AWS GPU instance type (default: %(default)s)",
    )
    parser.add_argument(
        "--replicas",
        type=int,
        default=0,
        help=(
            "Number of GPU machine replicas per availability zone (default: %(default)s). "
            "MachineSets are created scaled to 0 so you can scale them up later "
            "with 'oc scale'."
        ),
    )
    parser.add_argument(
        "--volume-size",
        type=int,
        default=250,
        help="Root EBS volume size in GB (default: %(default)s)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        default=False,
        help="Print the generated MachineSet YAML to stdout without applying",
    )
    parser.add_argument(
        "-v", "--verbose",
        action="store_true",
        default=False,
        help="Enable verbose (DEBUG-level) logging",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    level = logging.DEBUG if args.verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(levelname)s: %(message)s",
    )

    cfg = ProvisionerConfig(
        instance_type=args.instance_type,
        replicas=args.replicas,
        volume_size=args.volume_size,
        dry_run=args.dry_run,
        verbose=args.verbose,
    )

    return run(cfg)


def entry_point() -> None:
    """Wrapper used by the ``ocp-gpu-provisioner`` console script."""
    sys.exit(main())
