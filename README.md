# OCP GPU Provisioner for AWS

A Python CLI tool that provisions GPU-enabled worker MachineSets on AWS-backed
OpenShift clusters.  It reads existing worker MachineSets via the `oc` client,
creates GPU variants across all availability zones, and applies them to the
cluster -- idempotently and with a safe dry-run mode.

By default the new GPU MachineSets are created **scaled to 0 replicas** so no
machines are launched immediately.  This lets you review the configuration and
scale up individual MachineSets at your own pace using `oc scale`.

## Prerequisites

- **Python 3.9+**
- **`oc` CLI** (OpenShift Client) -- logged in to the target cluster

### Installing the `oc` CLI

#### Linux

```bash
# Download the latest stable client
curl -LO https://mirror.openshift.com/pub/openshift-v4/clients/ocp/stable/openshift-client-linux.tar.gz

# Extract and install
tar xzf openshift-client-linux.tar.gz
sudo mv oc kubectl /usr/local/bin/

# Verify
oc version
```

#### macOS

```bash
# Option A -- Homebrew
brew install openshift-cli

# Option B -- manual download
curl -LO https://mirror.openshift.com/pub/openshift-v4/clients/ocp/stable/openshift-client-mac.tar.gz
tar xzf openshift-client-mac.tar.gz
sudo mv oc kubectl /usr/local/bin/

# Verify
oc version
```

After installing, log in to your cluster:

```bash
oc login --server=https://<api-server>:6443 -u <user> -p <password>
# or
oc login --token=<token> --server=https://<api-server>:6443
```

## Installation

```bash
# Clone the repository
git clone <repo-url> && cd ocp-gpu-provisioner-aws

# Create a virtual environment (recommended)
python3 -m venv .venv
source .venv/bin/activate   # Linux / macOS

# Install the package in editable (development) mode
pip install -e .
```

This installs the `ocp-gpu-provisioner` command and makes the
`ocp_gpu_provisioner` package importable everywhere inside the venv.

## Usage

The tool can be invoked either as a CLI command or as a Python module:

```bash
# Preview what would be created (recommended first step)
ocp-gpu-provisioner --dry-run
# or equivalently:
python -m ocp_gpu_provisioner --dry-run

# Create GPU MachineSets scaled to 0 (default -- no machines launched yet)
ocp-gpu-provisioner

# Create GPU MachineSets and immediately scale to 1 replica per AZ
ocp-gpu-provisioner --replicas 1

# Custom GPU instance type
ocp-gpu-provisioner --instance-type g5.2xlarge

# Custom volume size
ocp-gpu-provisioner --volume-size 500

# Verbose output for troubleshooting
ocp-gpu-provisioner --dry-run -v
```

After provisioning, scale up the MachineSets you need:

```bash
# List GPU MachineSets
oc get machinesets -n openshift-machine-api | grep gpu

# Scale a specific GPU MachineSet to 1 replica
oc scale machineset <machineset-name> -n openshift-machine-api --replicas=1
```

### CLI Options

| Option | Default | Description |
|---|---|---|
| `--instance-type` | `g6.xlarge` | AWS GPU instance type |
| `--replicas` | `0` | Replicas per AZ (0 = create MachineSet but don't launch machines) |
| `--volume-size` | `250` | Root EBS volume size in GB |
| `--dry-run` | off | Print generated YAML without applying |
| `-v` / `--verbose` | off | Enable DEBUG-level logging |

## How It Works

1. **Connectivity check** -- verifies `oc` is on PATH and authenticated.
2. **Platform detection** -- queries `oc get infrastructure cluster` to confirm
   the cluster runs on AWS.  Aborts if it does not.
3. **Fetch MachineSets** -- retrieves all MachineSets from the
   `openshift-machine-api` namespace.
4. **Filter active workers** -- keeps only MachineSets whose role is `worker`
   and `spec.replicas >= 1`.  If none are found, the tool reports the issue and
   stops.
5. **Idempotency check** -- looks for existing GPU MachineSets (name contains
   `worker-gpu`) and skips availability zones that are already covered.
6. **Transform** -- for each source MachineSet, deep-copies it and applies the
   GPU transformation:
   - Strips cluster-managed fields (`uid`, `resourceVersion`, `managedFields`,
     `status`, etc.).
   - Renames to `{cluster}-worker-gpu-{az}`.
   - Sets machine role and type to `worker-gpu`.
   - Configures the requested GPU instance type and volume size.
   - Adds the `nvidia.com/gpu=True:NoSchedule` taint.
   - Adds the `node-role.kubernetes.io/worker-gpu` node label.
   - Preserves credentials, AMI, subnet, security groups, IAM profile, tags,
     and placement from the source.
7. **Apply or print** -- either prints the YAML (`--dry-run`) or applies it via
   `oc apply`.  By default the MachineSets are created with 0 replicas so no
   GPU instances are launched until you explicitly scale up with `oc scale`.

## Project Structure

```
src/ocp_gpu_provisioner/
  __init__.py        Package metadata
  __main__.py        Entry point for python -m
  cli.py             Argument parsing and logging setup
  oc_client.py       Thin wrapper around oc subprocess calls
  provisioner.py     Core logic: detect, transform, apply
```

## License

See [LICENSE](LICENSE).
