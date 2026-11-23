#!/bin/sh
# Build the matching upstream CLI in an isolated checkout and exercise its
# admission checks. This uses upstream test sessions, never the desktop account.
set -eu

tag=rust-v0.171.0-alpha.13
command -v cargo >/dev/null || { echo 'Rust 1.99.1 and Cargo are required.' >&2; exit 1; }
probe_dir=$(mktemp -d)
echo "Isolated build directory: $probe_dir"
git clone --depth 1 --branch "$tag" https://github.com/openai/codex.git "$probe_dir/codex"
cd "$probe_dir/codex/codex-rs"
mkdir "$probe_dir/engine-home"
export CODEX_HOME="$probe_dir/engine-home"
# Release tags stamp the workspace manifests, but the lock records local
# workspace crates at 0.0.0. Match those entries to their own manifests.
cargo metadata --no-deps --format-version 1 > "$probe_dir/workspace.json"
python3 - "$probe_dir/workspace.json" <<'PY'
import json
import re
import sys
import tomllib
from pathlib import Path

versions = {p["name"]: p["version"] for p in json.loads(Path(sys.argv[1]).read_text())["packages"]}
lock = Path("Cargo.lock")
parts = lock.read_text().split("[[package]]")
for i in range(1, len(parts)):
    entry = tomllib.loads(parts[i])
    version = versions.get(entry["name"])
    if "source" not in entry and version and entry["version"] == "0.0.0":
        parts[i] = parts[i].replace('version = "0.0.0"', f'version = "{version}"', 1)
    parts[i] = re.sub(r'^ "(\S+) 0\.0\.0", -p codex-cli --bin codex
for name in saved_message_wake_checks_the_last_started_turn_during_admission recovery_rejects_active_turn_without_injecting_or_applying_settings; do
    cargo test --locked -p codex-core --lib "$name" > "$probe_dir/$name.log" || {
        cat "$probe_dir/$name.log"
        exit 1
    }
    cat "$probe_dir/$name.log"
    grep -F 'test result: ok. 1 passed;' "$probe_dir/$name.log" >/dev/null
done
,
                      lambda m: f' "{m[1]} {versions[m[1]]}",' if m[1] in versions else m[0],
                      parts[i], flags=re.M)
lock.write_text("[[package]]".join(parts))
PY
cargo build --locked -p codex-cli --bin codex
for name in saved_message_wake_checks_the_last_started_turn_during_admission recovery_rejects_active_turn_without_injecting_or_applying_settings; do
    cargo test --locked -p codex-core --lib "$name" > "$probe_dir/$name.log" || {
        cat "$probe_dir/$name.log"
        exit 1
    }
    cat "$probe_dir/$name.log"
    grep -F 'test result: ok. 1 passed;' "$probe_dir/$name.log" >/dev/null
done
