#!/usr/bin/env bash
set -euo pipefail

repo=/python/pragrams/data_analysis_agent
commit=ee077c8
archive=/tmp/runtime-v5-${commit}.tar.gz
files=/tmp/runtime-v5-files-${commit}.txt
existing=/tmp/runtime-v5-existing-${commit}.txt
backup=/tmp/runtime-v5-pre-sync-${commit}-$(date +%Y%m%d%H%M%S).tar.gz

cd "$repo"
test -s "$archive"
test -s "$files"
tr -d '\r' < "$files" > "$existing.clean"
: > "$existing"
while IFS= read -r file; do
  if [ -f "$file" ]; then
    printf '%s\n' "$file" >> "$existing"
  fi
done < "$existing.clean"

count=$(wc -l < "$existing" | tr -d ' ')
echo "existing files to backup: $count"
tar -czf "$backup" --files-from "$existing" --ignore-failed-read
echo "archive: $archive"
echo "backup: $backup"
sha256sum "$archive" "$backup"
tar -xzf "$archive" -C "$repo"
echo '--- extraction complete ---'
ls -lh "$archive" "$backup"
