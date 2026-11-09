# Notificaciones y proceso de aviso

La CLI termina después de cada comando, así que los avisos los manda un proceso aparte, `tempo notifier`. No guarda estado propio relevante: lee la base, calcula el próximo evento y duerme hasta entonces. La base sigue siendo la única fuente de verdad; matar el proceso no pierde nada.

Tres piezas:

| Pieza | Módulo | Rol |
|---|---|---|
| `Notifier` | `notifier.py` | Decide qué avisar y cuándo |
| `ProcessSupervisor` | `supervisor.py` | Mantiene un único proceso de aviso vivo y le indica que relea la base |
| `Alerter` | `alerts.py` | Muestra la notificación |

## `Notifier`

```python
Notifier(store, clock, alerter, pause_reminder)
    .tick() -> float | None        # un paso: avisa lo pendiente, devuelve segundos a esperar
    .run(wake: threading.Event)    # loop: tick, clock.wait(wake, timeout), wake.clear()
```

`tick()` lee `store.active_session()` y calcula `session_state` con `clock.now()`:

| Estado | Acción | Devuelve |
|---|---|---|
| sin sesión abierta | desarma | `None` (espera sin límite) |
| `running` | arma `(session.id, expires_at)` | segundos hasta `expires_at` |
| `expired` y armado con ese mismo `(id, expires_at)` | desarma y avisa **Sesión terminada** | `None` |
| `expired` sin armar | nada | `None` |
| `paused`, pausa abierta con `reminded = 1` | desarma | `None` |
| `paused`, `now < inicio + pause_reminder` | desarma | segundos hasta `inicio + pause_reminder` |
| `paused`, `now >= inicio + pause_reminder` | desarma, `mark_pause_reminded(pause.id)`, avisa **Pausa larga** | `None` |

Las reglas que salen de la tabla:

- **El vencimiento se avisa una vez y solo si el proceso lo vio venir.** El arming en memoria evita repetir el aviso en cada `tick` y evita avisar un vencimiento viejo cuando el proceso arranca con la sesión ya vencida. Un `extend` produce un `expires_at` nuevo, que se arma en el siguiente `tick`; un `resume` también.
- **La pausa larga se avisa una sola vez por pausa,** también entre reinicios del proceso: el flag `reminded` vive en la base. `pause_reminder` es 1 h (`Config.pause_reminder`).

Textos:

```
Sesión terminada   Se cumplió el tiempo de web (1:30). Usá tempo extend para seguir o tempo stop para cerrarla.
Pausa larga        La sesión de web lleva 1:00 pausada.
```

El título no repite el nombre de la aplicación: con la aplicación registrada en Windows, el encabezado del toast ya muestra "tempo".

`run` duerme con `clock.wait(wake, timeout)`. Con `SystemClock` es `Event.wait`, que el handler de `SIGHUP` interrumpe al hacer `wake.set()`; el `tick` siguiente lee el estado nuevo. Si la señal llega entre el `wait` y el `clear()` se pierde, pero da igual: el loop hace `tick` inmediatamente después y la escritura que motivó la señal ya está commiteada.

Una falla del alerter no interrumpe el loop: se escribe en stderr (`tempo notifier: no se pudo avisar: ...`), que va a `notifier.log`.

## `ProcessSupervisor`

`supervisor.sync()` se llama en dos momentos: al inicio de cada comando de la CLI, a través de `Tempo.ensure_notifier()`, y después de cada operación de `Tempo` que escribe (15 en total: sesiones, edición, borrado, clientes, proyectos y exportación). Lo primero relanza un proceso caído aunque el comando sea una lectura (`status`, `list`, `report`); lo segundo hace que relea la base apenas cambia. En un comando que escribe, el proceso recibe dos `SIGHUP`, lo que solo cuesta un `tick` extra.

```
sync():
    (pid, inicio) = leer notifier.pid
    si inicio(pid) == inicio:              kill(pid, SIGHUP); fin
    flock(notifier.lock, LOCK_EX)              # se libera al cerrar el archivo
        si ahora hay uno vivo:                 fin   (otro comando lo lanzó mientras esperábamos)
        pid = posix_spawn(...)
        escribir "pid inicio(pid)" en notifier.pid
```

**Identificación.** `notifier.pid` guarda `PID inicio`, donde `inicio` es el instante de creación del proceso: el campo 22 de `/proc/<pid>/stat` (`starttime`, en ticks de reloj desde el arranque). `running_pid()` considera vivo el proceso si ese PID existe, no está en estado zombie (`Z`, `X`) y su inicio coincide. Un PID reciclado por otro programa tiene otro inicio. Al leer `stat`, los campos se separan después del último `)`, porque el nombre del proceso (campo 2) puede contener espacios y paréntesis.

El inicio existe desde la creación del proceso y el `exec` no lo cambia. Por eso la identidad no se toma de los argumentos: `/proc/<pid>/cmdline` queda vacío durante un instante mientras el hijo todavía está ejecutando el `exec` del intérprete (del orden de 1 de cada 300 a 1000 lanzamientos, consultando inmediatamente después de `posix_spawn`), y un `sync()` que cayera en esa ventana no reconocería el proceso recién lanzado y lanzaría un segundo.

Si el hijo muere antes de que el supervisor lea su inicio (por ejemplo, por un error al importar), no se escribe `notifier.pid`, y el próximo comando lo relanza.

**Exclusión.** El chequeo y el lanzamiento van bajo `flock` y el PID lo escribe el supervisor antes de soltar el lock, no el proceso hijo. Así un segundo comando que espere el lock siempre encuentra el PID del proceso recién lanzado.

**Lanzamiento.**

```python
os.posix_spawn(
    sys.executable, [sys.executable, "-m", "tempo", "notifier"], env,   # env con PYTHONPATH
    file_actions=[
        (POSIX_SPAWN_OPEN, 0, os.devnull, O_RDONLY, 0),
        (POSIX_SPAWN_OPEN, 1, ".../notifier.log", O_WRONLY | O_CREAT | O_APPEND, 0o644),
        (POSIX_SPAWN_DUP2, 1, 2),
    ],
    setsid=True,
    setsigmask={SIGHUP},
)
```

- `PYTHONPATH` apunta al directorio que contiene el paquete: la raíz del repo al correr con `python -m tempo`, o el propio archivo zipapp (`~/.local/bin/tempo`) en la instalación. El mismo comando sirve en los dos casos.
- `setsid=True` crea una sesión nueva sin terminal de control: cerrar la terminal no le manda `SIGHUP` y Ctrl+C no le llega.
- `posix_spawn` en lugar de `subprocess.Popen`: el proceso es desacoplado a propósito, y un `Popen` descartado con el hijo vivo emite `ResourceWarning`. Además `Popen` no permite fijar la máscara de señales del hijo.

**La carrera del arranque.** La acción por defecto de `SIGHUP` es terminar el proceso. `tempo start` escribe varias veces seguidas (crear cliente, crear proyecto, iniciar la sesión), y el segundo `sync()` puede mandar la señal antes de que el intérprete del hijo haya instalado el handler. Por eso el hijo nace con `SIGHUP` bloqueada (`setsigmask`). `serve_notifier` instala el handler y recién después la desbloquea con `pthread_sigmask(SIG_UNBLOCK, {SIGHUP})`: una señal que haya llegado durante el arranque queda pendiente y se entrega al handler en ese momento. `test_sighup_reloads_without_terminating` reproduce el caso mandando la señal inmediatamente después del lanzamiento, y falla si se quita `setsigmask`.

### Limitaciones

- **El proceso se relanza recién con el próximo comando.** Si muere con una sesión corriendo (reinicio de WSL o de Windows, `wsl --shutdown`, `kill`), queda caído hasta que se ejecute cualquier comando de tempo. Si para entonces la sesión ya venció, ese vencimiento no se avisa, porque el proceso no lo vio venir; `status` lo muestra como vencida.
- **Depende de Linux:** `/proc`, `flock`, `posix_spawn` con `setsid` y `setsigmask`. En otro sistema hace falta otra implementación de `Supervisor`.
- El proceso no termina solo: queda esperando sin límite cuando no hay sesión. `uninstall.sh` lo detiene.

## `Alerter`

```python
class Alerter(Protocol):
    def send(self, title: str, body: str) -> Result[None, AlertFailed]: ...
```

`build_alerter(config)` devuelve `NullAlerter` si `TEMPO_NO_NOTIFY=1` y, si no, `FirstWorkingAlerter([WindowsToastAlerter(), NotifySendAlerter()])`, que prueba en orden y devuelve el primer `Ok` o un `AlertFailed` con los motivos de todos.

### Toast de Windows desde WSL

`WindowsToastAlerter` ejecuta `powershell.exe -NoProfile -NonInteractive -Command <script>` con timeout de 30 s. El script carga `Windows.UI.Notifications` (WinRT), arma un `ToastText02` (título y cuerpo) y lo muestra con `CreateToastNotifier(appId).Show(...)`.

- Título y cuerpo viajan en variables de entorno (`TEMPO_TITLE`, `TEMPO_BODY`) y no interpolados en el script: no hay que escapar comillas ni caracteres especiales. `WSLENV=TEMPO_TITLE:TEMPO_BODY` hace que WSL las exporte al proceso de Windows.
- Windows solo muestra toasts de aplicaciones registradas, identificadas por un AppUserModelID que define el nombre y el ícono del encabezado. El script usa `tempo` si existe `HKCU:\Software\Classes\AppUserModelId\tempo`, y si no el AUMID de Windows PowerShell, que siempre está registrado (el toast se ve entonces como "Windows PowerShell").
- Un `powershell.exe` ausente, un código de salida distinto de 0 o un timeout dan `AlertFailed`.

### Registro de la aplicación

`install.sh`, cuando encuentra `powershell.exe`:

1. Obtiene `LocalApplicationData` de Windows (`C:\Users\<usuario>\AppData\Local`) y genera `tempo\tempo.png` con `scripts/make_icon.py`.
2. Crea `HKCU\Software\Classes\AppUserModelId\tempo` con `DisplayName = tempo` e `IconUri` apuntando al PNG.

Es una clave del usuario: no requiere administrador. `uninstall.sh` borra la clave y el directorio del ícono.

### `notify-send`

Alternativa para Linux con libnotify. En WSL normalmente no está instalado; queda como segundo intento de la cadena.
