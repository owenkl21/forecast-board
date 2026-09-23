#!/bin/bash
# Run the website locally with the settings in .env. Listens on every interface so the hub can
# reach it over Tailscale while we test the push; everything behind a PIN or the hub token.
cd "$(dirname "$0")"
set -a; . ./.env; set +a
exec .venv/bin/uvicorn app:app --host 0.0.0.0 --port 8000
