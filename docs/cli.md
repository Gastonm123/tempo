# Referencia de la CLI

```
tempo <comando> [opciones]
```

Los errores van a stderr con el prefijo `Error: ` y el mensaje de `cli.message()`.

| Código | Significado |
|---|---|
| 0 | Éxito |
| 1 | Falla del dominio (`Err`) o confirmación rechazada |
| 2 | Uso incorrecto: argumentos inválidos o combinación de opciones no permitida |
| 130 | Cancelado con Ctrl+C o Ctrl+D en una pregunta |

## Interacción

- **Listas.** Se elige por número o escribiendo el nombre exacto. Donde se acepta un valor nuevo (proyecto en `start`, cliente al crear un proyecto), cualquier otro texto no numérico se toma como nombre nuevo. Una respuesta inválida repite la pregunta.
- **Confirmaciones.** `s`, `si`, `sí`, `y` o `yes` confirman; `n`, `no` o Enter cancelan.
- **Valores por defecto.** Se muestran entre corchetes; Enter los conserva.
- **Duraciones.** `1h`, `90m`, `1h30m`. Una duración inválida repite la pregunta.
- **Fechas.** `AAAA-MM-DD HH:MM` en `edit`, `AAAA-MM-DD` en `report --from/--to`, siempre en hora local.

## Sesión activa

### `tempo start`

Interactivo, sin argumentos. Falla de entrada si ya hay una sesión activa.

1. **Proyecto**: lista de proyectos activos. Si no hay ninguno, pide el nombre del primero.
   - Un nombre de proyecto archivado ofrece desarchivarlo (y a su cliente, si también lo está).
   - Un nombre nuevo crea el proyecto: pide **Cliente** (`(sin cliente)`, uno existente o uno nuevo) y **Descripción del proyecto** (opcional).
2. **Duración**.
3. **Trabajo a realizar** (opcional).

```
Sesión iniciada en web: 1:30, vence a las 11:30.
```

### `tempo status`

```
Proyecto:   web-tienda (Acme SRL)
Estado:     corriendo, vence a las 11:30
Restante:   1:12
Trabajado:  0:18
Trabajo:    Checkout con Mercado Pago
```

`Estado` puede ser `corriendo, vence a las HH:MM`, `pausada desde las HH:MM (hace H:MM)` o `vencida a las HH:MM`. El restante se redondea hacia arriba al minuto y el trabajado se trunca.

### `tempo pause` · `tempo resume`

Sin argumentos. Fallan si no hay sesión, si ya está en ese estado o, para `pause`, si la sesión venció.

### `tempo extend <duración>`

Suma tiempo en cualquier estado. Sobre una sesión vencida, la reanuda desde ese momento sin contar el hueco.

### `tempo stop`

Pregunta **Trabajo realizado** con la descripción actual como default y cierra la sesión.

```
Sesión cerrada: 2026-11-09 10:00 → 2026-11-09 11:00, trabajado 1:00.
```

## Sesiones pasadas

### `tempo list [--project NOMBRE]... [--days N] [--limit N]`

| Opción | Default | |
|---|---|---|
| `--project` | todos | repetible |
| `--days` | 7 | días hacia atrás desde ahora |
| `--limit` | sin límite | las N más recientes, mostradas en orden cronológico |

Columnas: id, Proyecto, Inicio, Fin (`en curso` si sigue abierta), Trabajado, Descripción (recortada al ancho de la terminal).

### `tempo edit <id>`

Solo sesiones cerradas en meses no exportados. Pregunta Proyecto, Trabajo realizado, Inicio y Fin, cada uno con el valor actual como default.

### `tempo delete <id>`

Muestra la sesión y pide confirmación. Rechaza sesiones de meses exportados.

## Proyectos y clientes

```
tempo project add <nombre>                # pregunta cliente y descripción
tempo project list [--archived]
tempo project rename <nombre> <nuevo>
tempo project archive <nombre>
tempo project unarchive <nombre>          # pregunta si reactivar el cliente, si está archivado

tempo client add <nombre>
tempo client list [--archived]
tempo client rename <nombre> <nuevo>
tempo client archive <nombre>             # si tiene proyectos activos, los lista y pide confirmación
tempo client unarchive <nombre>           # no reactiva los proyectos
```

Los nombres con espacios van entre comillas: `tempo project rename web "web tienda"`.

## Reportes

```
tempo report [--project NOMBRE]... [--days N | --from AAAA-MM-DD [--to AAAA-MM-DD]]
tempo report --month MES (--csv | --md) [--project NOMBRE]... [-o SALIDA]
```

| Opción | |
|---|---|
| `--project` | Filtra; repetible. Al exportar, cambia el modo a export suelto |
| `--days N` | Últimos N días (default 30) |
| `--from` / `--to` | Rango de fechas; `--to` es inclusivo y exige `--from` |
| `--month` | `N` (año actual) o `AAAA-MM`; solo meses cerrados |
| `--csv` / `--md` | Formato de exportación |
| `-o` | Archivo de salida de un export suelto; relativo al directorio actual o absoluto |

Sin `--month` imprime el reporte. Con `--month` solo escribe el archivo e imprime su ruta. Detalle en [reportes.md](reportes.md).

## Interno

`tempo notifier` arranca el proceso de aviso. No aparece en `--help` y no está pensado para ejecutarse a mano; lo lanza el supervisor. Ver [notificaciones.md](notificaciones.md).
