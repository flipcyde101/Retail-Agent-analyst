#!/usr/bin/env bash
# From a fresh clone to a loaded database, in one command:
#
#   ./setup.sh            the real data: downloads the Open Prices dumps (about 100 MB)
#   ./setup.sh --sample   a dozen made-up rows, to try it in a minute without the download
#
# Needs uv, Docker and curl. Safe to run again: it keeps an existing .env and the dumps
# already downloaded, and reloads the tables. It only touches the local Docker database.
set -euo pipefail
cd "$(dirname "$0")"

SAMPLE=0
case "${1:-}" in
  "") ;;
  --sample) SAMPLE=1 ;;
  *) echo "Usage: ./setup.sh [--sample]" >&2; exit 2 ;;
esac

need() {
  command -v "$1" >/dev/null 2>&1 || { echo "Missing $1: install it from $2" >&2; exit 1; }
}
need uv https://docs.astral.sh/uv/
need docker https://docs.docker.com/get-docker/
need curl https://curl.se/

secret() { LC_ALL=C tr -dc 'a-z0-9' </dev/urandom | head -c 24 || true; }

# 1. Settings: .env from the example, with random database passwords.
if [ ! -f .env ]; then
  sed -e "s/^POSTGRES_PASSWORD=.*/POSTGRES_PASSWORD=$(secret)/" \
      -e "s/^AGENT_DB_PASSWORD=.*/AGENT_DB_PASSWORD=$(secret)/" \
      .env.example > .env
  echo "Created .env with random database passwords."
fi

# The loader drops and recreates the tables: never point it at a hosted database from here.
if grep -qE '^ADMIN_DATABASE_URL=.+' .env || [ -n "${ADMIN_DATABASE_URL:-}" ]; then
  echo "ADMIN_DATABASE_URL is set: this script only sets up the local database." >&2
  echo "For a hosted one, see \"Deploy\" in the README." >&2
  exit 1
fi

# 2. The database, waiting until it accepts connections.
docker compose up -d --wait

# 3. Python 3.11 and the dependencies, exactly as uv.lock pins them.
uv sync --locked

# 4. The data.
if [ "$SAMPLE" = 1 ]; then
  uv run python -m data.sample
else
  mkdir -p data/raw
  for dump in prices locations products; do
    file="data/raw/$dump.jsonl.gz"
    if [ ! -s "$file" ]; then
      echo "Downloading $dump..."
      curl -fL --retry 3 -o "$file.part" "https://prices.openfoodfacts.org/data/$dump.jsonl.gz"
      mv "$file.part" "$file"
    fi
  done
  uv run python -m data.load
fi

# 5. What is left: the model's key, which only you can add.
echo
if grep -qE '^[A-Z_]*API_KEY=.+' .env || [ -n "${ANTHROPIC_API_KEY:-}" ]; then
  echo "Ready. Ask it something:"
else
  echo "Ready. Add your model's API key to .env (ANTHROPIC_API_KEY=...), then ask it something:"
fi
echo '  uv run python -m agent.cli "Which chain has the cheapest whole milk per litre?"'
echo '  uv run streamlit run app/streamlit_app.py'
