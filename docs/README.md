# Documentación de tempo

| Documento | Contenido |
|---|---|
| [arquitectura.md](arquitectura.md) | Capas, dependencias, composición, errores como valores |
| [sesiones.md](sesiones.md) | Estados de una sesión, pausa, extensión, cierre, edición, borrado y archivado |
| [reportes.md](reportes.md) | Construcción del reporte, formatos y exportación mensual |
| [notificaciones.md](notificaciones.md) | Proceso de aviso, supervisor, señales y toasts de Windows |
| [reglas.md](reglas.md) | Invariantes y restricciones, dónde se hace cumplir cada una |
| [cli.md](cli.md) | Referencia de comandos, opciones y códigos de salida |
| [api/dominio.md](api/dominio.md) | `Tempo`, modelos y tipos de falla |
| [api/persistencia.md](api/persistencia.md) | `Store`, esquema SQLite y migraciones |
| [api/infraestructura.md](api/infraestructura.md) | `Clock`, `Terminal`, `Supervisor`, `Alerter`, `ReportStore`, `Renderer`, `Config` |

## Contexto

tempo es una herramienta personal para registrar horas de trabajo por proyecto y cliente. Los requisitos de partida fueron: un único ejecutable en el `PATH`, sesiones de duración fija pedida al usuario, pausa, listado de sesiones previas, reporte mensual con inicio, fin y acumulado, consulta del tiempo restante y gestión de varios proyectos, todo por CLI.

El diseño se cerró antes de escribir código y la implementación avanzó en etapas cortas (núcleo de cálculo, persistencia, reportes, CLI, proceso de aviso, instalación), cada una con sus tests y `mypy --strict` en verde. Se desarrolló en pair programming con Claude Code; las decisiones de abajo resumen esas discusiones.

El entorno objetivo es WSL2 (AlmaLinux) con Python 3.12. El supervisor del proceso de aviso depende de Linux (`/proc`, `flock`, `posix_spawn`); el resto del código es portable.

## Decisiones

**Solo biblioteca estándar.** El programa se instala copiando un zipapp; no hay entorno virtual ni dependencias que mantener. `mypy` y `coverage` viven en `.venv/` y son exclusivamente de desarrollo.

**La base es la única fuente de verdad.** El estado de una sesión se deriva de timestamps guardados (inicio, duración planificada, pausas). No hay un daemon que mantenga estado: el proceso de aviso solo lee la base y notifica. Si muere, no se pierde nada; se relanza con el próximo comando.

**Errores como valores.** Las fallas esperables del dominio se devuelven como `Err(...)` con datos concretos, nunca como excepciones. Las excepciones de la stdlib se traducen a `Err` únicamente en las piezas de borde (SQLite, archivos, procesos). Lo inesperado no se atrapa y termina el programa. Ver [arquitectura.md](arquitectura.md#errores-como-valores).

**Interfaces estructurales, sin herencia de código.** Toda dependencia externa está detrás de un `typing.Protocol` que contiene solo firmas. Las implementaciones lo declaran explícitamente (`class SystemClock(Clock)`), así `mypy` valida el contrato, pero no heredan comportamiento.

**Fechas en UTC dentro de la base.** Se guardan como texto ISO-8601 en UTC: el orden lexicográfico coincide con el cronológico y no depende de cambios de horario. El mes de una sesión y todo lo que se muestra se calcula en hora local.

**Sesiones enteras.** Una sesión nunca se parte. Pertenece al mes (hora local) en que empezó, aunque termine al día o al mes siguiente. El costo es que la hora entre 00:00 y la 01:00 de una sesión que arrancó el 30/09 a las 22:00 cuenta en septiembre; partirla obligaría a mostrar separadores por día para que la salida tenga sentido.

**Meses exportados inmutables.** Exportar un mes lo bloquea para edición y borrado, de modo que un reporte guardado siempre coincide con la base y regenerarlo produce el mismo contenido. No existe un comando para desbloquear.

**Comentarios y docstrings en ASCII.** Los textos para el usuario mantienen su ortografía; el código no.

## Desarrollo y tests

```
python3 -m unittest                                   # 74 tests, ~2 s
.venv/bin/mypy --strict tempo tests scripts
.venv/bin/coverage run --branch --source=tempo -m unittest && .venv/bin/coverage report
```

Los tests usan la API de `Tempo` y nunca métodos privados. Corren en el mismo proceso con `SqliteStore(":memory:")`, `FakeClock`, `FakeSupervisor`, `MemoryReportStore` y `DummyTerminal`; el bucle del proceso de aviso se prueba paso a paso con `Notifier.tick()`. Solo `tests/test_notifier_process.py` lanza procesos reales, para cubrir lo que depende del sistema operativo: escritura del PID, relanzamiento tras un `kill` y la recepción de `SIGHUP` durante el arranque.

Criterio para la suite: un test existe si cubre una regla de [reglas.md](reglas.md) o código que ningún otro test ejecuta; no se duplican en la capa de cálculo las reglas ya probadas a través de `Tempo`. La cobertura de ramas ronda el 73%; lo no cubierto es mayormente la capa de CLI (comandos triviales sobre `Tempo`) y las implementaciones que hablan con el sistema (`PosixTerminal`, `WindowsToastAlerter`), verificadas a mano.

`scripts/make_icon.py` genera el ícono (PNG 256×256 con supermuestreo 4×4) sin dependencias; lo usa `install.sh`.
