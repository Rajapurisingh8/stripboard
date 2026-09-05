---
name: Python environment setup
description: How to install and run Python dependencies in this Nix-backed workspace.
---

The system Python environment is immutable, so project Python dependencies should be installed in a local uv-managed virtual environment and scripts run through that environment.

**Why:** Global pip and uv installs targeting the Nix interpreter are blocked by the externally managed, read-only store.

**How to apply:** Create or reuse `.venv`, install with uv against `.venv/bin/python`, and invoke scripts with `.venv/bin/python`.