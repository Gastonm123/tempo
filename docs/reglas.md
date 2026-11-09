# Reglas y restricciones

Cada regla indica dónde se hace cumplir. Las que valida la base son invariantes duras: ningún camino de código las puede romper. Las que valida `Tempo` se devuelven como `Err` con un tipo concreto. Las de la CLI son validaciones de entrada.

## Sesiones

| Regla | Dónde | Falla |
|---|---|---|
| A lo sumo una sesión abierta | base: índice parcial `one_active` | `SessionAlreadyActive` |
| A lo sumo una pausa abierta por sesión | base: índice parcial `one_open_pause` | `AlreadyPaused` (validado antes en `Tempo`) |
| `duration_s > 0` | base: `CHECK`; entrada: `durations.parse` | `InvalidDuration` |
| `end_at >= start_at` en sesiones y pausas | base: `CHECK` | `InvalidInterval` en `edit` (validado antes en `Tempo`) |
| No se inicia en un proyecto archivado | `Tempo.start` | `ProjectArchived` |
| No se pausa una sesión vencida | `Tempo.pause` | `SessionExpired` |
| Una sesión vencida queda detenida; el fin registrado es el vencimiento | `sessions.effective_end` | — |
| El tiempo entre vencimiento y `extend` no cuenta | `Tempo.extend` (inserta una pausa) | — |
| Solo se editan sesiones cerradas | `Tempo.edit_session` | `SessionIsActive` |
| Al editar, las pausas se recortan al intervalo nuevo | `Tempo.edit_session` | — |
| Una sesión pertenece al mes local de su inicio y nunca se parte | `Tempo.month_of`, `build_report` | — |

Duraciones válidas: `^\s*(\d+\s*h)?\s*(\d+\s*m)?\s*$`, sin distinguir mayúsculas, con al menos una de las dos partes, horas antes que minutos y total mayor a cero. `1h`, `90m`, `1h30m` y `1h 30m` son válidas; `90`, `1.5h`, `30m1h` y `0m` no.

## Proyectos y clientes

| Regla | Dónde | Falla |
|---|---|---|
| Nombres no vacíos (después de `strip()`) | `Tempo` | `InvalidName` |
| Nombres únicos, distinguiendo mayúsculas | base: `UNIQUE` | `NameTaken` |
| El cliente de un proyecto no cambia | base: trigger `project_client_immutable`; la API no ofrece la operación | `IntegrityError` |
| El cliente es opcional | base: `client_id` admite `NULL` | — |
| No se crea un proyecto para un cliente archivado | `Tempo.add_project` | `ClientArchived` |
| Archivar un cliente con proyectos activos requiere `cascade=True` | `Tempo.archive_client` | `ClientHasActiveProjects` |
| Desarchivar un proyecto de un cliente archivado requiere `with_client=True` | `Tempo.unarchive_project` | `ClientArchived` |
| Desarchivar un cliente no reactiva sus proyectos | `Tempo.unarchive_client` | — |
| Renombrar está permitido aunque haya meses exportados | — | — |

## Meses exportados

| Regla | Dónde | Falla |
|---|---|---|
| Solo se exportan meses cerrados | `Tempo.month_report` | `MonthNotClosed` |
| No se exporta un mes con una sesión abierta que empezó en él | `Tempo.month_report` | `OpenSessionInMonth` |
| No se exporta un mes vacío | `Tempo.month_report` | `EmptyMonth` |
| Un reporte guardado bloquea el mes | `exports` + `Tempo.export_month` | — |
| Con el mes bloqueado no se edita ni se borra una sesión que empezó en él | `Tempo.edit_session`, `Tempo.delete_session` | `MonthLocked` |
| Tampoco se mueve una sesión hacia un mes bloqueado | `Tempo.edit_session` | `MonthLocked` |
| No hay forma de desbloquear un mes | — | — |
| Un reporte guardado se sobrescribe al reexportar | `DiskReportStore`, `overwrite=True` | — |
| Un export con `--project` nunca pisa un archivo | `DiskReportStore`, modo `"x"` | `FileExists` |

## Avisos

| Regla | Dónde |
|---|---|
| Un solo proceso de aviso a la vez | `ProcessSupervisor`: `flock` + identidad `(PID, inicio)` de `/proc/<pid>/stat` |
| El vencimiento se avisa una vez, y solo si el proceso lo vio venir | `Notifier` (estado armado en memoria) |
| La pausa larga (1 h) se avisa una vez por pausa, también entre reinicios | `Notifier` + `pauses.reminded` |
| `TEMPO_NO_NOTIFY=1` desactiva las notificaciones | `build_alerter` |

## Opciones de la CLI

Validadas en `validate_report_args` (salida con código 2):

- `--month` exige `--csv` o `--md`, que son excluyentes; `--csv`/`--md` sin `--month` es un error.
- `-o` solo junto con `--month` y `--project`.
- `--month` no se combina con `--days`, `--from` ni `--to`.
- `--days` no se combina con `--from`/`--to`; `--to` exige `--from`.
- `--days` y `--limit` deben ser enteros positivos.
