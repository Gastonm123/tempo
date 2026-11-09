# Reportes y exportación

El armado del reporte es puro (`report.build_report`) y está separado del formato (`Renderer`) y del destino (`ReportStore`). `Tempo` combina las tres piezas y aplica las reglas de exportación.

## `Report`

```python
build_report(sessions, *, tz, generated_at, projects) -> Report
```

```
Report
├── months: tuple[MonthGroup, ...]       # en orden cronológico
│   └── MonthGroup(month: 'AAAA-MM', rows: tuple[ReportRow, ...])  · worked_s
├── projects: tuple[str, ...] | None     # None = todos los proyectos
├── summary: tuple[ProjectSummary, ...]  # (project, client, sessions, worked_s), por nombre
└── generated_at: datetime
    · rows (todas las filas) · worked_s (total) · is_empty
```

`ReportRow(session_id, project, client, start, end, worked_s, paused_s, description)`, con `start` y `end` convertidos a `tz`. `duration_s` es la propiedad `end - start`, de modo que `duration_s = worked_s + paused_s` en toda fila.

Reglas de armado:

- Las sesiones abiertas se ignoran. La sesión en curso se consulta con `status`.
- Una sesión pertenece al mes local de su `start` (`start.strftime("%Y-%m")` después de convertir a `tz`) y ocupa una sola fila aunque cruce la medianoche o el fin de mes. Una sesión del 30/09 22:00 al 01/10 01:00 suma sus 3 h en septiembre y no aparece en octubre.
- `summary` se completa solo si hay más de un proyecto con filas; con uno queda vacío.
- `worked_s` de cada fila usa `sessions.worked_seconds(start, pauses, end)`, así que las pausas cuentan como en el resto del sistema, incluido el hueco no contado de un `extend` posterior al vencimiento.

`Tempo.report(start, end, projects=None)` toma las sesiones con `start_at` en `[start, end)` (comparación de texto ISO en UTC, ver [persistencia](api/persistencia.md)) y arma el reporte con `tz` y `generated_at` del reloj.

## Terminal

```
tempo report                                       # últimos 30 días
tempo report --days 90
tempo report --from 2026-08-01 --to 2026-10-31     # --to incluye ese día
tempo report --project web --project app           # combinable con cualquiera de los anteriores
```

| Opciones | Rango `[start, end)` |
|---|---|
| ninguna | `[ahora - 30 d, ahora + 1 d)` |
| `--days N` | `[ahora - N d, ahora + 1 d)`; cuenta desde el instante actual, no desde la medianoche |
| `--from A` | `[A 00:00, ahora + 1 d)` |
| `--from A --to B` | `[A 00:00, B 00:00 + 1 d)` |

`--days` no se combina con `--from`/`--to`, y `--to` exige `--from`.

`TerminalRenderer(width)` imprime, por cada mes, un separador `── Mes AAAA ──`, la tabla de sesiones y `Total del mes`; después el resumen por proyecto (si corresponde) y el total general. Columnas: Inicio, Fin, Proyecto, Trabajado, Pausas, Duración, Trabajo realizado. Los tiempos van en `H:MM` truncado al minuto.

Cada mes calcula sus propios anchos de columna. Con `width`, la última columna (la descripción) se acota a `width - (ancho de las demás + separadores)`, con un mínimo de 10 caracteres, y se corta con `…`. Si el resto de las columnas ya no entra, la línea supera el ancho. `width=None` desactiva el recorte. La CLI pasa `Terminal.width()` (`shutil.get_terminal_size`, 80 columnas si la salida no es una terminal).

## Exportación

```
tempo report --month 10 --csv                       # octubre del año actual
tempo report --month 2025-10 --md
tempo report --month 10 --csv --project web -o web-octubre.csv
```

`--month` exige exactamente uno de `--csv` o `--md` y no se combina con `--days`, `--from` ni `--to`. `-o` solo vale junto con `--project`.

### Validación: `month_report(month, projects)`

Se evalúa en este orden y es común a los dos modos:

| Falla | Condición |
|---|---|
| `MonthNotClosed(month)` | `month >= mes actual` (comparación de texto `AAAA-MM`): el mes actual y los futuros |
| `OpenSessionInMonth(month, id)` | alguna sesión que empezó en el mes (y pertenece a los proyectos filtrados, si los hay) sigue abierta |
| `EmptyMonth(month)` | no hay sesiones en el mes con ese filtro |

Los límites del mes salen de `month_bounds(month, tz)`: el primer instante del mes y del siguiente, en hora local, con el cambio de año resuelto (`2026-12` → `[2026-12-01, 2027-01-01)`).

`parse_month` acepta `N` o `NN` (mes del año en curso, en hora local) y `AAAA-MM`; cualquier otra cosa, o un mes fuera de 1–12, da `InvalidMonth`.

### Modos

| | Reporte guardado: `export_month` | Export suelto: `export_month_filtered` |
|---|---|---|
| Se usa | sin `--project` | con uno o más `--project` |
| Incluye | todos los proyectos | los proyectos indicados |
| Destino | `saved_reports`: `$TEMPO_HOME/reports/tempo-AAAA-MM.<ext>` | `loose_reports`: `./tempo-AAAA-MM.<ext>` o `-o` (relativo al directorio actual o absoluto) |
| Archivo existente | se sobrescribe; si se borró, se recrea | `FileExists(path)`; no se escribe nada |
| Registro en `exports` | una fila por export | ninguno |
| Bloquea el mes | sí | no |
| Salida de la CLI | `Reporte guardado: <ruta absoluta>` | `Archivo creado: <ruta absoluta>` |

En ambos el comando solo escribe el archivo; el reporte no se imprime.

### Reporte guardado y bloqueo

```python
content = render_to_text(RENDERERS[fmt], report)      # fuera de la transacción
with store.transaction():
    store.add_export(month, now)
    saved_reports.save(f"tempo-{month}.{fmt}", content, overwrite=True)
supervisor.sync()
```

El registro y la escritura van en la misma transacción: si `save` lanza una excepción (disco lleno, permisos), el `ROLLBACK` descarta la fila y el mes no queda bloqueado. El archivo, en cambio, puede quedar truncado, porque el modo `"w"` lo vacía al abrirlo; un export posterior lo reescribe. Con `overwrite=True` el store no puede devolver `FileExists`; si lo hiciera, `export_month` lanza `AssertionError` (es un error de programación, no una falla esperable).

Un mes está bloqueado si tiene al menos una fila en `exports`, sin importar el formato ni cuántas veces se exportó. Con el mes bloqueado, `edit_session` y `delete_session` rechazan las sesiones que empiezan en él (ver [sesiones](sesiones.md#sesiones-cerradas)). Como tampoco puede haber sesiones abiertas de ese mes (es un mes cerrado y la validación lo exige), las filas del reporte quedan fijas. Entre dos exports del mismo mes solo pueden cambiar los nombres de proyectos y clientes, que se pueden renombrar, y la línea `Generado` del Markdown.

No hay operación para desbloquear un mes.

### Escritura del archivo

`DiskReportStore.save(name, content, overwrite)` resuelve `directory / name` (si `name` es absoluto, gana `name`), crea los directorios faltantes y abre con `encoding="utf-8"` y `newline=""`, para que el `\r\n` del CSV no se traduzca. Sin `overwrite` abre en modo `"x"`, que falla de forma atómica si el archivo existe; el `FileExistsError` se traduce a `Err(FileExists(path))`.

## CSV

```
id,proyecto,cliente,inicio,fin,horas_trabajadas,horas_pausa,descripcion
2,web-tienda,Acme SRL,2026-10-01 09:00,2026-10-01 11:15,2.25,0.00,Maquetado del carrito
3,app-interna,,2026-10-01 14:00,2026-10-01 16:30,2.50,0.00,Login con OAuth
```

- RFC 4180 con `csv.writer`: separador `,`, comillas solo cuando hacen falta, fin de línea `\r\n`.
- UTF-8 con BOM (`﻿` al inicio), para que Excel detecte la codificación. Google Sheets y LibreOffice lo ignoran; en Python se lee con `encoding="utf-8-sig"`.
- Una fila por sesión. Sin separadores de mes ni filas de total, que se sumarían dos veces en una planilla.
- Fechas `AAAA-MM-DD HH:MM` en hora local. Horas en decimal con dos cifras (`segundos / 3600`, redondeo de `format`), no truncadas al minuto.
- `cliente` vacío si el proyecto no tiene. `horas_pausa` incluye el hueco no contado de un `extend` posterior al vencimiento.

## Markdown

```markdown
# Reporte de horas — Octubre 2026

- **Proyectos:** todos
- **Generado:** 2026-11-09 15:20
- **Total del mes:** 4:45

## Resumen por proyecto

| Proyecto | Cliente | Sesiones | Horas |
|---|---|---:|---:|
| app-interna | — | 1 | 2:30 |
| web-tienda | Acme SRL | 1 | 2:15 |
| **Total** |  | **2** | **4:45** |

## Detalle de sesiones

| Inicio | Fin | Proyecto | Trabajado | Pausas | Duración | Trabajo realizado |
|---|---|---|---:|---:|---:|---|
| 2026-10-01 09:00 | 2026-10-01 11:15 | web-tienda | 2:15 | 0:00 | 2:15 | Maquetado del carrito |
| 2026-10-01 14:00 | 2026-10-01 16:30 | app-interna | 2:30 | 0:00 | 2:30 | Login con OAuth |
```

- `Proyectos`: `todos` en un reporte guardado; con filtro, los nombres separados por coma, o `nombre (cliente)` si es uno solo con cliente.
- `Generado`: el momento del export en hora local. Es lo único que cambia al reexportar un mes sin renombres.
- El resumen aparece solo con más de un proyecto; su fila de total va en negrita.
- Sin ids internos: el archivo está pensado para compartirse.
- En las celdas, `|` se escapa como `\|` y los saltos de línea pasan a espacios.
- El título usa el nombre del mes en castellano. Con más de un mes en el reporte diría `Período`, caso que la exportación nunca produce.
