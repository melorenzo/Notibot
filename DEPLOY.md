# Deploy en el server

El compose monta esta carpeta en `/app` (`./app2-notibot-V2:/app`), así que el
código entra por volumen. **Pero esta versión sí necesita rebuild de la imagen**:
pasó a Python 3.12 y agregó `scrapling`. Con la imagen vieja (3.9) los scrapers
no arrancan.

## Requisitos

- Docker + Docker Compose en el server.
- El `variables.env` y el estado (`data/`, `token_jwt/`, `keys/`,
  `config/secrets.env`) **no están en el repo**: se copian del directorio actual.

## 1. Clonar al lado (sin pisar lo que anda)

```bash
cd /home/mlorenzo/docker
git clone <URL_DEL_REPO> app2-notibot-nuevo
```

## 2. Traer el estado y los secretos del directorio viejo

```bash
cd /home/mlorenzo/docker
OLD=app2-notibot-V2
NEW=app2-notibot-nuevo

cp "$OLD/variables.env"        "$NEW/variables.env"
cp "$OLD/.jwt_cache.json"      "$NEW/.jwt_cache.json"   2>/dev/null || true

# historial antiduplicados, logs, tokens y cookies de sesión
cp -a "$OLD/data"      "$NEW/data"      2>/dev/null || true
cp -a "$OLD/logs"      "$NEW/logs"      2>/dev/null || true
cp -a "$OLD/token_jwt" "$NEW/token_jwt" 2>/dev/null || true
cp -a "$OLD/keys"      "$NEW/keys"      2>/dev/null || true
cp -a "$OLD/config/."  "$NEW/config/"   2>/dev/null || true

chmod 600 "$NEW/variables.env"
chmod 600 "$NEW/config/secrets.env" 2>/dev/null || true
```

> `data/used_items.json` es el historial de deduplicación. Si se pierde, el bot
> vuelve a publicar notas que ya salieron.

## 3. Rebuild de la imagen y recrear contenedores

```bash
cd /home/mlorenzo/docker
docker compose build --no-cache
docker compose up -d --force-recreate
```

`restart` no alcanza: no recarga el `env_file` ni la imagen nueva.

## 4. Verificar

```bash
cd /home/mlorenzo/docker
docker compose ps

# que el contenedor tenga Python 3.12 y scrapling
docker exec notibot-cron-a python -c "import sys, scrapling; print(sys.version.split()[0], scrapling.__version__)"

# smoke test (no publica)
docker exec notibot-cron-a sh -c 'cd /app && bash deploy/smoke_test.sh'

# una corrida real de una fuente migrada, mirando el log
docker exec notibot-cron-a sh -c 'cd /app && SCRAPER_DEBUG=1 python scraping/scraper_ba_static.py'
docker exec notibot-cron-a sh -c 'tail -40 /app/logs/cron_a.log'

# el panel
docker exec app2-notibot sh -c 'curl -sf -o /dev/null -w "%{http_code}\n" http://127.0.0.1:8000/login'
```

## 5. Pasar el directorio nuevo a su lugar

```bash
cd /home/mlorenzo/docker
mv app2-notibot-V2 app2-notibot-V2.bak-$(date +%Y%m%d)
mv app2-notibot-nuevo app2-notibot-V2
docker compose up -d --force-recreate
```

## Rollback

```bash
cd /home/mlorenzo/docker
mv app2-notibot-V2 app2-notibot-fallado-$(date +%Y%m%d)
mv app2-notibot-V2.bak-<fecha> app2-notibot-V2
docker compose up -d --force-recreate
```

Si hace falta volver a los scrapers con BeautifulSoup, las versiones previas
están en `old_version/bs4_pre_scrapling/scraping/`.

## Scrapling

- Las huellas adaptativas viven en `data/scrapling/<scraper>.db`. Son
  descartables: si el scraper empieza a devolver cosas raras, borralas y vuelve
  a aprender.
- `SCRAPER_ADAPTATIVO=0` en `variables.env` apaga la reubicación adaptativa.

## Rotación de claves pendiente

Las contraseñas de WordPress de `latin_tinta`, `palabras_red`, `lavoz_portena`,
`informate_ciudad`, `pulsoporteno` y `horizonteba` estuvieron en texto plano en
scripts viejos (ya borrados). Conviene rotarlas: cambiar en cada wp-admin y
actualizar `LT_PASS`, `PR_PASS`, `LVP_PASS`, `IC_PASS`, `PP_PASS`, `HB_PASS` en
`variables.env`, después `docker compose up -d --force-recreate`.
