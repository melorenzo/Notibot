
# Notibot Patch — 2025-08-12

Este paquete agrega:
1. **Acceso por usuario de Telegram a cada diario** (configurable)  
2. **JWT centralizado con auto-refresh**, sin reiniciar contenedor  
3. **Cliente WordPress único** (subida de imagen + creación de post)  
4. **Flujo Telegram que mantiene el diario seleccionado luego de pedir imagen**  
5. **Scraper de LinkedIn** que incluye `url` en los items para evitar errores al elegir

## Archivos nuevos
- `config/diarios.json` — *Config de diarios (endpoints, permisos, etc.)*
- `core/diarios.py` — utilidades para cargar/filtrar diarios por usuario
- `token_jwt/token_store.py` — manejo de tokens con refresh y persistencia en `tokens.json`
- `uploading/wp_client.py` — funciones `upload_media`, `create_post`, `publicar`
- `scraping/scraper_linkd.py` — scraper robusto que retorna `titulo`, `descripcion`, `url`
- `bot_telegram_v2.py` — ejemplo de bot con flujo por estados que NO te pide el diario otra vez después de subir imagen

> ⚠️ No borra nada de tu repo. Podés testear `bot_telegram_v2.py` en paralelo.

## Cómo usar
1. **Copiá** la carpeta de este zip dentro de tu proyecto (respetando rutas).  
2. Editá `config/diarios.json` y cargá los **Telegram IDs** que corresponden a cada diario.  
3. Exportá credenciales en el contenedor (o `.env`):  
   ```env
   TELEGRAM_BOT_TOKEN=xxxxx

   LT_USER=...
   LT_PASS=...

   PR_USER=...
   PR_PASS=...

   LVP_USER=...
   LVP_PASS=...

   IC_USER=...
   IC_PASS=...
   ```
4. Ejecutá el bot nuevo:
   ```bash
   python bot_telegram_v2.py
   ```

## Cambios clave para tus módulos actuales
- Eliminá los `TOKEN = "..."` hardcodeados de `uploading/subir_wordpress*.py`.  
  En su lugar, importá y usá:
  ```python
  from core.diarios import get_diario
  from uploading.wp_client import publicar

  diario = get_diario("latin_tinta")
  post_id = publicar(diario, titulo, cuerpo, ruta_imagen)
  ```

- Si mantenés tu `ConversationHandler` actual, asegurate de **guardar `diario_slug` en `context.user_data`** apenas lo elige el usuario, y **NO lo borres** al recibir la imagen. Luego pasá ese slug a `publicar(...)`.

## Sobre el bug de LinkedIn
El error típico al "elegir la noticia" venía de que el item no tenía `url` o las claves no coincidían. El nuevo `scraper_linkd.py` agrega siempre `url` (o `None` de fallback) y arma una `salidas_scraper.json` estándar con `titulo`, `descripcion`, `url`.

Además, el scraper ya **no necesita sesión**: lee la página pública de la empresa
(`https://www.linkedin.com/company/<slug>/`), de donde saca cada post del
`<article data-activity-urn="...">` con el texto en
`<p data-test-id="main-feed-activity-card__commentary">`, resuelve los links
`lnkd.in` a la URL real (ej. `buenosaires.gob.ar`) y descarta los posts de más
de `LINKEDIN_MAX_DAYS` días. Si la vista pública no trae nada, ahí sí recurre a
cookies o usuario/clave sobre `/posts/`.

## Docker / JWT sin reiniciar
Como ahora los `uploading` piden el token **en cada request** usando `token_store.get_token_for_diario(...)`, cuando el token expira se renueva y se **usa al instante**, sin necesidad de reiniciar el contenedor.

---

Cualquier cosa, probamos juntos paso a paso.
