"""Allow running the package with ``python -m ocp_gpu_provisioner``."""

import sys

from .cli import main

sys.exit(main())
