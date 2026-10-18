#!/usr/bin/env bash
# Prépare l'environnement virtuel et installe les dépendances.
set -euo pipefail
cd "$(dirname "$0")/.."
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
pip install --only-binary=:all: -r requirements-dev.txt
pip install -e .
echo "Environnement prêt. Activez-le avec : source .venv/bin/activate"
