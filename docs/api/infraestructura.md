# Infraestructura

Interfaces que aíslan a `Tempo` y a la CLI del sistema. Cada una es un `typing.Protocol` con una implementación real y una para tests.

| Interfaz | Real | Tests | Módulo |
|---|---|---|---|
| `Clock` | `SystemClock` | `FakeClock` | `clock.py` |
| `Terminal` | `PosixTerminal` | `DummyTerminal` | `terminal.py` |
| `Supervisor` | `ProcessSupervisor` | `FakeSupervisor` | `supervisor.py` |
| `Alerter` | `WindowsToastAlerter`, `NotifySendAlerter`, `FirstWorkingAlerter`, `NullAlerter` | `RecordingAlerter` | `alerts.py` |
| `ReportStore` | `DiskReportStore` | `MemoryReportStore` | `store_files.py` |
| `Renderer` | `TerminalRenderer`, `CsvRenderer`, `MarkdownRenderer` | — | `report.py` |
| `Store` | `SqliteStore` (también sobre `:memory:` en tests) | — | `db.py`, ver [persistencia](persistencia.md) |

## `Clock`

```python
class Clock(Protocol):
    def now(self) -> datetime: ...
    def wait(self, event: threading.Event, seconds: float | None) -> bool: ...
```

- `now()` devuelve un `datetime` con zona horaria.
- `wait()` espera `seconds` (`None`: sin límite) o hasta que se active `event`, y devuelve `True` si terminó por el evento.

`SystemClock.now()` es `datetime.now().astimezone()` y `wait` es `event.wait(seconds)`.

`FakeClock(start)` exige un `start` con zona. El tiempo avanza solo con `advance(timedelta)` o `set(datetime)`. Su `wait` no duerme: si el evento está activo devuelve `True` sin avanzar, y si no avanza `seconds` y devuelve `event.is_set()`.

## `Terminal`

```python
class Terminal(Protocol):
    def write(self, text: str) -> None: ...
    def error(self, text: str) -> None: ...
    def width(self) -> int: ...
    def text(self, prompt: str, default: str = "") -> str: ...
    def choose(self, prompt: str, options: Sequence[str], *, title: str,
               allow_typed: bool) -> Choice: ...
    def confirm(self, prompt: str) -> bool: ...
    def duration(self, prompt: str) -> int: ...
```

| Método | Contrato |
|---|---|
| `write` | Una línea a la salida |
| `error` | Un mensaje de error |
| `width` | Ancho disponible en caracteres |
| `text` | Respuesta sin espacios en los extremos; vacía → `default` |
| `choose` | Muestra `title:` y debajo las opciones numeradas. `Picked(index)` o, con `allow_typed`, `Typed(text)`. Repite hasta obtener una respuesta válida |
| `confirm` | `True`/`False`. Vacío es "no". Repite ante respuestas inválidas |
| `duration` | Segundos. Repite hasta que `durations.parse` devuelva `Ok` |

La interpretación de respuestas es pura y compartida (`read_choice`, `read_yes_no`), sin herencia entre implementaciones. `read_choice` acepta un número de 1 a `len(options)` o el texto exacto de una opción; con `allow_typed`, cualquier otro texto no vacío y no numérico es `Typed`.

`PosixTerminal` usa `input()`/`print()`, manda `error` a stderr con el prefijo `Error: ` y toma `width` de `shutil.get_terminal_size()`. Un `EOFError` o `KeyboardInterrupt` en `input()` se propaga hasta `cli.entry()`.

`DummyTerminal(answers, width=200)` consume respuestas guionadas en orden y registra todo en `lines` (salida) y `errors`. Si una pregunta no tiene respuesta preparada lanza `AssertionError`; `unused_answers` permite verificar que no sobraron.

## `Supervisor`

```python
class Supervisor(Protocol):
    def sync(self) -> None: ...
```

Asegura que el proceso de aviso esté vivo y le indica que relea la base. Es la única dependencia de `Tempo` hacia ese proceso.

```python
ProcessSupervisor(pid_path, lock_path, log_path, command, env)
    .sync()
    .running_pid() -> int | None
    .start_time(pid) -> int | None     # staticmethod: campo 22 de /proc/<pid>/stat; None si no existe o es zombie
```

`cli.notifier_supervisor(config)` arma el comando (`python -m tempo notifier`) y el `PYTHONPATH`. El funcionamiento completo, incluida la carrera del arranque, está en [notificaciones.md](../notificaciones.md#processsupervisor).

`FakeSupervisor` cuenta las llamadas en `calls`.

## `Alerter`

```python
class Alerter(Protocol):
    def send(self, title: str, body: str) -> Result[None, AlertFailed]: ...
    def alarm(self, title: str, body: str, duration: timedelta) -> Result[None, AlertFailed]: ...
    def silence(self) -> Result[None, AlertFailed]: ...
```

| Método | Contrato |
|---|---|
| `send` | Aviso común |
| `alarm` | Aviso que suena en loop hasta `silence()` o hasta `duration` |
| `silence` | Corta la alarma sonando; sin alarma, `Ok` |

| Implementación | |
|---|---|
| `WindowsToastAlerter` | Toast WinRT vía `powershell.exe`; la alarma es un toast `scenario="alarm"` que `silence` saca por tag y grupo |
| `NotifySendAlerter` | `notify-send`; `alarm` es un aviso común y `silence` no hace nada |
| `FirstWorkingAlerter(alerters)` | `send` y `alarm` prueban en orden: `Ok` del primero que funcione o `AlertFailed` con todos los motivos unidos por `; `. `silence` se pide a todos y junta los motivos de los que fallan |
| `NullAlerter` | Siempre `Ok`, no muestra nada |
| `RecordingAlerter` | Registra en orden en `events`: `("send", title, body)`, `("alarm", title, body)`, `("silence",)` |

Las implementaciones que ejecutan comandos convierten `OSError`, `TimeoutExpired` (30 s) y códigos de salida distintos de cero en `AlertFailed`. Detalle del toast en [notificaciones.md](../notificaciones.md#toast-de-windows-desde-wsl).

## `ReportStore`

```python
class ReportStore(Protocol):
    def save(self, name: str, content: str, *, overwrite: bool) -> Result[Path, FileExists]: ...
```

`name` es relativo al directorio del store; una ruta absoluta lo reemplaza. Con `overwrite=False`, un archivo existente da `FileExists` y no se escribe nada.

`DiskReportStore(directory)` resuelve la ruta, crea los directorios, escribe en UTF-8 con `newline=""` y usa el modo `"x"` cuando no debe sobrescribir. `MemoryReportStore(directory)` guarda en el diccionario `files: dict[Path, str]`.

## `Renderer`

```python
class Renderer(Protocol):
    def render(self, report: Report, out: TextIO) -> None: ...
```

`render_to_text(renderer, report) -> str` lo vuelca a un string. Formatos en [reportes.md](../reportes.md).

## `Config`

No es una interfaz, es un dato: `@dataclass(frozen=True, slots=True)`.

| Campo | Default |
|---|---|
| `home` | `~/.local/tempo` (o `$TEMPO_HOME`), expandido y resuelto |
| `notify_enabled` | `True`; `False` con `TEMPO_NO_NOTIFY=1` |
| `pause_reminders` | `(30 min, 1 h)` |
| `alarm_duration` | 5 min |
| `list_days` | 7 |
| `report_days` | 30 |

Propiedades derivadas: `db_path`, `pid_path`, `lock_path`, `reports_dir`. `Config.from_env(env)` es la única lectura del entorno en todo el programa; `cli.main` la llama una vez y reparte a cada pieza solo el valor que necesita.
