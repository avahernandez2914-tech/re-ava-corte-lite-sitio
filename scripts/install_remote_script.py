"""Copy the AvaLiveMCP Remote Script into Ableton's User Library.

    python scripts/install_remote_script.py            # auto-detect (macOS / Windows)
    python scripts/install_remote_script.py --dest "D:/Ableton/User Library/Remote Scripts"
"""
import argparse
import shutil
import sys
from pathlib import Path

SOURCE = Path(__file__).resolve().parent.parent / "remote_script" / "AvaLiveMCP"


def default_dest() -> Path:
    home = Path.home()
    base = home / "Music" if sys.platform == "darwin" else home / "Documents"
    return base / "Ableton" / "User Library" / "Remote Scripts"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dest", type=Path, default=None, help="Carpeta 'Remote Scripts' de tu User Library")
    args = parser.parse_args()
    dest_root = args.dest or default_dest()
    if not dest_root.parent.exists():
        sys.exit(f"No encuentro {dest_root.parent}. Indica la ruta de tu User Library con --dest "
                 "(Live > Preferencias > Library muestra dónde está).")
    dest_root.mkdir(exist_ok=True)
    target = dest_root / "AvaLiveMCP"
    if target.exists():
        shutil.rmtree(target)
    shutil.copytree(SOURCE, target, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    print(f"Instalado en: {target}")
    print("Ahora reinicia Live y en Preferencias > Link, Tempo & MIDI elige 'AvaLiveMCP' como Control Surface.")


if __name__ == "__main__":
    main()
