#!/usr/bin/env bash
set -euo pipefail
# The image supplies clients matching the PostgreSQL service's major version.
tools_dir="${RUNNER_TEMP:?}/admincore-postgres-tools"
mkdir -p "$tools_dir"
for tool in psql pg_dump createdb pg_restore dropdb; do
  cat > "$tools_dir/$tool" <<'WRAPPER'
#!/usr/bin/env bash
set -euo pipefail
tool="$(basename "$0")"
exec docker run --rm --network host --env PGPASSWORD \
  --volume "${GITHUB_WORKSPACE:?}:${GITHUB_WORKSPACE}" \
  postgres:18 "$tool" "$@"
WRAPPER
  chmod +x "$tools_dir/$tool"
done
echo "$tools_dir" >> "${GITHUB_PATH:?}"
