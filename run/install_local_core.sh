#!/usr/bin/env bash
# Run after activating bom_cases; accepts the path to a local core checkout.
set -euo pipefail
CORE_SOURCE="${1:-${HOME}/wrnz/pysteps_param}"
if [[ "${CONDA_DEFAULT_ENV:-}" != "bom_cases" ]]; then
  echo 'Activate bom_cases first: conda activate bom_cases' >&2
  exit 1
fi
if [[ ! -f "${CORE_SOURCE}/pyproject.toml" ]]; then
  echo "Core checkout not found: ${CORE_SOURCE}" >&2
  exit 1
fi
python -m pip install --no-deps --no-build-isolation --editable "$CORE_SOURCE"
python -m pip check
