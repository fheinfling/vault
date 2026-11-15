#!/usr/bin/env bash
# Linux only: the swift.org toolchain includes libFuzzer; Apple's does not.
set -euo pipefail
cd "$(dirname "$0")/.."

seconds="${1:-60}"
if (( $# > 1 )) || [[ ! "$seconds" =~ ^[1-9][0-9]{0,4}$ ]] || (( seconds > 86400 )); then
  echo "usage: $0 [seconds: 1..86400] (default: 60)" >&2
  exit 2
fi
if [[ "$(uname -s)" != Linux ]]; then
  echo "CSV fuzzing needs the swift.org Linux toolchain; see Fuzz/README.md for Docker." >&2
  exit 2
fi

# Separate from the root .build (which may contain macOS or unsanitized objects).
build_args=(--package-path Fuzz --scratch-path Fuzz/.build -c debug --product CSVFuzz
  -Xswiftc -sanitize=fuzzer,address -Xswiftc -parse-as-library)
swift build "${build_args[@]}"
bin_dir="$(swift build "${build_args[@]}" --show-bin-path)"
mkdir -p Fuzz/.build/corpus Fuzz/.build/artifacts
cp Fuzz/Corpus/* Fuzz/.build/corpus/

# Keep mutations and reproducers out of the checked-in seeds. exec preserves failures.
exec "$bin_dir/CSVFuzz" Fuzz/.build/corpus \
  -max_total_time="$seconds" -max_len=4096 -timeout=10 -rss_limit_mb=2048 \
  -artifact_prefix=Fuzz/.build/artifacts/ -print_final_stats=1
