#!/bin/sh
# Install ChromaBrowser into a local virtualenv and link ~/.local/bin/chroma.
set -eu

DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
cd "$DIR"

PYTHON=${PYTHON:-python3}

if ! command -v "$PYTHON" >/dev/null 2>&1; then
    echo "python3 not found. Install it with: sudo apt install python3 python3-venv" >&2
    exit 1
fi

if ! "$PYTHON" -c "import venv" >/dev/null 2>&1; then
    echo "python3-venv is missing. Install it with: sudo apt install python3-venv" >&2
    exit 1
fi

"$PYTHON" -m venv .venv
./.venv/bin/pip install --upgrade pip >/dev/null
./.venv/bin/pip install -e . >/dev/null

BIN_DIR="${HOME}/.local/bin"
mkdir -p "$BIN_DIR"
ln -sf "$DIR/bin/chroma" "$BIN_DIR/chroma"

chmod +x bin/chroma
echo "ChromaBrowser installed."
echo "  run: chroma [URL]"
case ":$PATH:" in
    *":$BIN_DIR:"*) ;;
    *) echo "  note: add $BIN_DIR to PATH to run 'chroma' from anywhere" ;;
esac
