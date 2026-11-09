# Documentación de tempo

| Documento | Contenido |
|---|---|
| [context.md](context.md) | Requisitos de partida, entorno y decisiones de diseño |
| [arquitectura.md](arquitectura.md) | Capas, dependencias, composición, errores como valores |
| [sesiones.md](sesiones.md) | Estados de una sesión, pausa, extensión, cierre, edición, borrado y archivado |
| [reportes.md](reportes.md) | Construcción del reporte, formatos y exportación mensual |
| [notificaciones.md](notificaciones.md) | Proceso de aviso, supervisor, señales y toasts de Windows |
| [reglas.md](reglas.md) | Invariantes y restricciones, dónde se hace cumplir cada una |
| [cli.md](cli.md) | Referencia de comandos, opciones y códigos de salida |
| [api/dominio.md](api/dominio.md) | `Tempo`, modelos y tipos de falla |
| [api/persistencia.md](api/persistencia.md) | `Store`, esquema SQLite y migraciones |
| [api/infraestructura.md](api/infraestructura.md) | `Clock`, `Terminal`, `Supervisor`, `Alerter`, `ReportStore`, `Renderer`, `Config` |

## Desarrollo y tests

```
python3 -m unittest                                   # 74 tests, ~2 s
.venv/bin/mypy --strict tempo tests scripts
.venv/bin/coverage run --branch --source=tempo -m unittest && .venv/bin/coverage report
```

Los tests usan la API de `Tempo` y nunca métodos privados. Corren en el mismo proceso con `SqliteStore(":memory:")`, `FakeClock`, `FakeSupervisor`, `MemoryReportStore` y `DummyTerminal`; el bucle del proceso de aviso se prueba paso a paso con `Notifier.tick()`. Solo `tests/test_notifier_process.py` lanza procesos reales, para cubrir lo que depende del sistema operativo: escritura del PID, relanzamiento tras un `kill` y la recepción de `SIGHUP` durante el arranque.

Criterio para la suite: un test existe si cubre una regla de [reglas.md](reglas.md) o código que ningún otro test ejecuta; no se duplican en la capa de cálculo las reglas ya probadas a través de `Tempo`. La cobertura de ramas ronda el 73%; lo no cubierto es mayormente la capa de CLI (comandos triviales sobre `Tempo`) y las implementaciones que hablan con el sistema (`PosixTerminal`, `WindowsToastAlerter`), verificadas a mano.

`scripts/make_icon.py` genera el ícono (PNG 256×256 con supermuestreo 4×4) sin dependencias; lo usa `install.sh`.
