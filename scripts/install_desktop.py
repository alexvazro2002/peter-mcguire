"""Install a relocatable desktop shortcut pointing at the verified project root."""
from pathlib import Path
import os

ROOT = Path(__file__).resolve().parents[1]


def desktop_entry(root=ROOT):
    # Desktop Entry quoting differs from shell quoting; escape reserved characters.
    def quoted(value):
        return '"' + str(value).replace('\\', '\\\\').replace('"', '\\"').replace('`', '\\`').replace('$', '\\$').replace('%', '%%') + '"'
    return ('[Desktop Entry]\nType=Application\nVersion=1.0\n'
            'Name=Peter (McGuire)\nComment=Your local NexusDesk desktop assistant\n'
            f'Exec=/bin/bash {quoted(root / "start-linux.sh")}\n'
            f'Icon={root / "assets" / "peter.svg"}\nPath={root}\nTerminal=false\n'
            'Categories=Utility;Office;\nStartupNotify=true\n')


if __name__ == '__main__':
    target = Path(os.environ.get('XDG_DATA_HOME', str(Path.home() / '.local/share'))) / 'applications/peter-mcguire.desktop'
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(desktop_entry(), encoding='utf-8')
    target.chmod(0o755)
    desktop = Path.home() / 'Desktop'
    if desktop.is_dir():
        shortcut = desktop / 'Peter.desktop'
        shortcut.write_text(desktop_entry(), encoding='utf-8')
        shortcut.chmod(0o755)
    print('Installed application launcher:', target)
