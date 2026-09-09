# FactoryBridge HTTPS Gateway

## Objective

Make the Windows notebook the persistent execution engine while ChatGPT remains an intermittent decision/strategy layer.

The gateway is intentionally split into two trust surfaces:

- **public read-only** endpoints with sanitized state;
- **private write** endpoints protected by a bearer token stored only under `%LOCALAPPDATA%\FactoryBridge\gateway\gateway.token`.

The runtime itself listens only on:

`http://127.0.0.1:8787`

No router port-forwarding is required.

## Public read-only endpoints

- `/public/health`
- `/public/attention`
- `/public/checkpoint`
- `/public/showcase`
- `/` — human dashboard

Public responses do not expose local filesystem paths or arbitrary logs.

## Private endpoint

- `POST /api/missions`
- `GET /api/runtime/status`

Private requests require:

`Authorization: Bearer <local token>`

Mission kinds remain allowlisted:

- `projecthub.verify`
- `projecthub.showcase`
- `projecthub.full_cycle`

There is no shell endpoint and no arbitrary path argument.

## Internet exposure

For the first proof, `SETUP_FACTORY_GATEWAY.cmd` installs/uses `cloudflared` and starts a Cloudflare Quick Tunnel from the public HTTPS URL to `127.0.0.1:8787`.

The quick-tunnel URL is written to:

- `%LOCALAPPDATA%\FactoryBridge\gateway\public-url.txt`
- `<bridgeRoot>\00_BRAIN\GATEWAY_URL.txt`

This keeps Google Drive only as a compact discovery/fallback channel. Runtime heartbeats, mission evidence, test logs and build outputs remain local.

Quick Tunnel URLs can change after a restart. The next hardening step is a named Cloudflare Tunnel with a stable hostname when a domain/account decision is made.

## Security boundaries

- Gateway binds only to loopback.
- Cloudflare Tunnel is outbound from the notebook.
- Public endpoints are read-only and sanitized.
- Write endpoints require the local bearer token.
- The token is never written to Drive or Git.
- No arbitrary shell, URL fetch, disk path, or command parameter is accepted.
- Showcase details expose only build basename/commit metadata, never a local absolute path.

## Runtime role split

### Local engine

`%LOCALAPPDATA%\FactoryBridge`

Stores supervision state, mission evidence, logs, tunnel state and local runtime artifacts.

`%USERPROFILE%\ProjectHub-Lab`

Stores immutable published ProjectHub builds for human inspection/testing.

### Brain/mailbox

Google Drive keeps only compact canonical/discovery state and remains a fallback for inbound mission delivery until the private HTTPS write surface is connected to a supported ChatGPT integration.
