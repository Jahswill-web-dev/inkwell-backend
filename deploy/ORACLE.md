# Inkwell on Oracle Ubuntu ARM

This configuration runs the API, consultant worker, and PostgreSQL 17 on one VM.
The API initially listens only on the VM's loopback address; PostgreSQL has no
published port. Public HTTPS will be configured separately when an API hostname
is available. CORS alone does not make an API accessible to Vercel.

## Transfer the source

The production files must reach the server along with the current backend source.
They are local workspace changes until committed/pushed or explicitly copied.
For a public repository, clone its HTTPS URL on the VM after publishing the files.
For a private repository, use a read-only deploy key or copy a source archive via
SCP. Do not copy your local virtual environment, `.env`, or private SSH key.

## First startup

In the backend repository directory on the VM:

```bash
cp deploy/oracle.env.example .env.production
chmod 600 .env.production
openssl rand -hex 32
openssl rand -hex 32
nano .env.production
```

Use the two generated values for POSTGRES_PASSWORD and JWT_SECRET_KEY. Keep the
database password hexadecimal so it is safe to insert in the connection URL.
Fill OPENAI_API_KEY (required for the configured voice provider in production)
and OPENROUTER_API_KEY (for marketing generation). Check that configured model
IDs are available to your provider account; no model access is granted by hosting.
Never paste these secrets into chat. If nano is missing, install it with
`sudo apt install -y nano`.

```bash
sudo docker compose --env-file .env.production -f compose.production.yaml config --quiet
sudo docker compose --env-file .env.production -f compose.production.yaml up -d --build
sudo docker compose --env-file .env.production -f compose.production.yaml ps -a
curl --fail http://127.0.0.1:8000/ready
```

Startup waits for PostgreSQL, applies migrations in a one-shot container, then
starts the API and worker. `migrate` exiting with code 0 is expected. If startup
fails, inspect the relevant service locally:

```bash
sudo docker compose --env-file .env.production -f compose.production.yaml logs --tail=80 migrate api worker
```

Redact credentials and user content before sharing logs. Do not run `config`
without `--quiet` when sharing output: resolved configuration includes secrets.

## Test from Windows

In a second local PowerShell window, establish a tunnel (substitute your key path
and public IP):

```powershell
ssh -i "C:\path\to\private.key" -N -L 8000:127.0.0.1:8000 ubuntu@YOUR_PUBLIC_IP
```

Keep it open and visit http://localhost:8000/docs. This validates the API privately;
the deployed Vercel frontend still needs a publicly reachable HTTPS backend.

## Backups and updates

### Enable public HTTPS

After private readiness succeeds, copy `Caddyfile` and `compose.https.yaml` into
the backend directory on the VM. The default hostname is
`144-24-170-81.sslip.io`, which embeds the currently assigned public IP. If that
IP changes, update the hostname. To use another hostname, add API_DOMAIN to
`.env.production`. Public DNS must resolve it to the VM. Allow inbound TCP 80
and 443 in the public subnet's security list and any active host firewall.
Keep TCP 8000 bound to loopback and PostgreSQL unpublished.

```bash
sudo docker compose --env-file .env.production -f compose.production.yaml -f compose.https.yaml config --quiet
sudo docker compose --env-file .env.production -f compose.production.yaml -f compose.https.yaml up -d --no-deps api
sudo docker compose --env-file .env.production -f compose.production.yaml -f compose.https.yaml up -d --no-deps caddy
curl --fail https://144-24-170-81.sslip.io/ready
```

The API container restarts briefly to enable trusted proxy headers. Caddy obtains
and renews its TLS certificate; allow time for initial issuance. Persist its data
volume to preserve certificates and avoid unnecessary reissuance. If issuance
fails, inspect Caddy logs and DNS/firewall settings; don't bypass TLS verification.
Test the HTTPS readiness URL from a computer outside the VM as well.

Once HTTPS is enabled, include both `-f compose.production.yaml -f compose.https.yaml`
in all Compose commands below, including updates, so proxy settings are retained.
The HTTPS overlay does not change worker settings or database storage.

### Database backups

Create a database backup before applying updates:

```bash
mkdir -p backups
chmod 700 backups
sudo docker compose --env-file .env.production -f compose.production.yaml exec -T postgres pg_dump -U inkwell -d inkwell -Fc > "backups/inkwell-$(date -u +%Y%m%dT%H%M%SZ).dump"
```

Check that pg_dump succeeded and copy the backup to a separate machine. Schedule
regular backups and test restoration into a separate database before relying on
them. A Docker volume is persistence, not a backup.

For a coordinated update after obtaining the new source:

```bash
sudo docker compose --env-file .env.production -f compose.production.yaml build
sudo docker compose --env-file .env.production -f compose.production.yaml stop api worker
sudo docker compose --env-file .env.production -f compose.production.yaml run --rm migrate
# Continue only if migrations succeeded.
sudo docker compose --env-file .env.production -f compose.production.yaml up -d api worker
curl --fail http://127.0.0.1:8000/ready
```

The volume survives normal container recreation and VM reboot. Never use
`docker compose down -v` on this deployment unless you intend to delete its data.
On first initialization PostgreSQL sets the password from the environment; changing
POSTGRES_PASSWORD later does not change an existing database's password.
