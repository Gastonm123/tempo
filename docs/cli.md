# Referencia de la CLI

```
tempo <comando> [opciones]
```

Los errores van a stderr con el prefijo `Error: ` y el mensaje de `cli.message()`. Los errores de argumentos los escribe argparse, en español, con el uso del subcomando que falló.

| Código | Significado |
|---|---|
| 0 | Éxito |
| 1 | Falla del dominio (`Err`) o confirmación rechazada |
| 2 | Uso incorrecto: argumentos inválidos o combinación de opciones no permitida |
| 130 | Cancelado con Ctrl+C o Ctrl+D en una pregunta |

## Textos

- Los mensajes, preguntas y la ayuda de la CLI van en ASCII, sin tildes; se conservan la `ñ`, `¿` y `¡`. Los reportes exportados (Markdown, CSV) y las notificaciones de escritorio usan la ortografía normal.
- Un adjetivo sin sustantivo escrito va en masculino (`Estado: pausado`); con sustantivo, concuerda (`Sesion pausada`).
- Las duraciones se muestran como se escriben: `1h30m`, `5m`, `2h`. El Markdown exportado usa `H:MM` y el CSV horas decimales.
- Causa y sugerencia se separan con un punto: `La sesion vencio a las 15:30. Usa ...`.

## Interacción

- **Listas.** Llevan un título (`Elige un proyecto:`) y piden `Numero de proyecto o nombre:`. Se elige por número, por el texto exacto de una opción o por el nombre de un proyecto o cliente existente, aunque esté archivado. Cualquier otra respuesta muestra `Opcion invalida.` y repite la pregunta. Las acciones van como opciones de la lista: `Crear proyecto`, `Crear cliente`, `Omitir` (sin cliente), `Mantener <proyecto>` (en `edit`).
- **Confirmaciones.** `s`, `si`, `sí`, `y` o `yes` confirman; `n`, `no` o Enter cancelan.
- **Valores por defecto.** No se muestran; Enter los conserva. Los comandos que editan lo avisan con `Enter conserva el valor actual.`.
- **Validación.** Cada respuesta se valida al ingresarla: si no sirve, se muestra el motivo y se repite esa pregunta. Ver la regla en [arquitectura.md](arquitectura.md#errores-como-valores).
- **Duraciones.** `1h`, `90m`, `1h30m`.
- **Fechas.** `AAAA-MM-DD HH:MM` en `edit`, `AAAA-MM-DD` en `report --from/--to`, siempre en hora local.

## Sesión activa

### `tempo start`

Interactivo, sin argumentos. Falla de entrada si ya hay una sesión activa.

1. **Proyecto**: lista de proyectos activos y `Crear proyecto`. Si no existe ningún proyecto, pide directamente el nombre del primero.
   - El nombre de un proyecto archivado ofrece desarchivarlo y, si su cliente también está archivado, desarchivar el cliente. Responder que no cancela.
   - `Crear proyecto` pide el nombre (no vacío ni repetido), el **cliente** (`Omitir`, uno existente, uno archivado escrito por nombre o `Crear cliente`) y la **Descripcion del proyecto** (opcional). El cliente nuevo se crea junto con el proyecto, al final.
2. **Duracion**.
3. **Trabajo realizado** (obligatorio).

```
Elige un proyecto:
  1) web
  2) Crear proyecto
Numero de proyecto o nombre: 1
Duracion (p. ej. 1h, 90m): 1h30m
Trabajo realizado: Checkout con Mercado Pago
Sesion iniciada en web: duracion 1h30m, vence a las 11:30.
```

### `tempo status`

```
Proyecto:          web-tienda (Acme SRL)
Estado:            corriendo, vence a las 11:30
Restante:          1h12m
Trabajado:         18m
Trabajo realizado: Checkout con Mercado Pago
```

`Estado` puede ser `corriendo, vence a las HH:MM`, `pausado desde las HH:MM (hace 25m)` o `vencido a las HH:MM (usa ...)`. El restante se redondea hacia arriba al minuto y el trabajado se trunca.

### `tempo pause` · `tempo resume`

Sin argumentos. Fallan si no hay sesión, si ya está en ese estado o, para `pause`, si la sesión venció. Los dos muestran el tiempo restante:

```
Sesion pausada. Restante: 1h12m.
Sesion reanudada. Restante: 1h12m.
```

### `tempo extend <duración>`

Suma tiempo en cualquier estado. Sobre una sesión vencida, la reanuda desde ese momento sin contar el hueco.

```
Se agregaron 30m. Restante: 1h42m.
```

### `tempo stop`

Sin preguntas: cierra la sesión con el trabajo realizado cargado en `start`.

```
Sesion cerrada: 2026-11-09 10:00 -> 2026-11-09 11:00, trabajado 1h.
```

## Sesiones pasadas

### `tempo list [--project NOMBRE]... [--days DIAS] [--limit CANTIDAD]`

| Opción | Default | |
|---|---|---|
| `--project` | todos | repetible |
| `--days` | 7 | días hacia atrás desde ahora |
| `--limit` | sin límite | las N más recientes, mostradas en orden cronológico |

Columnas: Id, Proyecto, Inicio, Fin (`en curso` si sigue abierta), Trabajado, Trabajo realizado (recortado al ancho de la terminal con `...`).

### `tempo edit <id>`

Solo sesiones cerradas en meses no exportados. Muestra la sesión con la misma tabla que `list` y pregunta, en orden:

1. **Proyecto**: la lista de `start` con `Mantener <proyecto actual>` como primera opción.
2. **Trabajo realizado** (obligatorio).
3. **Inicio**: fecha válida y de un mes no exportado.
4. **Fin**: fecha válida y posterior al inicio.

Enter conserva el valor actual de los tres últimos.

### `tempo delete <id>`

Muestra la sesión con la tabla de `list` y pide confirmación. Rechaza sesiones de meses exportados. Las pausas de la sesión se borran con ella.

## Proyectos y clientes

```
tempo project add <nombre>                # pregunta cliente y descripcion
tempo project list [--archived]
tempo project edit [nombre]               # pregunta nombre y descripcion
tempo project archive [nombre]
tempo project unarchive [nombre]          # pregunta si desarchivar el cliente, si esta archivado

tempo client add <nombre>
tempo client list [--archived]
tempo client edit [nombre]                # pregunta nombre
tempo client archive [nombre]             # si tiene proyectos activos, los lista y pide confirmacion
tempo client unarchive [nombre]           # no desarchiva los proyectos
```

Los nombres con espacios van entre comillas: `tempo project edit "web tienda"`.

Sin nombre, `edit` y `archive` muestran la lista de activos y `unarchive` la de archivados; en la lista también se acepta el nombre de cualquier proyecto o cliente. Con nombre, actúan sobre ese, aunque esté archivado. Archivar algo archivado o desarchivar algo activo falla con un error del dominio.

`project add` valida el nombre antes de preguntar el resto.

### `tempo project edit [nombre]` · `tempo client edit [nombre]`

El cliente de un proyecto no se puede cambiar: `project edit` lo muestra como dato.

```
Enter conserva el valor actual.
Cliente: Acme SRL
Nombre: web-tienda
Descripcion del proyecto:
Proyecto 'web-tienda' actualizado.
```

Fallan antes de preguntar si el proyecto, o algún proyecto del cliente (incluidos los archivados), tiene sesiones en un mes exportado. El nombre nuevo se valida al ingresarlo (no vacío ni de otro proyecto o cliente). Como Enter conserva el default, una descripción no se puede dejar vacía.

## Reportes

```
tempo report [--project NOMBRE]... [--days DIAS | --from AAAA-MM-DD [--to AAAA-MM-DD]]
tempo report --month MES (--csv | --md) [--project NOMBRE]... [-o SALIDA]
```

| Opción | |
|---|---|
| `--project` | Filtra; repetible. Al exportar, cambia el modo a export suelto |
| `--days N` | Últimos N días (default 30) |
| `--from` / `--to` | Rango de fechas; `--to` es inclusivo y exige `--from` |
| `--month` | `N` (año actual) o `AAAA-MM`; solo meses terminados |
| `--csv` / `--md` | Formato de exportación |
| `-o` | Archivo de salida de un export suelto; relativo al directorio actual o absoluto |

Sin `--month` imprime el reporte. Con `--month` solo escribe el archivo e imprime su ruta; el export guardado (sin `--project`) avisa además que el mes quedó bloqueado:

```
Reporte guardado en /home/usuario/.local/tempo/reports/tempo-2026-10.md. El mes 2026-10 quedo bloqueado.
Reporte guardado en /home/usuario/web.csv.
```

Detalle en [reportes.md](reportes.md).

## Proceso de aviso

Los avisos los manda un proceso aparte, que el supervisor lanza con `python -m tempo.notifier_main`. No es un comando de la CLI. Ver [notificaciones.md](notificaciones.md).
