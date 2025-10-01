#!/usr/bin/env bash
set -euo pipefail

# Wrapper to run `rw-sync push --new` using a system conda environment.
#
# Usage:
#   ./rw_sync_push_new.sh                # use RW_CONDA_ENV, CONDA_DEFAULT_ENV, or 'base'
#   ./rw_sync_push_new.sh -e readwise    # specify env explicitly
#   ./rw_sync_push_new.sh readwise -- --max 50  # positional env + extra args after --

print_usage() {
  cat <<'USAGE'
Usage: rw_sync_push_new.sh [-e <conda_env_name_or_path>] [<conda_env_name_or_path>] [-- <extra rw-sync args>]

Environment variables:
  RW_CONDA_ENV   Default conda env name or absolute path to env (overridden by -e or positional).

Notes:
  - Runs from this directory so .env and .rw-sync.yaml are honored.
  - If 'rw-sync' is not installed in the env, falls back to `python -m reader_sync.cli` with PYTHONPATH=src.
USAGE
}

# Default env directory can be provided via environment; avoid hardcoding user paths.
DEFAULT_ENV_DIR="${RW_DEFAULT_ENV_DIR:-}"
ENV_NAME="${RW_CONDA_ENV:-}"
EXTRA_ARGS=()

while [[ $# -gt 0 ]]; do
  case "$1" in
    -e|--env)
      ENV_NAME=${2:-}
      shift 2 || true
      ;;
    --)
      shift
      EXTRA_ARGS+=("$@")
      break
      ;;
    -h|--help)
      print_usage
      exit 0
      ;;
    *)
      # Treat first bare argument as env name if not set yet
      if [[ -z "$ENV_NAME" ]]; then
        ENV_NAME="$1"
        shift
      else
        EXTRA_ARGS+=("$1")
        shift
      fi
      ;;
  esac
done

# Decide whether the provided env is a path or a name.
ENV_DIR=""
if [[ -n "$ENV_NAME" && -d "$ENV_NAME" ]]; then
  ENV_DIR="$ENV_NAME"
elif [[ -z "$ENV_NAME" ]]; then
  ENV_DIR="$DEFAULT_ENV_DIR"
elif [[ "$ENV_NAME" == "System" && -d "$DEFAULT_ENV_DIR" ]]; then
  # Backward-compatible alias: treat literal "System" as the fixed env dir
  ENV_DIR="$DEFAULT_ENV_DIR"
fi

# If we have an env directory, prefer direct execution via its bin/ without needing conda.
if [[ -n "$ENV_DIR" ]]; then
  ENV_BIN="$ENV_DIR/bin"
  if [[ ! -d "$ENV_BIN" ]]; then
    echo "[ERROR] Env path does not look like a conda env: $ENV_DIR" >&2
    exit 1
  fi
  export PATH="$ENV_BIN:$PATH"
else
  # Fall back to activating by env name via conda if no path is set.
  # Default env resolution if still empty
  if [[ -z "$ENV_NAME" ]]; then
    ENV_NAME="${CONDA_DEFAULT_ENV:-base}"
  fi

  # Locate and source conda
  find_conda_base() {
    local base
    if command -v conda >/dev/null 2>&1; then
      base=$(conda info --base 2>/dev/null || true)
      if [[ -n "$base" && -f "$base/etc/profile.d/conda.sh" ]]; then
        echo "$base"; return 0
      fi
    fi
    for guess in "$HOME/miniconda3" "$HOME/anaconda3" \
                 "/opt/homebrew/Caskroom/miniconda/base" \
                 "/opt/anaconda3" "/usr/local/anaconda3" \
                 "$HOME/miniconda"; do
      if [[ -f "$guess/etc/profile.d/conda.sh" ]]; then
        echo "$guess"; return 0
      fi
    done
    return 1
  }

  if ! CONDA_BASE=$(find_conda_base); then
    echo "[ERROR] Could not find a conda installation and no env path was provided. Set RW_CONDA_ENV to an env path or use -e <env_path>." >&2
    exit 1
  fi

  # shellcheck disable=SC1090
  source "$CONDA_BASE/etc/profile.d/conda.sh"
  conda activate "$ENV_NAME"
fi

SCRIPT_DIR=$(cd -- "$(dirname "${BASH_SOURCE[0]}")" >/dev/null 2>&1 && pwd)
pushd "$SCRIPT_DIR" >/dev/null

export RW_SYNC_CONFIG="$SCRIPT_DIR/.rw-sync.yaml"

START_TS=$(date +%s)
if command -v rw-sync >/dev/null 2>&1; then
  echo "> Running: rw-sync push --new ${EXTRA_ARGS[*]-}"
  rw-sync push --new ${EXTRA_ARGS+"${EXTRA_ARGS[@]}"}
else
  echo "[WARN] 'rw-sync' not found in env '$ENV_NAME'. Falling back to 'python -m reader_sync.cli'."
  export PYTHONPATH="$SCRIPT_DIR/src${PYTHONPATH:+:$PYTHONPATH}"
  python -m reader_sync.cli push --new ${EXTRA_ARGS+"${EXTRA_ARGS[@]}"}
fi

STATUS=$?
END_TS=$(date +%s)
ELAPSED=$((END_TS - START_TS))

popd >/dev/null

if [[ $STATUS -eq 0 ]]; then
  echo "> Sync completed in ${ELAPSED}s"
else
  echo "[ERROR] Sync failed with exit code $STATUS after ${ELAPSED}s" >&2
fi

exit $STATUS
