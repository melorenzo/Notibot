# Deploy en el server

## Orden de ejecución (checklist)

Cómo está el server hoy: la carpeta que corre (`app2-notibot-V2`) quedó intacta
y el repo se clonó en otra carpeta. No hay que renombrar nada: se valida en el
clon y el `--apply` hace el swap, que deja la original completa en
`app2-notibot-V2.bak-<fecha>` y pone el clon en su lugar. El cronjob del host no
se rompe: los contenedores conservan nombre y la ruta que monta el compose sigue
siendo `./app2-notibot-V2`.

El orden importa: las credenciales y el servicio del compose tienen que existir
**antes** del `--check`, y el panel no se levanta a mano nunca (con la imagen
vieja de Python 3.9 no importa scrapling).

```
NUEVO=/home/mlorenzo/docker/<carpeta-del-clon>

1. $NUEVO/variables.env = copia del original + PANEL_* (600)
2. servicio notibot-panel en docker-compose.yml        → docker compose config -q
3. WORK_DIR=$NUEVO bash $NUEVO/deploy/deploy_github.sh            (--check)
   └─ si una fuente da 0 items: rm -rf $NUEVO/data/scrapling y repetir
4. WORK_DIR=$NUEVO bash $NUEVO/deploy/deploy_github.sh --apply    → swap + panel 200 + smoke
5. registro DNS A de panelnoti.fullware.com.ar         → dig +short
6. nginx-panel.conf + nginx -t + reload                → curl http://panelnoti...
7. certbot --nginx -d panelnoti.fullware.com.ar        → curl -sI https://... 200
8. login en el navegador + una corrida de prueba desde el panel
9. retirar del crontab del host los docker exec notibot-cron-*
10. (postergado) rotar LT_PASS, PR_PASS, LVP_PASS, IC_PASS, PP_PASS, HB_PASS
```

Detalle de cada paso en las secciones de abajo.

### Gotchas que cortan el deploy

- `WORK_DIR=$NUEVO` es lo único distinto del camino normal: manda al clon en
  lugar de a `app2-notibot-nuevo`. `PROD_DIR` sigue siendo `app2-notibot-V2`, y
  de ahí copia `variables.env`, `data/`, `logs/`, `token_jwt/`, `keys/` y
  `config/`.
- El paso 1 va antes del 3: tanto el `--check` como el `--apply` leen
  `variables.env` del directorio de trabajo y fallan si no está
  `PANEL_PASSWORD`. Si el clon ya tiene `variables.env`, el script lo deja (avisa
  "ya estaba, lo dejo"): cargale ahí las `PANEL_*`.
- El paso 1 no afecta lo que publica: los contenedores no releen `variables.env`
  hasta que se recrean, y eso pasa recién en el `--apply`.
- `data/used_items.json` viaja con el `data/`: si el clon arranca sin él, el bot
  vuelve a publicar notas que ya salieron.
- El swap mueve la original a `app2-notibot-V2.bak-<fecha>` y el clon a
  `app2-notibot-V2`; no toca nombres de contenedores ni la ruta del compose, así
  que el cronjob sigue apuntando a lo mismo.
- Rollback: `bash /home/mlorenzo/docker/app2-notibot-V2/deploy/deploy_github.sh --rollback`.
- Una vez hecho el swap, el clon vive en `app2-notibot-V2` y las próximas
  corridas no necesitan `WORK_DIR`: el script ve el `.git` y actualiza en sitio.
- Si preferís clonar directamente sobre `app2-notibot-V2` (moviendo la original
  antes), el script también detecta ese caso y no hace swap.

## Camino rápido (GitHub, sin scp)

Todo está en `deploy/deploy_github.sh`. Hay tres modos según cómo esté el
directorio de producción:

- **clon al lado** (el caso actual): el repo está en otra carpeta y
  `app2-notibot-V2` sigue intacta. Se corre con `WORK_DIR=<clon>`: valida en el
  clon y el `--apply` hace el swap con backup de la original.
- **en sitio**: `app2-notibot-V2` mismo es el clon → actualiza con git y recrea,
  sin swap; el rollback es `git reset` al commit anterior.
- **clon automático**: si no hay clon ni se pasa `WORK_DIR`, clona en
  `app2-notibot-nuevo` y hace el swap.

```bash
cd /home/mlorenzo/docker
NUEVO=/home/mlorenzo/docker/<carpeta-del-clon>

WORK_DIR=$NUEVO bash $NUEVO/deploy/deploy_github.sh             # valida, no recrea nada
WORK_DIR=$NUEVO bash $NUEVO/deploy/deploy_github.sh --apply     # swap + recrea
bash app2-notibot-V2/deploy/deploy_github.sh --rollback         # vuelve al backup/commit anterior
```

El modo `--check` (default) trae el código, copia `variables.env`, `data/`,
`logs/`, `token_jwt/`, `keys/` y `config/` de lo que falte, construye la imagen y
corre `ba_static`, `ba_noticias`, `lanacion` e `infobae` contando items en
contenedores descartables. No recrea los servicios que están publicando.

`equipoba` no se prueba ahí porque necesita el contenedor de Selenium; se valida
con `deploy/smoke_test.sh` después del swap.

## Procedimiento manual

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

# el panel (servicio notibot-panel, ver sección "Entrar al panel")
docker exec notibot-panel sh -c 'curl -sf -o /dev/null -w "%{http_code}\n" http://127.0.0.1:8030/login'
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

## Entrar al panel (`panelnoti.fullware.com.ar`)

El panel es un servicio más del compose (no lo arranca `bot_telegram.py`).
Escucha en **8030** y se publica **solo en el loopback del server**; el que sale
a internet es nginx con el dominio y TLS.

### 1. Servicio en el compose

Agregalo a `/home/mlorenzo/docker/docker-compose.yml` copiando `image:`,
`env_file:`, `volumes:` y `networks:` de un `notibot-cron-*`, y cambiando el
comando:

```yaml
  notibot-panel:
    image: app2-notibot:latest
    container_name: notibot-panel
    command: uvicorn panel.app:app --host 0.0.0.0 --port 8030
    working_dir: /app
    env_file: ./app2-notibot-V2/variables.env
    volumes:
      - ./app2-notibot-V2:/app
    ports:
      - "127.0.0.1:8030:8030"
    restart: unless-stopped
```

- `127.0.0.1:8030:8030` publica el puerto **solo en loopback**, así nginx (que
  corre en el host) llega por `http://127.0.0.1:8030` y nadie entra directo por
  IP. Si tu nginx está en otro contenedor, sacá el prefijo `127.0.0.1:` y usá la
  red de docker en el `proxy_pass`.
- Se llama `notibot-panel` a propósito: `deploy_github.sh` recrea todos los
  servicios cuyo nombre contenga "notibot", así que entra solo en el swap.
- Tiene que usar la misma imagen que los cron: la vieja (Python 3.9) no importa
  scrapling. No lo levantes a mano antes del `--apply`.

### 2. Variables en `variables.env`

```
PANEL_PASSWORD=<una contraseña larga>
PANEL_SECRET=<otra cadena larga y distinta>
PANEL_TRUST_PROXY=1
PANEL_COOKIE_SECURE=1
```

Sin `PANEL_PASSWORD` el panel abre pero rechaza todo login. El usuario es
implícito: la pantalla de login pide **solo la contraseña**. La sesión dura 12 h.

`PANEL_TRUST_PROXY=1` es obligatorio detrás de nginx: si no, todas las visitas
llegan con la IP del proxy y los 6 intentos fallidos de cualquiera bloquean a
todos. `PANEL_COOKIE_SECURE=1` fuerza la cookie `Secure` detrás de HTTPS.

### 3. DNS y nginx

1. Apuntá un `A` de `panelnoti.fullware.com.ar` a la IP del server (y `AAAA`
   solo si el server tiene IPv6 pública; si no, no lo crees).
2. Copiá la config del repo y activala:

```bash
sudo cp /home/mlorenzo/docker/app2-notibot-V2/deploy/nginx-panel.conf \
        /etc/nginx/sites-available/notibot-panel
sudo ln -sf /etc/nginx/sites-available/notibot-panel /etc/nginx/sites-enabled/notibot-panel
sudo nginx -t && sudo systemctl reload nginx
```

3. Emití el certificado (certbot agrega el bloque 443 y el redirect solo):

```bash
sudo certbot --nginx -d panelnoti.fullware.com.ar
```

### 4. Verificar

```bash
# desde tu máquina, con el panel ya recreado
curl -sI https://panelnoti.fullware.com.ar/login | head -1     # 200
```

Después entrá, hacé login y confirmá en el navegador que la cookie
`notibot_panel` figura como `Secure` y `HttpOnly`.

El scheduler interno corre dentro de este servicio: cuando lo tengas andando,
retirá las líneas del cron del host que hacen `docker exec notibot-cron-*`, si no
las corridas se duplican.

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
