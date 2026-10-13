"""Private native-library setup stays usable when the project is relocated."""
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts import linux_runtime, prepare_linux_libs


def test_private_loader_environment_preserves_existing_paths(tmp_path):
    (tmp_path / '.runtime/lib').mkdir(parents=True)
    original = {'LD_LIBRARY_PATH': '/existing/lib', 'KEEP': 'yes'}
    env = linux_runtime.library_environment(tmp_path, original)
    assert env['LD_LIBRARY_PATH'] == str(tmp_path / '.runtime/lib') + ':/existing/lib'
    assert env['KEEP'] == 'yes' and original['LD_LIBRARY_PATH'] == '/existing/lib'


def test_library_validation_runs_in_new_process_with_private_search_path(tmp_path, monkeypatch):
    (tmp_path / '.runtime/lib').mkdir(parents=True)
    calls = []
    monkeypatch.setattr(linux_runtime.subprocess, 'run',
                        lambda *args, **kwargs: calls.append((args, kwargs)) or SimpleNamespace(returncode=0))
    library = tmp_path / '.runtime/lib/libmpv.so.1'
    linux_runtime.check_shared_library(library, tmp_path)
    args, kwargs = calls[0]
    assert args[0][-1] == str(library)
    assert kwargs['env']['LD_LIBRARY_PATH'].split(':')[0] == str(library.parent)
    assert kwargs['timeout'] == 15


def test_reinstall_repairs_absolute_alias_without_downloading(tmp_path, monkeypatch):
    library_dir = tmp_path / '.runtime/lib'
    library_dir.mkdir(parents=True)
    (library_dir / 'libmpv.so.2').write_bytes(b'fake existing native library')
    alias = library_dir / 'libmpv.so.1'
    alias.symlink_to('/previous/project/.runtime/lib/libmpv.so.2')
    monkeypatch.setattr(prepare_linux_libs, 'ROOT', tmp_path)
    checked = []
    monkeypatch.setattr(prepare_linux_libs, 'check_shared_library', lambda *args: checked.append(args))
    monkeypatch.setattr(prepare_linux_libs.subprocess, 'run', lambda *a, **kw: pytest.fail('Existing libraries were downloaded again'))
    prepare_linux_libs.prepare(download=True)
    assert alias.readlink() == Path('libmpv.so.2') and alias.is_file()
    assert checked == [(alias, tmp_path)]


def test_native_loader_failure_is_reported_without_success(tmp_path, monkeypatch):
    monkeypatch.setattr(linux_runtime.subprocess, 'run',
                        lambda *args, **kwargs: SimpleNamespace(returncode=1, stderr='Traceback\nOSError: missing native dependency\n'))
    with pytest.raises(RuntimeError, match='missing native dependency'):
        linux_runtime.check_shared_library(tmp_path / 'libmpv.so.1', tmp_path)
