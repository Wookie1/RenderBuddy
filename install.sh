#!/bin/bash
# Sets up RenderBuddy in this folder: a private Python environment with its one dependency.
set -e
cd "$(dirname "$0")"
python3 -m venv --clear .venv
.venv/bin/pip install -q --upgrade pip
.venv/bin/pip install -q -r requirements.txt
.venv/bin/python renderbuddy.py info
echo
echo "Installed. Next: .venv/bin/python renderbuddy.py pair <CODE>   then   .venv/bin/python renderbuddy.py run"
