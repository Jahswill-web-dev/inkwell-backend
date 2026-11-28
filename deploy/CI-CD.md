# GitHub Actions to Oracle

Repository: `Jahswill-web-dev/inkwell-backend`. Production VM: `ubuntu@144.24.170.81`.
The existing production environment remains at `/home/ubuntu/inkwell-backend/.env.production`.

## CI

`.github/workflows/ci.yml` runs on pull requests, pushes to `main`, and manual runs.
It installs the frozen uv lockfile, lints, runs unit and integration tests against
an isolated PostgreSQL 17 service, and builds the production Docker image.
Paid provider smoke tests are excluded; production API keys are not needed in CI.

## Deployment setup

Install `deploy/oracle-deploy.sh` on the VM at
`/home/ubuntu/inkwell-backend/deploy/oracle-deploy.sh` with mode 700.
Validate prerequisites without changing services:

```bash
bash /home/ubuntu/inkwell-backend/deploy/oracle-deploy.sh --check
```

Generate a separate Ed25519 key for GitHub Actions. Add its public key to the
VM user's `~/.ssh/authorized_keys`, prefixed with:

```text
restrict,command="/bin/bash /home/ubuntu/inkwell-backend/deploy/oracle-deploy.sh"
```

The prefix and public key must be on the same line, separated by a space. This
key can only invoke the receiver with `deploy FULL_COMMIT_SHA`; interactive
shells and forwarding are disabled. Deployment code can build/run containers,
so repository main-branch access controls production deployment access.

In repository Settings → Secrets and variables → Actions:

1. Add repository **secret** `ORACLE_SSH_KEY`: the dedicated private key, including
   its BEGIN/END lines. Never commit it, put it in a variable, or paste it into chat.
2. After CI passes and the server receiver/key are ready, add repository
   **variable** `ORACLE_DEPLOY_ENABLED` with value `true`.

The SSH host key is pinned in `deploy/oracle_known_hosts`. If Oracle is rebuilt
or its host key changes, verify the new fingerprint through a trusted channel
before updating this file; do not disable host checking.

## What a deployment does

Only successful `main` CI runs can deploy. Pull requests never deploy. With the
enable variable unset or false, the deployment job is skipped.

The runner streams `git archive` for the exact tested commit to the forced SSH
receiver. Oracle needs no GitHub credentials or git checkout. The receiver:

1. Takes a server lock and extracts a fresh release into `releases/`.
2. Links the existing server-only `.env.production` and validates Compose config.
3. Builds on Oracle's native architecture while the existing application runs.
4. Dumps PostgreSQL into `backups/` and verifies the dump can be read.
5. Stops the API and worker, then runs migrations using the new image.
6. Starts API, worker and Caddy with both Compose files and waits for readiness.
7. Records `.deployed-commit` and `.deployed-release` only after local readiness.

GitHub also checks public HTTPS `/ready`. The database and Caddy certificate
volumes keep the existing `inkwell-production` project name. No deployment
deletes volumes. There is a brief interruption during migration/restart.

Concurrent deployments are serialized. Builds and backups failing leave the
existing API/worker running. Migration failures leave them stopped and fail the
workflow; they do not automatically roll back the database or hide the error.

## Verify or recover

Use the Actions tab to inspect the run and failed step. On Oracle:

```bash
cat /home/ubuntu/inkwell-backend/.deployed-commit
cat /home/ubuntu/inkwell-backend/.deployed-release
sudo docker ps
curl --fail http://127.0.0.1:8000/ready
```

For a failed deployment, the log identifies its release and backup. Inspect
`migrate`, `api`, and `worker` from that release with the same Compose options
as `ORACLE.md`. Decide whether to fix forward or restore after assessing schema
compatibility; starting an old image alone does not undo database migrations.
Set `ORACLE_DEPLOY_ENABLED=false` to stop subsequent automatic deployments while
investigating. Do not cancel an in-progress migration.

Release directories and backups are retained for recovery. Monitor disk space,
copy database backups off the VM, and establish retention after deciding how
many recovery points to keep. Never use `docker compose down -v` for updates.
