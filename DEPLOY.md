# Deploy en el server

## Orden de ejecución (checklist)

`app2-notibot-V2` **es** el clon del repo y es la carpeta que corre. El script
actualiza con git **en sitio**: no clona aparte, no hace swap y no toca el
cronjob (los contenedores mantienen nombre y la ruta del compose). Además
completa las `PANEL_*` que falten y levanta el panel en `127.0.0.1:8030`.

```
cd /home/mlorenzo/docker/app2-notibot-V2

1. bash deploy/deploy_github.sh --diff     (opcional) qué pisa el reset
2. bash deploy/deploy_github.sh            (--check) valida, no recrea nada
   └─ si una fuente da 0 items: rm -rf data/scrapling y repetir
3. bash deploy/deploy_github.sh --apply    recrea + panel 200 + smoke test
4. dig +short panelnoti.fullware.com.ar    registro DNS A a la IP del server
5. NPM: Proxy Host panelnoti.fullware.com.ar -> notibot-panel:8030
6. NPM: pestaña SSL -> Let's Encrypt + Force SSL   curl -sI https://... 200
7. login en el navegador + una corrida de prueba desde el panel
8. retirar del crontab del host los docker exec notibot-cron-*
9. (postergado) rotar LT_PASS, PR_PASS, LVP_PASS, IC_PASS, PP_PASS, HB_PASS
```

Detalle de cada paso en las secciones de abajo.

### Gotchas que cortan el deploy

- `--check` y `--apply` hacen `git reset --hard origin/main` sobre la carpeta
  viva: los archivos versionados con cambios locales se descartan (antes el
  script guarda `../notibot-antes-<fecha>.status` y `.patch`), y los que no están
  en el repo (`variables.env`, `data/`, `logs/`, `token_jwt/`, `keys/`,
  `config/`) quedan intactos.
- Si falta `variables.env` (un clone limpio no lo trae porque nunca estuvo en el
  repo), el script lo recupera solo: busca un `variables.env` en un directorio
  hermano del compose y, si no hay, lo reconstruye del entorno de un contenedor
  del notibot que ya existe (`docker inspect`). Solo corta si no encuentra
  ninguna de las dos cosas. Nunca imprime valores: dice de dónde salió y
  cuántas variables tiene.
- `data/used_items.json` no está en el repo; si falta, el bot vuelve a publicar
  notas que ya salieron.
- El script agrega `PANEL_SECRET`, `PANEL_TRUST_PROXY=1` y `PANEL_COOKIE_SECURE=1`
  si no están, y si falta `PANEL_PASSWORD` la genera y la imprime una vez (queda
  en `variables.env`).
- `PANEL_COOKIE_SECURE=1` implica login **solo por HTTPS**. Para probar antes del
  certificado, entrá por `http://127.0.0.1:8030/login` con un túnel SSH, o ponelo
  en 0 y recreá el panel.
- El panel no necesita declararse en el compose: el script lo levanta con
  `docker run --name notibot-panel ... -p 127.0.0.1:8030:8030`. Si agregás el
  servicio al compose, usa ese y no duplica el contenedor.
- Rollback: `bash deploy/deploy_github.sh --rollback` — git reset al commit
  anterior, rebuild y recreación.
- Si algún día el directorio de producción no es un clon, el script cae al modo
  clon aparte (`app2-notibot-nuevo`) con swap y backup.

## Camino rápido (GitHub, sin scp)

Todo está en `deploy/deploy_github.sh`. Hay tres modos según cómo esté el
directorio de producción:

- **clon al lado** (el caso actual): el repo está en otra carpeta y
  `app2-notibot-V2` sigue intacta. Se corre con `WORK_DIR=<clon>`: valida en el
  clon y el `--apply` hace el swap con backup de la original.
- **en sitio** (el caso actual): `app2-notibot-V2` es el clon → actualiza con
  git y recrea, sin swap; el rollback es `git reset` al commit anterior.
- **clon aparte**: si producción no es un clon, clona en `app2-notibot-nuevo`
  (o en `WORK_DIR`) y hace el swap con backup.

```bash
cd /home/mlorenzo/docker/app2-notibot-V2

bash deploy/deploy_github.sh --diff       # qué descarta el reset (opcional)
bash deploy/deploy_github.sh              # valida, no recrea nada
bash deploy/deploy_github.sh --apply      # recrea + panel + smoke test
bash deploy/deploy_github.sh --rollback   # vuelve al commit anterior
```

El modo `--check` (default) trae el código, completa las `PANEL_*` que falten,
construye la imagen y corre `ba_static`, `ba_noticias`, `lanacion` e `infobae`
contando items en contenedores descartables. No recrea los servicios que están
publicando.

`equipoba` no se prueba ahí porque necesita el contenedor de Selenium; se valida
con `deploy/smoke_test.sh` en el `--apply`.

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

El panel lo levanta `deploy_github.sh` en el `--apply` con `docker run` (no lo
arranca `bot_telegram.py`). Escucha en **8030** y se publica **solo en el
loopback del server**; el que sale a internet es **Nginx Proxy Manager**, que
corre como contenedor. Por eso el script detecta el contenedor de NPM y suma el
panel a **su red de docker**: así el proxy le llega por `notibot-panel:8030` sin
que haya que publicar el 8030 en ninguna interfaz pública.

```bash
docker ps --filter name=notibot-panel
docker logs --tail 30 notibot-panel
curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8030/login
```

### 1. Servicio en el compose (opcional)

Si preferís manejarlo desde el compose en vez de `docker run`, agregalo copiando
`image:`, `env_file:`, `volumes:` y `networks:` de un `notibot-cron-*`, y
cambiando el comando:

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

- `127.0.0.1:8030:8030` publica el puerto **solo en loopback**: nadie entra
  directo por IP y el script puede chequear la salud con `curl`. El tráfico real
  entra por la red de NPM (`--network <red de NPM>`), no por ese puerto.
  Si lo armás a mano en el compose, agregá `networks: [<red de NPM>]` al
  servicio para que NPM lo resuelva por nombre.
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

El script completa estas líneas solo si faltan (a `PANEL_PASSWORD` la genera y
la imprime una vez). El usuario es implícito: la pantalla de login pide **solo
la contraseña**. La sesión dura 12 h.

`PANEL_TRUST_PROXY=1` es obligatorio detrás del proxy: si no, todas las visitas
llegan con la IP del proxy y los 6 intentos fallidos de cualquiera bloquean a
todos. `PANEL_COOKIE_SECURE=1` fuerza la cookie `Secure` detrás de HTTPS.

### 3. DNS y Nginx Proxy Manager

1. Apuntá un `A` de `panelnoti.fullware.com.ar` a la IP del server (y `AAAA`
   solo si el server tiene IPv6 pública; si no, no lo crees).
2. En NPM: **Hosts → Proxy Hosts → Add Proxy Host**.
   - Domain Names: `panelnoti.fullware.com.ar`
   - Scheme: `http`
   - Forward Hostname / IP: `notibot-panel`
   - Forward Port: `8030`
   - Block Common Exploits: sí (opcional)
   - Websockets Support: no hace falta
3. Pestaña **SSL**: `Request a new SSL Certificate` con Let's Encrypt,
   `Force SSL` y `HTTP/2 Support` activados. NPM emite y renueva el
   certificado solo; no hay certbot en el host ni bloque 443 que mantener.

Si el proxy no resuelve `notibot-panel`, mirá en qué red quedó cada uno:

```bash
docker inspect notibot-panel --format '{{range $k, $v := .NetworkSettings.Networks}}{{$k}}{{"\n"}}{{end}}'
docker inspect <contenedor-de-npm> --format '{{range $k, $v := .NetworkSettings.Networks}}{{$k}}{{"\n"}}{{end}}'
```

Tienen que compartir al menos una. El script la detecta solo en el `--apply`
(línea `proxy : <contenedor de NPM> -> el panel se suma a la red ...`). Si querés
forzarla: `RED_NPM=<red> bash deploy/deploy_github.sh --apply`.

Si NPM corre con `network_mode: host` o en la red `bridge` por defecto, no hay DNS
por nombre: en ese caso el panel queda solo en loopback y el `Forward Hostname`
tiene que ser la IP del server (y el panel tener el 8030 publicado), o pasás el
panel al compose con la red de NPM.

### 4. Verificar

```bash
# desde el server, el panel responde en loopback
curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8030/login   # 200

# desde tu máquina, ya con el Proxy Host y el SSL de NPM
curl -sI https://panelnoti.fullware.com.ar/login | head -1            # 200
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
