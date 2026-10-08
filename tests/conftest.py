"""Public checkout: identify native fixtures that are intentionally not distributed."""
from pathlib import Path
import pytest

ROOT = Path(__file__).resolve().parents[1]

def pytest_collection_modifyitems(items):
    for item in items:
        module = item.path.name
        name = item.originalname or item.name
        missing = False
        if module == 'test_bridge.py' and 'libraries' in item.fixturenames:
            missing = not (ROOT / 'qa/live-C8734').exists()
        if module == 'test_library_import.py':
            missing = not (ROOT / 'qa/MyLibrary-baseline').exists()
        if module == 'test_geometry.py' and 'prepared' in item.fixturenames:
            missing = not (ROOT / 'tests/fixtures/C7365889').exists()
        if module == 'test_previews.py' and name != 'test_keyword_result_keeps_exact_lcsc_code_and_3d_availability':
            missing = not (ROOT / 'qa/PreviewBrowserSource').exists()
        if module == 'test_portable_setup.py':
            if name.startswith('test_first_') or name == 'test_native_placeholder_detection_never_removes_user_work':
                missing = not (ROOT / 'qa/NativeEmptyReference').exists() or not (ROOT / 'tests/fixtures/C7365889').exists()
            if name == 'test_failed_install_restores_menu_registry_and_new_files' and getattr(item, 'callspec', None) and item.callspec.params.get('stage') == 'registry':
                missing = not (ROOT / 'assets/empty-library/Compiled/MyLibrary.IntLib').exists()
        if missing:
            item.add_marker(pytest.mark.skip(reason='Requires private AD native fixtures; see docs/DEVELOPMENT.md'))
