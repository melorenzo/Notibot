# Notibot V3 — Dos Crons Independientes

## Arquitectura

```
Cron A  →  fuentes A (ba_static → lanacion → equipoba)  →  palabras_red
Cron B  →  fuentes B (equipoba → ba_static → lanacion)  →  [2do diario cuando lo tengas]

          ↘                                            ↗
           used_items.json (compartido, evita duplicados entre crons)
```

Cada cron tiene su propia rotación de fuentes (`cron_a_rotation.json` / `cron_b_rotation.json`),
arrancando en posiciones distintas para que las publicaciones varíen.

---

## Fuentes disponibles

### Fuentes con feed RSS (las que más noticias aportan)

Se scrapean con un único script genérico, `scraping/scraper_rss.py`, que se
configura por variables de entorno. La configuración vive en
`scraping/rss_sources.py` (una sola lista para los 4 workers y el bot).

| key | Label | Feed | Notas por feed |
|---|---|---|---|
| `caba360` | CABA 360 | `https://caba360.com.ar/feed.xml` | 30 |
| `noticiascaba` | Noticias CABA | `https://noticiascaba.com.ar/feed.xml` | 30 |
| `infoporteo` | Infoporteño | `https://xn--infoporteo-19a.com.ar/feed.xml` | 30 |

Cada item se enriquece con la nota original (1 request por publicación):
imagen de portada (`og:image`) y los primeros párrafos del cuerpo, para que la
IA reciba contexto suficiente.

### Fuentes HTML / Selenium

| key | Label | Script | Notas |
|---|---|---|---|
| `ba_static` | BA Noticias (GCBA static) | `scraping/scraper_ba_static.py` | 9 por página × 3 páginas |
| `ba_noticias` | BA Noticias | `scraping/scraper_ba_noticias.py` | ~9 |
| `lanacion` | La Nación Ciudad | `scraping/scraper_lanacion.py` | 12 |
| `infobae` | Infobae | `scraping/scraper_infobae.py` | ~10 (ya no usa Selenium) |
| `equipoba` | Equipo BA | `scraping/scraper_equipoba.py` | SPA con login, flaky |
| `x` | X (Twitter) | `scraping/scraper_x.py` | requiere `TWITTER_BEARER_TOKEN` |
| `linkedin` | LinkedIn | `scraping/scraper_linkd.py` | vista pública (sin sesión) |

Las fuentes que necesitan credenciales declaradas en `requires_env` (por
ejemplo X) se saltean automáticamente si la variable no está seteada: antes
el cron gastaba hasta 25 minutos en 3 reintentos inútiles.

---

## Archivos nuevos a copiar al proyecto

```
app2-notibot-V2/
├── cron_base.py           ← lógica compartida (requerida por A y B)
├── cron_worker_a.py       ← Cron A: palabras_red
├── cron_worker_b.py       ← Cron B: 2do diario
├── core/
│   └── dedup.py           ← hashes de deduplicación (URL + título + legacy)
└── scraping/
    ├── scraper_ba_static.py    ← reemplaza al viejo scraper_ba_noticias.py
    ├── scraper_equipoba.py     ← nuevo: SPA con Selenium
    ├── scraper_rss.py          ← genérico para feeds RSS/Atom
    ├── rss_sources.py          ← registro de las fuentes RSS
    ├── enrich.py               ← imagen + copete de la nota original
    └── paths.py                ← resolución de rutas de salida
```

---

## Variables de entorno nuevas (agregar al variables.env)

```env
# ── Cron A ────────────────────────────────────────
CRON_A_DIARIO=palabras_red
CRON_A_TONO=entusiasta          # serio | entusiasta | realista

# ── Cron B ────────────────────────────────────────
CRON_B_DIARIO=palabras_red      # ← cambiar cuando agregues el 2do diario
CRON_B_TONO=serio

# ── Común ─────────────────────────────────────────
CRON_MAX_RETRIES=3
TELEGRAM_USER_ID=6165724246     # tu Telegram ID para recibir notificaciones

# ── URLs de los nuevos scrapers ───────────────────
BA_STATIC_URL=https://static.buenosaires.gob.ar/noticias
BA_STATIC_MAX_PAGES=3
BA_NOTICIAS_URL=https://buenosaires.gob.ar/noticias
EQUIPOBA_URL=https://equipoba.buenosaires.gob.ar/app/billboard
SALIDAS_BA_STATIC=/app/salidas_ba_static.json
SALIDAS_BA_NOTICIAS=/app/salidas_ba_noticias.json
SALIDAS_EQUIPOBA=/app/salidas_equipoba.json

# ── Paths de salida (ABSOLUTOS) ───────────────────
# Con rutas relativas los scrapers morían al hacer
# os.makedirs(os.path.dirname(path)) y no escribían el JSON.
SALIDAS_LANACION=/app/salidas_lanacion.json
SALIDAS_INFOBAE=/app/salidas_infobae.json
SALIDAS_X=/app/salidas_x.json
SALIDAS_LINKEDIN=/app/salidas_linkedin.json
```

---

## Cuando el cron dice "no quedan noticias"

Antes de quejarse de la fuente, mirá el log: cada scraper ahora loguea su
`stdout`, que es donde imprimen el error real.

```bash
docker exec notibot_v2 sh -c "cd /app && grep -E 'sin resultados|Fallback|Todas' /app/logs/cron_a.log | tail -20"
```

Causas habituales:

1. **La fuente no trae nada nuevo** → el mensaje "Todas las noticias
   disponibles ya fueron publicadas" lista las fuentes agotadas. Con las
   3 fuentes RSS (30 notas cada una) esto no debería pasar seguido.
2. **El scraper falló** → el log muestra `exit=1 stdout=...` con el motivo.
3. **Faltan credenciales** → el log dice "Fuente X salteada: falta Y".
4. **El portal cambió el HTML** → el scraper devuelve 0 items. Se arregla
   actualizando los selectores en el `scraper_*.py` correspondiente.

### Probar un scraper suelto

```bash
docker exec notibot_v2 sh -c "cd /app && SCRAPER_DEBUG=1 python scraping/scraper_rss.py"
```

Para las fuentes RSS hay que pasar la configuración:

```bash
docker exec notibot_v2 sh -c "cd /app && SCRAPER_DEBUG=1 \
  RSS_SOURCE_KEY=caba360 \
  RSS_SITE_URL=https://caba360.com.ar/ \
  RSS_FEED_URL=https://caba360.com.ar/feed.xml \
  python scraping/scraper_rss.py"
```

---

## Configurar el cron del host

```bash
crontab -e
```

Agregar (horarios desfasados para que no corran al mismo tiempo):

```cron
# Notibot V3 — Cron A (palabras_red): 8:00 y 14:00
0 8,14 * * * docker exec notibot_v2 sh -c "cd /app && python cron_worker_a.py >> /app/logs/cron_a.log 2>&1"

# Notibot V3 — Cron B (2do diario): 10:00 y 18:00
0 10,18 * * * docker exec notibot_v2 sh -c "cd /app && python cron_worker_b.py >> /app/logs/cron_b.log 2>&1"
```

---

## Probar manualmente

```bash
# Probar Cron A
docker exec notibot_v2 python cron_worker_a.py

# Probar Cron B
docker exec notibot_v2 python cron_worker_b.py

# Probar scrapers individuales
docker exec notibot_v2 python scraping/scraper_ba_static.py
docker exec notibot_v2 python scraping/scraper_equipoba.py
```

---

## Cuando agregues el 2do diario

1. Agregar en `config/diarios.json`:
```json
"nuevo_diario": {
    "name": "Nombre del Diario",
    "base_url": "https://...",
    "wp_posts_endpoint": "/wp-json/wp/v2/posts",
    "wp_media_endpoint": "/wp-json/wp/v2/media",
    "jwt_token_url": "/wp-json/jwt-auth/v1/token",
    "username_env": "ND_USER",
    "password_env": "ND_PASS",
    "allowed_telegram_ids": {"6165724246": "Maty"},
    "default_status": "publish",
    "default_author": null,
    "default_categories": [],
    "default_tags": []
}
```

2. Agregar credenciales en `variables.env`:
```env
ND_USER=usuario
ND_PASS=contraseña
```

3. Cambiar en `variables.env`:
```env
CRON_B_DIARIO=nuevo_diario
```

Listo, sin tocar ningún archivo Python.

---

## Sobre equipoba.buenosaires.gob.ar/app/billboard

Es una SPA con JavaScript obligatorio (React/Vue). El scraper usa Selenium como el de Infobae.
Si el GCBA cambia el frontend, revisar los selectores CSS en `scraper_equipoba.py` función `_extract()`.
Los debug screenshots quedan en `/app/debug/equipoba_*.png`.

## Sobre la imagen automática

Los artículos de `static.buenosaires.gob.ar` tienen imágenes propias del GCBA bajo
**Creative Commons 2.5 Argentina** — son libres de usar. El campo `imagen_url` viene
en los items scrapeados. En una futura versión se puede subir automáticamente con
`wp_upload_media()` del V2.
