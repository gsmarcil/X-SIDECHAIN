#!/bin/sh
# Install X-SIDECHAIN into a virtualenv inside this repository.
#
# The script changes nothing outside this directory, asks for no privileges,
# and installs no system packages. When a prerequisite is missing it says which
# one and how to install it, rather than trying to do it for you.
#
# Re-running is safe: an existing virtualenv is reused and an existing
# configuration is never overwritten.
set -eu

STEPS=8
RECREATE=0
SMOKE=1

repo=$(cd "$(dirname "$0")" && pwd)
venv=$repo/.venv
config=$repo/x-sidechain.json
example=$repo/x-sidechain.example.json

if [ -t 1 ]; then
    green=$(printf '\033[32m'); red=$(printf '\033[31m')
    dim=$(printf '\033[2m'); reset=$(printf '\033[0m')
else
    green=''; red=''; dim=''; reset=''
fi

step() { printf '%s[%s/%s]%s %s\n' "$dim" "$1" "$STEPS" "$reset" "$2"; }
ok()   { printf '      %sOK%s   %s\n' "$green" "$reset" "$1"; }
note() { printf '      %s%s%s\n' "$dim" "$1" "$reset"; }
fail() {
    printf '      %sFAIL%s %s\n' "$red" "$reset" "$1" >&2
    shift
    for line in "$@"; do printf '           %s\n' "$line" >&2; done
    exit 1
}

usage() {
    cat <<'USAGE'
Usage: ./install.sh [OPTIONS]

Install X-SIDECHAIN into .venv inside this repository.

Options:
  --recreate   delete .venv and rebuild it from scratch
  --no-smoke   skip the final config-validation smoke test
  --help       show this message

The script never uses sudo, never installs system packages, and never
overwrites an existing x-sidechain.json.
USAGE
}

while [ $# -gt 0 ]; do
    case $1 in
        --recreate) RECREATE=1 ;;
        --no-smoke) SMOKE=0 ;;
        -h|--help)  usage; exit 0 ;;
        *) printf 'unknown option: %s\n\n' "$1" >&2; usage >&2; exit 2 ;;
    esac
    shift
done

if [ "$(id -u)" = "0" ] && [ -z "${X_SIDECHAIN_ALLOW_ROOT:-}" ]; then
    fail "running as root" \
        "A root-owned .venv and configuration break the tool for your normal user," \
        "and X-SIDECHAIN keeps its workspaces owner-only (0700) on purpose." \
        "Run this as yourself, without sudo." \
        "If this machine really has no other user, set X_SIDECHAIN_ALLOW_ROOT=1."
fi

# ---------------------------------------------------------------- 1. platform
step 1 "Checking the platform"
system=$(uname -s)
[ "$system" = "Linux" ] || fail "X-SIDECHAIN targets Linux; this is $system" \
    "The audit log and workspace permissions rely on POSIX ownership."
ok "Linux"

# ------------------------------------------------------------------ 2. python
step 2 "Checking Python"
python=""
for candidate in python3.13 python3.12 python3.11 python3; do
    command -v "$candidate" >/dev/null 2>&1 || continue
    if "$candidate" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)' 2>/dev/null; then
        python=$candidate
        break
    fi
done
if [ -z "$python" ]; then
    found=$(python3 --version 2>&1 || echo "none found")
    fail "Python 3.11 or newer is required; yours is too old ($found)" \
        "Debian/Ubuntu: sudo apt install python3.11" \
        "Fedora:        sudo dnf install python3.11" \
        "Arch:          sudo pacman -S python" \
        "Then run ./install.sh again."
fi
ok "$("$python" --version 2>&1) at $(command -v "$python")"

# -------------------------------------------------------------------- 3. venv
step 3 "Checking the venv module"
"$python" -c 'import venv' 2>/dev/null || fail "the venv module is missing" \
    "Debian/Ubuntu ship it separately:" \
    "  sudo apt install $(basename "$python")-venv" \
    "Then run ./install.sh again."
ok "venv is available"

# ------------------------------------------------------------ 4. virtualenv
step 4 "Preparing the virtualenv"
if [ "$RECREATE" = "1" ] && [ -d "$venv" ]; then
    rm -rf "$venv"
    note "removed the previous .venv"
fi
if [ -x "$venv/bin/python" ]; then
    ok "reusing .venv"
else
    rm -rf "$venv"
    "$python" -m venv "$venv" || fail "could not create the virtualenv at .venv" \
        "If the module is present but ensurepip is not, install your" \
        "distribution's $(basename "$python")-venv package and try again."
    ok "created .venv"
fi
vpython=$venv/bin/python
[ -x "$vpython" ] || fail ".venv exists but has no interpreter" \
    "Rebuild it: ./install.sh --recreate"

# ------------------------------------------------------------------ 5. pip
step 5 "Upgrading pip, setuptools and wheel"
"$vpython" -m pip --version >/dev/null 2>&1 || "$vpython" -m ensurepip --upgrade >/dev/null 2>&1 || \
    fail "pip is missing from the virtualenv and ensurepip is not available" \
        "Debian/Ubuntu: sudo apt install $(basename "$python")-venv" \
        "Then: ./install.sh --recreate"
attempt=1
while [ "$attempt" -le 3 ]; do
    if "$vpython" -m pip install --quiet --upgrade pip setuptools wheel; then
        break
    fi
    [ "$attempt" = 3 ] && fail "pip upgrade failed 3 times" \
        "Check your network or proxy settings, then run ./install.sh again."
    note "attempt $attempt failed; retrying"
    sleep $((attempt * 2))
    attempt=$((attempt + 1))
done
ok "$("$vpython" -m pip --version | cut -d' ' -f1-2)"

# --------------------------------------------------------------- 6. package
step 6 "Installing X-SIDECHAIN (editable)"
"$vpython" -m pip install --quiet --editable "$repo" || fail "pip install -e . failed" \
    "Read pip's error above. The usual causes are no network access," \
    "or a setuptools older than this project needs." \
    "A clean rebuild often fixes it: ./install.sh --recreate"
ok "installed $("$venv/bin/x-sidechain" --version 2>/dev/null || echo "the package")"

# ----------------------------------------------------------------- 7. assets
step 7 "Verifying the installed package"
[ -x "$venv/bin/x-sidechain" ] || fail "the x-sidechain command was not installed" \
    "Expected it at .venv/bin/x-sidechain." \
    "Rebuild: ./install.sh --recreate"
"$vpython" - <<'PY' || fail "the web assets are not packaged" \
    "The ui command serves its page from the installed package." \
    "Rebuild: ./install.sh --recreate"
import sys
from importlib.resources import files
web = files("x_sidechain") / "web"
missing = [name for name in ("index.html", "app.js", "live.js", "styles.css", "favicon.png")
           if not (web / name).is_file()]
if missing:
    print("missing:", ", ".join(missing), file=sys.stderr)
    raise SystemExit(1)
PY
ok "command and web assets in place"

# ----------------------------------------------------------------- 8. config
step 8 "Configuration"
if [ -f "$config" ]; then
    ok "keeping your existing x-sidechain.json"
else
    cp "$example" "$config"
    chmod 600 "$config"
    ok "created x-sidechain.json from the example"
    note "edit it to add your providers and agents (see docs/CONFIGURATION.md)"
fi
if [ "$SMOKE" = "1" ]; then
    # The example is validated, not your file: yours is a template until you
    # have filled it in, and a template that fails is not an install error.
    "$venv/bin/x-sidechain" validate-config --config "$example" >/dev/null \
        || fail "the smoke test failed: the shipped example does not validate" \
            "This is a bug in the package, not in your setup."
    ok "smoke test passed"
else
    note "smoke test skipped"
fi

cat <<EOF

${green}X-SIDECHAIN is installed.${reset}

  . .venv/bin/activate
  x-sidechain ui --no-browser --config x-sidechain.json

Activation is per-shell. Without it, call ${dim}.venv/bin/x-sidechain${reset} directly.
EOF
