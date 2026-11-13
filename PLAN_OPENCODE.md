# Notibot — Plataforma de alta automática de diarios

Plan de implementación para ejecutar con opencode, fase por fase.
Ubicación del proyecto en el server: `/home/mlorenzo/docker/app2-notibot-V2`

## 0. Cómo usar este plan con opencode

1. Antes de empezar: backup completo, `tar czf ~/notibot_backup_$(date +%F).tgz /home/mlorenzo/docker/app2-notibot-V2` y, si no hay git, `git init` + commit inicial.
2. Pedirle a opencode que trabaje **una fase por vez**, primero en modo plan (leer el código y proponer cambios) y recién después implementar.
3. Al terminar cada fase: correr los criterios de aceptación, hacer commit y recién entonces pasar a la siguiente.
4. No avanzar de fase si los 8 diarios actuales dejaron de publicar.

## 1. Contexto técnico (hechos verificados)

- Stack: Docker Compose, Python 3.9 dentro del container (`app2-notibot:latest`), imagen compartida por todos los servicios del notibot.
- Estructura relevante en `/app` (= `app2-notibot-V2`): `cron_base.py` (lógica común, función `run_pipeline`), `cron_worker_a.py` … `cron_worker_h.py` (workers casi idénticos), `core/diarios.py` (`all_diarios`, `posts_url`, `media_url`), `config/diarios.json`, `scraping/` (scrapers + `rss_sources`), `procesing/` (IA con Groq), `token_jwt/token_store.py` (`get_token_for_diario`), `data/` (estado), `variables.env`.
- Diarios actuales (slug → worker): `palabras_red` (A), `horizonteba` (B), `pulsoporteno` (C), `latin_tinta` (D), `diario_ciudad` (E), `egos_noticias` (F), `loscienbarrios` (G), `delidiet` (H).
- Cada diario es un WordPress con plugin **JWT Authentication for WP REST API**. Las credenciales NO están en `diarios.json`: el JSON guarda los *nombres* de variables (`username_env`, `password_env`) y los valores viven en `variables.env`.
- Hoy hay un container por worker (`notibot-cron-a` … `-h`) que solo duerme; el cron del **host** hace `docker exec`. Un horario por diario, una pasada diaria.
- Rotación de fuentes: `next_source` mantiene un índice por worker en `data/cron_X_rotation.json`; la fuente rotada va primero y el resto se prueba en cascada. Rotación de tono: archivo `data/cron_X_tono.json`.
- Fuentes (8): infobae, equipoba, ba_noticias, lanacion, ba_static, y por RSS: infoporteo, noticiascaba, caba360. Cada scraper escribe a `salidas_<fuente>.json` en la raíz, compartido por todos los workers (cada ejecución borra el archivo antes de correr).
- Estado compartido de notas usadas: `data/used_items.json` (se lee al inicio y se reescribe completo al final).
- Un único Selenium (`selenium_chrome`) compartido.
- Notificaciones Telegram: `tg_send` envía a un único `TELEGRAM_USER_ID` (env). Existe un bot principal (`app2-notibot`) que publica a mano y usa `allowed_telegram_ids` del JSON para permisos.
- Red: el server está detrás de **Cloudflare Tunnel** (sin IP fija). Egos Argentinos tiene un desafío de Cloudflare que bloquea las peticiones del notibot por el dominio público, por eso su `base_url` es `http://wordpress-egos` (nombre del servicio en la red interna de Docker). Los sitios locales pueden usarse así; los externos deben usar su URL pública.
- Los sitios WordPress locales están en carpetas `sitioN` y se publican por Nginx Proxy Manager (el puerto de `set $port` identifica el container).

## 2. Reglas para opencode

- **No romper lo que anda.** Los 8 diarios deben seguir publicando igual durante y después de cada fase. Cambios con valores por defecto compatibles.
- Compatible con **Python 3.9** (sin `match`, sin `X | Y` en type hints en runtime).
- No tocar los scrapers ni `procesing/` salvo indicación expresa.
- **Secretos:** nunca loguear ni imprimir claves, tokens ni el contenido de `variables.env`. No incluir secretos en commits ni en `diarios.json`. Archivos de secretos con permisos 600.
- Todo cambio sobre `config/diarios.json` y archivos de estado: **escritura atómica** (archivo temporal + rename) y backup previo con fecha.
- Cualquier escritura concurrente a archivos compartidos debe estar protegida con candado de archivo.
- Idioma de mensajes de usuario y logs: español rioplatense.
- Documentar cada fase en `docs/` y mantener un `CHANGELOG.md`.

## 3. Fases

### Fase 1 — Worker genérico y configuración por diario

Objetivo: reemplazar los 8 `cron_worker_X.py` por un único `cron_worker.py <slug>`.

- Nuevos campos opcionales en cada entrada de `diarios.json`, con defaults que reproduzcan el comportamiento actual:
  - `activo` (bool, default true)
  - `tonos` (lista; válidos: serio, entusiasta, realista, ironico)
  - `orden_fuentes` (lista de keys; default: orden estándar)
  - `horarios` (lista de "HH:MM"; default vacío)
  - `used_path` (default: `data/used_items.json` compartido)
  - `notify_telegram_ids` (ver Fase 3)
- El worker genérico arma el catálogo de fuentes una sola vez (reutilizando la definición actual), lo ordena según `orden_fuentes` y delega en `run_pipeline`.
- Estado (`rotation`, `tono`) por slug en `data/<slug>_rotation.json` y `data/<slug>_tono.json`. Migrar los índices actuales de `cron_X_rotation.json` para no perder la posición.
- Los `cron_worker_X.py` actuales quedan como wrappers mínimos de una línea hasta que la Fase 2 esté estable.

Aceptación: `python cron_worker.py diario_ciudad` publica igual que `cron_worker_e.py`; los 8 diarios migrados a la config; sin cambios de comportamiento visibles en Telegram.

### Fase 2 — Planificador único con candado global

Objetivo: dejar de editar el crontab del host y evitar solapamientos.

- Un solo container (`notibot-scheduler`) que lee `horarios` de `diarios.json` y dispara cada diario a su hora (zona `America/Argentina/Buenos_Aires`).
- **Candado global** (archivo, `fcntl.flock`) alrededor de cada ejecución: si hay un worker corriendo, el siguiente espera en cola. Esto resuelve los choques sobre `salidas_*.json`, `used_items.json` y Selenium.
- Recarga de la config sin reiniciar (relee `diarios.json` periódicamente).
- Comando manual `python cron_worker.py <slug> --ahora` y listado `--proximas`.
- Logs por diario en `logs/<slug>.log` con rotación.
- Reemplazo del cron del host: documentar cómo retirar las líneas `docker exec notibot-cron-*` una vez validado.

Aceptación: dos diarios programados a la misma hora corren en serie, sin errores; reiniciar el scheduler no dispara corridas duplicadas.

### Fase 3 — Notificaciones por diario

Objetivo: que cada diario avise a quien corresponda.

- Modificar `tg_send`, `tg_success`, `tg_error` para aceptar una lista de `chat_id`.
- Config: `notify_telegram_ids` (éxitos y avisos del diario) y siempre el admin (`TELEGRAM_USER_ID`) para **errores técnicos**. Opción `notify_errores` (default solo admin).
- Si un destinatario no puede recibir mensajes (no hizo `/start`), registrar el aviso en log y continuar sin romper la corrida ni los demás envíos.
- El botón "Cambiar imagen" debe seguir funcionando solo para IDs autorizados (`allowed_telegram_ids`).

Aceptación: un diario con dos IDs en `notify_telegram_ids` los notifica a ambos; un ID inválido no impide la publicación ni el aviso al admin.

### Fase 4 — Servicio de alta (núcleo)

Objetivo: una función única `crear_diario(...)` que usen todas las interfaces.

Entrada: nombre, slug, `base_url`, usuario WP, clave WP, tonos, horarios, `notify_telegram_ids`, orden de fuentes (opcional, se genera uno distinto a los existentes para repartir carga).

Pasos del servicio:
1. Validar: slug único y bien formado; URL válida; sin trailing slash.
2. Probar conectividad y login JWT (`/wp-json/jwt-auth/v1/token`). Distinguir y reportar con mensajes claros: respuesta de Cloudflare ("Just a moment"), 404 (plugin ausente), `incorrect_password`, `jwt_auth_bad_config`.
3. Si el sitio es local y no responde por el dominio público, sugerir y probar la URL interna `http://<servicio>` en la red de Docker.
4. Guardar credenciales en un **archivo de secretos** (por ejemplo `config/secrets.env`, permisos 600) que el worker lee en cada corrida, para no tener que recrear containers. Mantener compatibilidad con `username_env`/`password_env`.
5. Escribir la entrada en `diarios.json` (backup + escritura atómica).
6. Publicar una nota de prueba como borrador (`status: draft`) y reportar el resultado; opción de borrarla.
7. Dejar el diario con `activo: true` solo si todo salió bien.

Funciones adicionales: `listar_diarios`, `pausar/activar`, `editar`, `eliminar` (con confirmación y backup).

Aceptación: dar de alta un diario de prueba de punta a punta sin editar archivos a mano ni recrear containers; los errores de validación son legibles.

### Fase 5 — Interfaz por Telegram

- Solo el admin (ID `6165724246`) puede usar los comandos de gestión.
- Asistente conversacional `/nuevo_diario`: pregunta nombre, URL, usuario, clave, tonos, horarios y destinatarios. **Borrar el mensaje que contiene la clave** apenas se recibe.
- Comandos: `/diarios`, `/pausar <slug>`, `/activar <slug>`, `/correr <slug>`, `/estado`.
- Confirmación explícita antes de guardar, mostrando un resumen sin la clave.

Aceptación: alta completa desde el celular; usuarios no admin reciben un rechazo.

### Fase 6 (opcional) — Panel web

- Mini aplicación (FastAPI o similar) sobre el servicio de la Fase 4, sin lógica propia.
- Autenticación obligatoria; exposición solo a través de **Cloudflare Tunnel con Cloudflare Access** (o accesible únicamente por red local/VPN). Nunca publicar el panel sin autenticación.
- CSRF, sin mostrar nunca claves guardadas (solo "configurada"), protección de fuerza bruta.
- Pantallas: listado y estado de diarios, alta/edición, pausar, "correr ahora", logs recientes.

Aceptación: mismas operaciones que Telegram, sin duplicar lógica.

### Fase 7 — Asistente de preparación WordPress (opcional, solo sitios locales)

Script `prepare_wp.py <carpeta_sitio>` con modo `--dry-run`:
- Verifica el plugin `jwt-authentication-for-wp-rest-api`.
- Genera una `JWT_AUTH_SECRET_KEY` nueva (`openssl rand`) e inserta el `define` **antes** de "That's all, stop editing" en `wp-config.php` (no duplica si existe; backup previo).
- Agrega las reglas de `Authorization` al `.htaccess` si faltan.
- Para sitios en hosting externo: imprimir un checklist, no tocar nada.

## 4. Backlog de bugs conocidos (incluir cuando se pueda)

1. **Año equivocado en notas reescritas** (apareció "2025" en una nota de 2026): agregar la fecha actual al prompt de `procesing/procesar_gemini.py` con la instrucción de no inventar ni cambiar años; y una verificación posterior que avise por Telegram si el texto reescrito contiene un año que no está en la nota original.
2. **Notas viejas desde `ba_static`** (`static.buenosaires.gob.ar/gcaba_historico/...`): esa fuente no trae fecha (`fecha` viene vacío). Derivar la fecha de la ruta de la imagen (`/AAAA-MM/`) o de la página de la nota y descartar lo que tenga más de N días (configurable).
3. **Validación temprana del diario**: chequear que el slug exista en `diarios.json` y que el login JWT funcione *antes* de scrapear y llamar a la IA (hoy se descubre al final y se gasta scraping e IA en vano).
4. **`used_items.json` compartido**: ofrecer `used_path` por diario para evitar cuello de botella y escrituras pisadas.

## 5. Decisiones pendientes (a definir antes de la Fase 4)

- ¿Las credenciales van en un `secrets.env` único o un archivo por diario?
- ¿Se mantiene el `used_items.json` global o se pasa a uno por diario?
- ¿El panel web (Fase 6) se hace, o alcanza con Telegram?
- Si el panel web se hace: ¿Cloudflare Access (login por correo) o solo red local?
- ¿Qué IDs de Telegram recibirán avisos de qué diario?

## 6. Definición de terminado

- Dar de alta un diario nuevo toma menos de 5 minutos y no requiere editar archivos ni recrear containers.
- Los 8 diarios actuales publican en sus horarios sin solaparse.
- Ninguna clave aparece en logs, en `diarios.json` ni en mensajes de Telegram.
- Existe documentación de operación en `docs/` y un procedimiento de rollback por fase.
