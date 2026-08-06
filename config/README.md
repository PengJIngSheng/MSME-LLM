# bisnes.ai Runtime Config

Profiles keep the Windows laptop and Ubuntu server settings separate.

## Select a profile

Windows PowerShell:

```powershell
$env:APP_PROFILE = "local.windows"
python server.py
```

Ubuntu:

```bash
export APP_PROFILE=server.ubuntu
python server.py
```

`MOF_PROFILE` is also accepted. If neither variable is set, the app uses `local.windows`.

## Remote GPU hosting (Plesk + IPServerOne)

If the web app is hosted on Plesk but the LLM/GPU runtime lives on IPServerOne, point the app at the GPU host instead of `localhost`.

```bash
export OLLAMA_HOST=http://<ipserverone-host-or-ip>:11434
export PUBLIC_SITE_URL=https://your-plesk-domain.com
export APP_PROFILE=server.ubuntu
python server.py
```

The app already reads `OLLAMA_HOST` through the config loader, so this is the simplest way to switch from local inference to a remote Ollama endpoint.

## File order

The loader merges settings in this order:

1. Built-in defaults
2. `config/default.yaml`
3. `config/<profile>.yaml`
4. `CONFIG_FILE`, if set
5. environment variables and `config/secrets*.env`

Secrets should live in `config/secrets.local.windows.env` or `config/secrets.server.ubuntu.env`.
Copy `config/secrets.example.env` as a starting point. `JWT_SECRET` is mandatory and
must never be committed. Google Workspace refresh tokens are encrypted with
`GOOGLE_TOKEN_ENCRYPTION_KEY`; changing that key requires users to reconnect their
Google accounts.

After rotating a previously exposed Google OAuth client secret, inspect and then clear
legacy plaintext user tokens with `python scripts/purge_legacy_google_oauth.py` and
`python scripts/purge_legacy_google_oauth.py --apply`. The cleanup disconnects affected
Google Workspace users so they can authorize again with encrypted credentials.

## Docker

Local:

```bash
docker compose -f docker-compose.yml -f docker-compose.local.yml up -d
```

Server:

```bash
export APP_PROFILE=server.ubuntu
docker compose -f docker-compose.yml -f docker-compose.server.yml up -d
```

`docker-compose.server.yml` binds MongoDB, PGVector, and Ollama to `127.0.0.1` by default. Set `SERVER_BIND_IP=0.0.0.0` only when your cloud firewall or private network already blocks public access.
