# MOF Configuration Profiles

Set the active environment with:

```powershell
$env:APP_PROFILE="local.windows"
```

or on Ubuntu:

```bash
export APP_PROFILE=server.ubuntu
```

Load order:

1. `config/default.yaml`
2. legacy `config.yaml`
3. `config/<APP_PROFILE>.yaml`
4. `CONFIG_FILE`, if set
5. environment variables and ignored secrets env files

Secrets are not committed. Copy `config/secrets.example.env` to:

```text
config/secrets.local.windows.env
config/secrets.server.ubuntu.env
```

The Google OAuth web client id in the frontend must match the backend secret.
Use either `GOOGLE_OAUTH_CLIENT_SECRET` or `GOOGLE_CLIENT_SECRET_FILE`.
