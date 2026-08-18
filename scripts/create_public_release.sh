#!/bin/sh
set -eu

# Export the reviewed working tree without the repository's private/deleted
# history. This is intentionally non-destructive: it never rewrites the source
# repository and produces a clean snapshot suitable for a new public repository.
repository_root=$(git rev-parse --show-toplevel)
cd "$repository_root"

manifest=$(mktemp "${TMPDIR:-/tmp}/openrole-release.XXXXXX")
trap 'rm -f "$manifest"' EXIT HUP INT TERM

git ls-files --cached --others --exclude-standard | while IFS= read -r path; do
    if [ -f "$path" ]; then
        printf '%s\n' "$path"
    fi
done > "$manifest"

if grep -E '(^|/)(\.env($|\.)|credentials\.json$|.*\.db($|-wal$|-shm$)|companies_h1b_250\.csv$|companies_verified.*\.csv$|fortune_100_companies\.csv$)' "$manifest"; then
    echo "Refusing release: sensitive or restricted file matched the manifest" >&2
    exit 1
fi

revision=$(git rev-parse --short=12 HEAD)
timestamp=$(date -u +%Y%m%dT%H%M%SZ)
output_directory=${1:-dist}
mkdir -p "$output_directory"
archive="$output_directory/openrole-intelligence-${revision}-${timestamp}.tar.gz"

tar -czf "$archive" -T "$manifest"
shasum -a 256 "$archive" > "$archive.sha256"
printf 'Created %s\n' "$archive"
printf 'Create a new empty public repository and import this snapshot; do not push the old Git history.\n'
