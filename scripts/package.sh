#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DIST_DIR="$ROOT_DIR/dist"
ARCHIVE_PATH="$DIST_DIR/wellhabit.zip"

mkdir -p "$DIST_DIR"

find "$ROOT_DIR" -type d -name '__pycache__' -prune -exec rm -rf {} +
find "$ROOT_DIR" -type f \( -name '*.pyc' -o -name '*.pyo' -o -name '*.pyd' \) -delete

cd "$ROOT_DIR"
rm -f "$ARCHIVE_PATH"
zip -r "$ARCHIVE_PATH" . \
  -x '.git/*' \
  -x 'dist/*' \
  -x 'instance/' \
  -x 'instance/*' \
  -x 'logs/' \
  -x 'logs/*' \
  -x '*.pyc' \
  -x '*.pyo' \
  -x '*.pyd' \
  -x '*__pycache__*' \
  -x '.env' \
  -x '.env.*' \
  -x '.venv/*' \
  -x 'venv/*' \
  -x 'node_modules/*' \
  -x '.vscode/*' \
  -x '.idea/*' \
  -x '*.sqlite' \
  -x '*.sqlite3' \
  -x '*.sqlite-shm' \
  -x '*.sqlite-wal' \
  -x '*.db' \
  -x '.DS_Store' \
  -x 'Thumbs.db'

echo "Created $ARCHIVE_PATH"
