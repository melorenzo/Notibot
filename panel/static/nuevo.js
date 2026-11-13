// panel/static/nuevo.js — prueba de conexión del formulario de alta
(function () {
  var boton = document.getElementById('btn-probar');
  var salida = document.getElementById('prueba-res');
  if (!boton || !salida) return;

  function valor(id) {
    var el = document.getElementById(id);
    return el ? el.value.trim() : '';
  }

  function pintar(clase, texto) {
    salida.className = 'prueba-resultado ' + clase;
    salida.textContent = texto;
  }

  boton.addEventListener('click', async function () {
    var baseUrl = valor('base_url');
    var usuario = valor('usuario');
    var password = document.getElementById('password').value;
    if (!baseUrl || !usuario || !password) {
      pintar('err', 'Completá dominio, usuario y contraseña para probar.');
      return;
    }
    boton.disabled = true;
    pintar('info', 'Probando login JWT…');
    try {
      var resp = await fetch('/api/probar', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ base_url: baseUrl, usuario: usuario, password: password })
      });
      var data = await resp.json();
      if (data.ok) {
        pintar('ok', 'Conexión OK' + (data.sitio ? ' — ' + data.sitio : '') + '. ' + (data.mensaje || ''));
      } else {
        pintar('err', (data.mensaje || 'No pude conectar.') + (data.estado ? ' [' + data.estado + ']' : ''));
      }
    } catch (e) {
      pintar('err', 'Error consultando el panel: ' + e.message);
    } finally {
      boton.disabled = false;
    }
  });
})();
