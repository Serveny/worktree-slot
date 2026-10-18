#!/usr/bin/env bash
set -euo pipefail

slot="${1:?usage: graphify.sh <absolute-slot-path> <feature-branch>}"
if [ -d graphify-out ]; then
  echo "copying graphify-out to $slot" >&2
  rm -rf -- "$slot/graphify-out"
  cp -a -- graphify-out/. "$slot/graphify-out"
  printf '%s\n' "$slot" > "$slot/graphify-out/.graphify_root"
fi
