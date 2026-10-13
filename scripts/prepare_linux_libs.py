"""Prepare Flet 0.28's libmpv compatibility alias without editing system libraries.

Flet's Linux client requires libmpv.so.1; current Fedora supplies libmpv.so.2.
The Flet documentation describes the compatibility link. Keep it app-private.
If mpv is absent, download Fedora RPMs and extract shared libraries only.
"""
import argparse
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.linux_runtime import check_shared_library


def prepare(download=False):
    target = ROOT / '.runtime/lib'
    target.mkdir(parents=True, exist_ok=True)
    alias = target / 'libmpv.so.1'
    # Repair old absolute aliases when a project folder has been relocated.
    private_mpv = target / 'libmpv.so.2'
    if alias.is_symlink() and private_mpv.is_file() and alias.readlink() != Path('libmpv.so.2'):
        alias.unlink()
        alias.symlink_to('libmpv.so.2')
    if alias.exists():
        check_shared_library(alias, ROOT)
        print('Flet desktop library is ready.')
        return
    source = next((p for p in (private_mpv, Path('/usr/lib64/libmpv.so.2'), Path('/usr/lib/x86_64-linux-gnu/libmpv.so.2')) if p.is_file()), None)
    if source is None:
        if not download or not shutil.which('dnf'):
            raise SystemExit('Install Fedora mpv-libs or rerun this script with --download on Fedora.')
        for command in ('rpm2cpio','cpio','rpmkeys'):
            if not shutil.which(command):
                raise SystemExit(command + ' is required for the app-private library setup.')
        with tempfile.TemporaryDirectory(prefix='peter-fedora-') as folder:
            temp = Path(folder)
            subprocess.run(['dnf','--disable-repo=*','--enable-repo=fedora','--enable-repo=updates',
                            '--enable-repo=fedora-cisco-openh264','-y','download','--resolve',
                            '--destdir',folder,'mpv-libs.x86_64'], check=True)
            for rpm in temp.glob('*.rpm'):
                if rpm.name.startswith(('python3-','yt-dlp')):
                    continue
                subprocess.run(['rpmkeys','--checksig',str(rpm)],check=True)
                extract = temp / 'extract'
                extract.mkdir(exist_ok=True)
                process = subprocess.Popen(['rpm2cpio',str(rpm)],stdout=subprocess.PIPE)
                subprocess.run(['cpio','-idmu','--quiet'],stdin=process.stdout,cwd=extract,check=True)
                process.stdout.close()
                if process.wait():
                    raise RuntimeError('RPM library extraction failed.')
            for library in (temp/'extract/usr/lib64').glob('*.so*'):
                if library.is_symlink():
                    if not (target/library.name).exists():
                        (target/library.name).symlink_to(library.readlink())
                elif library.is_file():
                    shutil.copy2(library,target/library.name)
        source = target / 'libmpv.so.2'
    if not source.is_file():
        raise SystemExit('Fedora download did not provide libmpv.so.2.')
    alias.unlink(missing_ok=True)
    alias.symlink_to(source.name if source.parent == target else source.resolve())
    # The launcher uses this directory in LD_LIBRARY_PATH for transitive deps.
    check_shared_library(alias, ROOT)
    print('Prepared private Flet compatibility link:', alias)


if __name__ == '__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--download',action='store_true')
    prepare(parser.parse_args().download)
