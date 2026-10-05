# Shared HTTPS entry point

Run this Compose project independently of both the app services and the public
website. It is the only project that publishes ports 80/443 on the Hetzner VM.
Caddy obtains and renews certificates, then routes by hostname to services on the
external `papyrus-edge` Docker network:

| Hostname | Internal service |
| --- | --- |
| `papyrus-reader.com` | `papyrus-website:8080` |
| `www.papyrus-reader.com` | Redirect to the main website |
| `app.papyrus-reader.com` | `papyrus-app:8080` |
| `api.papyrus-reader.com` | `papyrus-api:8080` |
| `sync.papyrus-reader.com` | `papyrus-sync:8080` |

Install these files at `/srv/apps/papyrus-edge` on the VM. Copy
`edge.env.example` to `edge.env`, set the ACME contact email, and run
`sh deploy.sh`. Keep `edge.env` private with `chmod 600 edge.env`. Configure the
app and website Compose projects separately; their runbooks describe their
releases. Starting or stopping either project does not restart the shared proxy.
The proxy mounts no app or website content and does not need database credentials.

For a Caddyfile-only change, validate and reload without replacing the container:

```sh
docker compose --env-file edge.env -f compose.yml exec -T proxy caddy validate --config /etc/caddy/Caddyfile --adapter caddyfile
docker compose --env-file edge.env -f compose.yml exec -T proxy caddy reload --config /etc/caddy/Caddyfile --adapter caddyfile
```

The persistent `papyrus-edge-caddy-data` and `papyrus-edge-caddy-config` volumes
belong to this project. Do not remove them during an app or website release.
When migrating from the earlier app-owned proxy, stop that proxy, copy its
certificate data into the new data volume, then start this proxy to take over
80/443. Preserve the app database/media volumes and keep the previous proxy
configuration and certificate volumes for rollback.
