#!/usr/bin/env bash
# Installed on the VM at /home/ubuntu/inkwell-backend/deploy/oracle-deploy.sh.
# A dedicated authorized_keys entry forces this script; stdin is a git archive.
set -Eeuo pipefail
umask 077

root=/home/ubuntu/inkwell-backend
docker=(sudo -n docker)

preflight() {
  test -f "$root/.env.production"
  "${docker[@]}" compose version > /dev/null
  command -v flock > /dev/null
  command -v curl > /dev/null
  command -v tar > /dev/null
}

if [[ ${1:-} == --check && -z ${SSH_ORIGINAL_COMMAND:-} ]]; then
  preflight
  printf 'Deployment prerequisites passed. No services changed.\n'
  exit 0
fi

if [[ ! ${SSH_ORIGINAL_COMMAND:-} =~ ^deploy\ ([0-9a-f]{40})$ ]]; then
  printf 'Only deploy followed by a full commit SHA is allowed.\n' >&2
  exit 1
fi
sha=${BASH_REMATCH[1]}
preflight

# Also serialize against another SSH session or manual invocation on the VM.
exec 9> "$root/.deployment.lock"
flock -w 1200 9
mkdir -p "$root/releases" "$root/backups"
release=$(mktemp -d "$root/releases/$sha.XXXXXX")
tar --extract --file=- --directory="$release" --no-same-owner --no-same-permissions
for required in Dockerfile compose.production.yaml compose.https.yaml Caddyfile; do
  test -f "$release/$required"
done
ln -s "$root/.env.production" "$release/.env.production"

cd "$release"
compose=("${docker[@]}" compose -p inkwell-production --env-file .env.production
  -f compose.production.yaml -f compose.https.yaml)
"${compose[@]}" config --quiet
test -n "$("${compose[@]}" ps --status running -q postgres)"

printf 'Building tested commit %s; current services remain available during build.\n' "$sha"
"${compose[@]}" build

backup="$root/backups/inkwell-$(date -u +%Y%m%dT%H%M%SZ)-$sha.dump"
"${compose[@]}" exec -T postgres pg_dump -U inkwell -d inkwell -Fc > "$backup"
test -s "$backup"
"${compose[@]}" exec -T postgres pg_restore --list < "$backup" > /dev/null
printf 'Database backup verified: %s\n' "$backup"

"${compose[@]}" stop --timeout 60 api worker
if ! "${compose[@]}" run --rm --no-deps migrate; then
  printf 'Migration failed. API and worker remain stopped. Backup: %s\n' "$backup" >&2
  exit 1
fi
# Dependencies have been explicitly checked/migrated; do not rerun the old
# one-shot migrate container or recreate PostgreSQL during an application update.
"${compose[@]}" up -d --no-deps --wait --wait-timeout 180 api worker caddy
curl --fail --silent --show-error --max-time 10 http://127.0.0.1:8000/ready
printf '\n'
printf '%s\n' "$sha" > "$root/.deployed-commit"
printf '%s\n' "$release" > "$root/.deployed-release"
printf 'Deployment healthy: %s\n' "$sha"
