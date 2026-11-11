# tempo

Gestor de sesiones de trabajo para la terminal. Cronometra sesiones de duración fija asociadas a un proyecto, avisa con una alarma de escritorio que suena hasta 5 minutos cuando se cumple el tiempo y arma reportes mensuales de horas en terminal, CSV o Markdown.

Pensado para uso personal en Linux; las notificaciones están resueltas para WSL (toast de Windows). Solo depende de la biblioteca estándar de Python 3.12.

```
$ tempo start
Elige un proyecto:
  1) web-tienda
  2) app-interna
  3) Crear proyecto
Numero de proyecto o nombre: 1
Duracion (p. ej. 1h, 90m): 1h30m
Trabajo de esta sesion: Checkout con Mercado Pago
Sesion iniciada en web-tienda: duracion 1h30m, vence a las 11:30.

$ tempo status
Proyecto:          web-tienda (Acme SRL)
Estado:            corriendo, vence a las 11:30
Restante:          1h12m
Trabajado:         18m
Trabajo realizado: Checkout con Mercado Pago
```

## Instalación

```
./install.sh
```

Empaqueta el código como un zipapp ejecutable en `~/.local/bin/tempo`. En WSL además registra la aplicación en Windows (`HKCU`, sin permisos de administrador) para que las notificaciones muestren el nombre y el ícono de tempo. `./uninstall.sh` revierte ambas cosas y deja intactos los datos.

Los datos viven en `~/.local/tempo/` (base SQLite, reportes guardados y archivos del proceso de aviso). `TEMPO_HOME` cambia esa ubicación.

## Uso

| Comando | |
|---|---|
| `tempo start` | Inicia una sesión; pregunta proyecto, duración y trabajo de esta sesión |
| `tempo status` | Estado y tiempo restante de la sesión activa |
| `tempo pause` / `resume` | Pausa y reanuda; el tiempo pausado no cuenta |
| `tempo extend 30m` | Suma tiempo, incluso a una sesión vencida |
| `tempo stop` | Cierra la sesión con el trabajo realizado cargado al iniciarla |
| `tempo list` | Sesiones de los últimos 7 días |
| `tempo edit ID` / `delete ID` | Corrige o borra una sesión cerrada |
| `tempo report` | Horas de los últimos 30 días, agrupadas por mes |
| `tempo report --month 10 --csv` | Exporta un mes terminado y lo bloquea |
| `tempo project …` / `client …` | `add`, `list`, `edit`, `archive`, `unarchive` |

La referencia completa está en [docs/cli.md](docs/cli.md).

## Documentación

- [Índice de la documentación, desarrollo y tests](docs/README.md)
- [Contexto y decisiones de diseño](docs/context.md)
- [Arquitectura](docs/arquitectura.md)
- [Sesiones: ciclo de vida, pausas, edición y archivado](docs/sesiones.md)
- [Reportes y exportación](docs/reportes.md)
- [Notificaciones y proceso de aviso](docs/notificaciones.md)
- [Reglas y restricciones](docs/reglas.md)
- [Referencia de la CLI](docs/cli.md)
- APIs por capa: [dominio](docs/api/dominio.md), [persistencia y esquema](docs/api/persistencia.md), [infraestructura](docs/api/infraestructura.md)

## Desarrollo

```
python3 -m venv .venv && .venv/bin/pip install mypy coverage
python3 -m unittest
.venv/bin/mypy --strict tempo tests scripts
```

Después de cambiar el código hay que volver a correr `./install.sh`: el binario instalado es una copia empaquetada.
