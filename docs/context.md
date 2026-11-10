# Contexto de desarrollo

tempo es una herramienta personal para registrar horas de trabajo por proyecto y cliente. Los requisitos de partida fueron: un único ejecutable en el `PATH`, sesiones de duración fija pedida al usuario, pausa, listado de sesiones previas, reporte mensual con inicio, fin y acumulado, consulta del tiempo restante y gestión de varios proyectos, todo por CLI.

El diseño se cerró antes de escribir código y la implementación avanzó en etapas cortas (núcleo de cálculo, persistencia, reportes, CLI, proceso de aviso, instalación), cada una con sus tests y `mypy --strict` en verde. Se desarrolló en pair programming con Claude Code; las decisiones de abajo resumen esas discusiones.

El entorno objetivo es WSL2 (AlmaLinux) con Python 3.12. El supervisor del proceso de aviso depende de Linux (`/proc`, `flock`, `posix_spawn`); el resto del código es portable.

## Decisiones de diseño

**Solo biblioteca estándar.** El programa se instala copiando un zipapp; no hay entorno virtual ni dependencias que mantener. `mypy` y `coverage` viven en `.venv/` y son exclusivamente de desarrollo.

**La base es la única fuente de verdad.** El estado de una sesión se deriva de timestamps guardados (inicio, duración planificada, pausas). No hay un daemon que mantenga estado: el proceso de aviso solo lee la base y notifica. Si muere, no se pierde nada; se relanza con el próximo comando.

**Errores como valores.** Las fallas esperables del dominio se devuelven como `Err(...)` con datos concretos, nunca como excepciones. Las excepciones de la stdlib se traducen a `Err` únicamente en las piezas de borde (SQLite, archivos, procesos). Lo inesperado no se atrapa y termina el programa. Ver [arquitectura.md](arquitectura.md#errores-como-valores).

**Interfaces estructurales, sin herencia de código.** Toda dependencia externa está detrás de un `typing.Protocol` que contiene solo firmas. Las implementaciones lo declaran explícitamente (`class SystemClock(Clock)`), así `mypy` valida el contrato, pero no heredan comportamiento.

**Fechas en UTC dentro de la base.** Se guardan como texto ISO-8601 en UTC: el orden lexicográfico coincide con el cronológico y no depende de cambios de horario. El mes de una sesión y todo lo que se muestra se calcula en hora local.

**Sesiones enteras.** Una sesión nunca se parte. Pertenece al mes (hora local) en que empezó, aunque termine al día o al mes siguiente. El costo es que la hora entre 00:00 y la 01:00 de una sesión que arrancó el 30/09 a las 22:00 cuenta en septiembre; partirla obligaría a mostrar separadores por día para que la salida tenga sentido.

**Meses exportados inmutables.** Exportar un mes bloquea la edición y el borrado de sus sesiones, y la edición de los proyectos y clientes que tienen sesiones en él, de modo que un reporte guardado siempre coincide con la base y regenerarlo produce el mismo contenido. No existe un comando para desbloquear.

**Comentarios y docstrings en ASCII.** Los textos para el usuario mantienen su ortografía; el código no.
