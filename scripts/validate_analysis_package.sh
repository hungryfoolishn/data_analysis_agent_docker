#!/usr/bin/env bash
set -euo pipefail

base_url="${1:?usage: validate_analysis_package.sh BASE_URL RUN_ID}"
run_id="${2:?usage: validate_analysis_package.sh BASE_URL RUN_ID}"
if [[ ! "$run_id" =~ ^run_[0-9a-f]{32}$ ]]; then
  echo "invalid run id" >&2
  exit 2
fi

task_tmp="$(mktemp -d)"
trap 'rm -rf -- "$task_tmp"' EXIT
zip_path="$task_tmp/package.zip"
headers_path="$task_tmp/headers.txt"
manifest_path="$task_tmp/manifest.json"

http_status="$(curl -fsS -w '%{http_code}' -D "$headers_path" -o "$zip_path" \
  "${base_url%/}/analysis/runs/$run_id/package")"
unzip -t "$zip_path" >/dev/null
unzip -p "$zip_path" manifest.json > "$manifest_path"

verified=0
while IFS=$'\t' read -r archive_path expected_hash; do
  actual_hash="$(unzip -p "$zip_path" "$archive_path" | sha256sum | awk '{print $1}')"
  if [[ "$actual_hash" != "$expected_hash" ]]; then
    echo "hash mismatch: $archive_path" >&2
    exit 1
  fi
  verified=$((verified + 1))
done < <(jq -r '.files[] | [.archive_path, .sha256] | @tsv' "$manifest_path")

jq -n \
  --arg http_status "$http_status" \
  --arg package_version "$(awk 'BEGIN{IGNORECASE=1} /^x-analysis-package-version:/ {gsub("\\r", "", $2); print $2}' "$headers_path")" \
  --arg run_id "$(jq -r '.run_id' "$manifest_path")" \
  --arg run_status "$(jq -r '.run_status' "$manifest_path")" \
  --argjson entries "$(unzip -Z1 "$zip_path" | wc -l)" \
  --argjson verified_files "$verified" \
  --argjson missing_files "$(jq '.missing_files | length' "$manifest_path")" \
  --argjson zip_bytes "$(stat -c '%s' "$zip_path")" \
  '{http_status: $http_status, package_version: $package_version, run_id: $run_id, run_status: $run_status, entries: $entries, verified_files: $verified_files, missing_files: $missing_files, zip_bytes: $zip_bytes}'
