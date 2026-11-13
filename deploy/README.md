# Deploy Notibot — instrucciones

Paquete con los arreglos de fuentes + las 3 fuentes nuevas con feed RSS.

## Paquete `notibot-patch-20261103c` (LinkedIn sin sesión)

Es un **delta**: el patch `20261103` ya está aplicado en el server. Este trae:

```
notibot-patch-20261103c/
├── scraping/
│   ├── scraper_linkd.py   ← reescrito: vista pública de la empresa, sin sesión
│   └── enrich.py          ← fallback de imagen (imagen_post) + ignora src relativos
├── variables.env          ← LINKEDIN_COMPANY / LINKEDIN_MAX_DAYS / LINKEDIN_MAX_ITEMS
├── README.md
├── README_PATCH.md
├── APLICAR.sh             ← backup + copia + recreate + smoke test
└── deploy/                ← scripts de deploy (incluye el fix de --force-recreate)
```

**Incluye los scripts de deploy**, así que el paquete
`notibot-deploy-tools-20261103b.tar.gz` ya no hace falta.

```bash
scp deploy/notibot-patch-20261103c.tar.gz root@server:/home/mlorenzo/docker/
cd /home/mlorenzo/docker
tar -xzf notibot-patch-20261103c.tar.gz
bash notibot-patch-20261103c/APLICAR.sh
```

> Ojo: el `variables.env` del server puede tener valores que no querés pisar
> (tokens, claves de WP). `APLICAR.sh` lo sobreescribe. Para no pisarlo:
> `bash notibot-patch-20261103c/deploy/deploy.sh notibot-patch-20261103c --no-env`
> (en ese caso, agregá a mano `LINKEDIN_MAX_DAYS=10` y `LINKEDIN_MAX_ITEMS=20`).

Qué cambió en el scraper de LinkedIn:

- Antes: leía `/posts/?feedView=all` con cookies → las cookies están vencidas →
  LinkedIn respondía `/authwall` → 0 items, ~37 s por corrida.
- Ahora: lee la **página pública** de la empresa
  (`https://www.linkedin.com/company/gobierno-de-la-ciudad-de-buenos-aires/`),
  que no pide sesión. Saca cada post del
  `<article data-activity-urn="urn:li:activity:...">` con el texto en
  `<p data-test-id="main-feed-activity-card__commentary">`.
- Resuelve los links `lnkd.in` a la URL real (ej. `buenosaires.gob.ar`) para
  publicar el link original y poder bajar la imagen de portada.
- Descarta posts de más de `LINKEDIN_MAX_DAYS` días.
- Si la vista pública no trae nada, cae a la sesión (cookies o usuario/clave).

Probado en local: 8 posts en ~19 s, y el pipeline publica sin duplicados.

## Paquete `notibot-patch-20261103` (ya aplicado)

Paquete con los arreglos de fuentes + las 3 fuentes nuevas con feed RSS.

## Qué incluye

```
notibot-patch-20261103/
├── cron_base.py              ← pipeline arreglado (stale data, stdout, timeouts, fallback)
├── cron_worker_a|b|c|d.py    ← + CABA 360 / Noticias CABA / Infoporteño
├── bot_telegram.py           ← menú con las fuentes nuevas
├── variables.env             ← rutas absolutas + CRON_B_DIARIO corregido
├── README.md
├── core/
│   └── dedup.py              ← dedup por URL + título
└── scraping/
    ├── scraper_rss.py        ← scraper genérico RSS/Atom (nuevo)
    ├── rss_sources.py        ← registro de las 3 fuentes RSS (nuevo)
    ├── enrich.py             ← imagen + copete de la nota original (nuevo)
    ├── paths.py              ← rutas de salida centralizadas (nuevo)
    ├── scraper_ba_noticias.py
    ├── scraper_ba_static.py
    ├── scraper_infobae.py    ← ya no usa Selenium
    ├── scraper_lanacion.py
    ├── scraper_linkd.py
    └── scraper_x.py
```

**No** incluye `data/`, `logs/`, `token_jwt/`, `debug/`, `keys/`, `imagenes/` ni
`uploading/`: esos se quedan intactos en el server (importante: `data/used_items.json`
es el historial de deduplicación, si se pisa se republica todo).

## Pasos

**1. Copiar el paquete al server**

```bash
scp deploy/notibot-patch-20261103.tar.gz root@server:/home/mlorenzo/docker/
```

**2. Descomprimir (no pisa nada todavía)**

```bash
cd /home/mlorenzo/docker
tar -xzf notibot-patch-20261103.tar.gz
```

**3. Aplicar (backup + copia + restart + smoke test)**

```bash
bash /home/mlorenzo/docker/notibot-patch-20261103/deploy/deploy.sh \
     /home/mlorenzo/docker/notibot-patch-20261103
```

El script:
1. hace backup de los archivos actuales en `app2-notibot-V2/deploy/backup/<fecha>/`
2. muestra el diff de `variables.env`
3. copia el código
4. **recrea** los contenedores (`docker compose up -d --force-recreate`) — un
   `restart` NO recarga el `env_file`, así que las variables nuevas no
   entrarían al contenedor
5. corre el smoke test (no publica nada)

Como el compose monta `./app2-notibot-V2:/app`, **no hace falta `docker build`**:
el código nuevo entra directo al contenedor.

### Si el `variables.env` del server tiene valores que no querés pisar

```bash
bash .../deploy.sh /home/mlorenzo/docker/notibot-patch-20261103 --no-env
```

## 4. Probar

```bash
# Smoke test (no publica nada)
bash /home/mlorenzo/docker/app2-notibot-V2/deploy/smoke_test.sh

# Cron real (publica 1 nota y avisa por Telegram)
docker exec notibot-cron-a sh -c 'cd /app && python cron_worker_a.py'

# Log
docker exec notibot-cron-a sh -c 'tail -60 /app/logs/cron_a.log'
```

Para las fuentes RSS, el scraper solo necesita sus variables:

```bash
docker exec notibot-cron-a sh -c 'cd /app && SCRAPER_DEBUG=1 \
  RSS_SOURCE_KEY=caba360 \
  RSS_SITE_URL=https://caba360.com.ar/ \
  RSS_FEED_URL=https://caba360.com.ar/feed.xml \
  python scraping/scraper_rss.py'
```

## Rollback

```bash
cd /home/mlorenzo/docker/app2-notibot-V2
cp -r deploy/backup/<FECHA>/. .
cd /home/mlorenzo/docker && docker compose restart app2-notibot notibot-cron-a notibot-cron-b notibot-cron-c notibot-cron-d
```
