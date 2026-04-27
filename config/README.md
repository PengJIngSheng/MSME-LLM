# MOF Runtime Config

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

## File order

The loader merges settings in this order:

1. Built-in defaults
2. `config/default.yaml`
3. legacy `config.yaml`
4. `config/<profile>.yaml`
5. `CONFIG_FILE`, if set
6. environment variables and `config/secrets*.env`

Secrets should live in `config/secrets.local.windows.env` or `config/secrets.server.ubuntu.env`.

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
