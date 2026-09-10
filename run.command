#!/bin/sh
set -eu
cd "$(dirname "$0")"
if [ ! -x .venv/bin/python ]; then
    echo 'First run: python3 -m venv .venv && .venv/bin/python -m pip install -e .'
    exit 1
fi
exec .venv/bin/python -m smith_agents "$@"
