#!/usr/bin/env bash
# Instala tempo como ~/.local/bin/tempo (un zipapp) y, en WSL, registra
# "tempo" en Windows para que las notificaciones muestren su nombre e icono.
set -euo pipefail

here="$(cd "$(dirname "$0")" && pwd)"
bin_dir="${HOME}/.local/bin"
target="${bin_dir}/tempo"

# Un proceso de aviso de una version anterior sigue corriendo el codigo viejo:
# se detiene y el proximo comando de tempo lanza el nuevo.
data="${TEMPO_HOME:-$HOME/.local/tempo}"
if [ -f "$data/notifier.pid" ]; then
  pid="$(cut -d' ' -f1 "$data/notifier.pid")"
  if tr '\0' ' ' < "/proc/$pid/cmdline" 2>/dev/null | grep -q "tempo notifier"; then
    kill "$pid" && echo "Proceso de aviso anterior detenido."
  fi
fi

build="$(mktemp -d)"
trap 'rm -rf "$build"' EXIT
mkdir -p "$build/tempo"
cp "$here"/tempo/*.py "$build/tempo/"
mkdir -p "$bin_dir"
python3 -m zipapp "$build" -p "/usr/bin/env python3" -m "tempo.cli:entry" -o "$target"
chmod +x "$target"
echo "Instalado: $target"

case ":$PATH:" in
  *":$bin_dir:"*) ;;
  *) echo "Atención: $bin_dir no está en el PATH." ;;
esac

if command -v powershell.exe >/dev/null 2>&1; then
  win_dir="$(powershell.exe -NoProfile -Command '[Environment]::GetFolderPath("LocalApplicationData")' | tr -d '\r')\\tempo"
  icon_dir="$(wslpath -u "$win_dir")"
  mkdir -p "$icon_dir"
  python3 "$here/scripts/make_icon.py" "$icon_dir/tempo.png"
  TEMPO_ICON="$win_dir\\tempo.png" WSLENV="TEMPO_ICON" powershell.exe -NoProfile -NonInteractive -Command '
    $key = "HKCU:\Software\Classes\AppUserModelId\tempo"
    New-Item -Path $key -Force | Out-Null
    New-ItemProperty -Path $key -Name DisplayName -Value "tempo" -PropertyType String -Force | Out-Null
    New-ItemProperty -Path $key -Name IconUri -Value $env:TEMPO_ICON -PropertyType String -Force | Out-Null
  '
  echo "Notificaciones registradas en Windows como \"tempo\" (ícono: $win_dir\\tempo.png)."
fi
