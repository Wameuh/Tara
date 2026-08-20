# Lancer le socle web

## Developpement natif

Copier `config/webinterface.example.yaml` dans un emplacement operateur puis:

```powershell
$env:UV_CACHE_DIR = '.uv-cache'
uv sync --extra dev
uv run python scripts/build_web_brand_assets.py
Set-Location webinterface/frontend
npm.cmd ci
npm.cmd run build
Set-Location ../..
uv run tara-web --config config/webinterface.example.yaml
```

Le serveur natif ecoute seulement `127.0.0.1:8000` par defaut. Les routes sont
`/api/v1/live`, `/api/v1/ready` et `/api/v1/config/public`. Les variables
admises sont `TARA_WEB_STORAGE_ROOT`, `TARA_WEB_BACKUPS_ROOT`,
`TARA_WEB_SQLITE_PATH`, `TARA_WEB_PUBLIC_URL`, `TARA_WEB_ALLOWED_HOSTS`,
`TARA_WEB_ALLOWED_ORIGINS` et le secret configure par `security.link_secret_env`.

## Compose

Creer `config/webinterface.yaml` a partir de l'exemple puis lancer:

```powershell
docker compose up --build
docker compose down
```

Le profil de reference ne publie aucun port FastAPI; un reverse proxy doit joindre
`tara-web` sur le reseau interne. Le volume `tara_web_data` contient SQLite et
les futures donnees; la configuration est montee en lecture seule. Pour une
installation publique, activer `require_link_secret` et injecter sa variable hors
du fichier YAML versionne.
