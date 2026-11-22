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
cargo build --locked -p codex-cli --bin codex
for name in saved_message_wake_checks_the_last_started_turn_during_admission recovery_rejects_active_turn_without_injecting_or_applying_settings; do
    cargo test --locked -p codex-core --lib "$name" > "$probe_dir/$name.log" || {
        cat "$probe_dir/$name.log"
        exit 1
    }
    cat "$probe_dir/$name.log"
    grep -F 'test result: ok. 1 passed;' "$probe_dir/$name.log" >/dev/null
done
