#!/usr/bin/env bash
set -euo pipefail

if [ -d graphify-out ]; then
  printf 'Next action for the agent: load the graphify skill and follow it to run `/graphify . --update` in the main checkout "%s". This is a skill instruction, not a shell command.\n' "$PWD"
fi
