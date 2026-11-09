#!/usr/bin/env bash
# Desinstala el ejecutable y el registro de notificaciones de Windows.
# No borra los datos (~/.local/tempo): sesiones, proyectos y reportes guardados.
set -euo pipefail

target="${HOME}/.local/bin/tempo"
data="${TEMPO_HOME:-$HOME/.local/tempo}"

if [ -f "$data/notifier.pid" ]; then
  pid="$(cut -d' ' -f1 "$data/notifier.pid")"
  if tr '\0' ' ' < "/proc/$pid/cmdline" 2>/dev/null | grep -q "tempo notifier"; then
    kill "$pid" && echo "Proceso de aviso detenido."
  fi
fi

rm -f "$target" && echo "Borrado: $target"

if command -v powershell.exe >/dev/null 2>&1; then
  win_dir="$(powershell.exe -NoProfile -Command '[Environment]::GetFolderPath("LocalApplicationData")' | tr -d '\r')\\tempo"
  powershell.exe -NoProfile -NonInteractive -Command \
    'Remove-Item -Path "HKCU:\Software\Classes\AppUserModelId\tempo" -Recurse -ErrorAction SilentlyContinue'
  rm -rf "$(wslpath -u "$win_dir")"
  echo "Registro de notificaciones de Windows eliminado."
fi

echo "Tus datos siguen en $data. Borralos a mano si ya no los necesitás."
