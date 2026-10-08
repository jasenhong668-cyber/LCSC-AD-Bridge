import json, pathlib, struct
import bridge, component_browser, preview_render

ROOT = pathlib.Path(__file__).resolve().parents[1]
SOURCE = ROOT / 'qa/PreviewBrowserSource'

def sample(): return json.loads((SOURCE / 'bound/binding-report.json').read_text(encoding='utf-8'))

def test_preview_reads_real_library_and_mesh_without_merging_user_library(monkeypatch, tmp_path):
    report = sample()
    seen = []
    monkeypatch.setattr(bridge, 'fetch', lambda *args, **kwargs: report)
    def forbidden(*args, **kwargs): raise AssertionError('Preview must not modify the master library')
    monkeypatch.setattr(component_browser.main_library, 'import_master', forbidden)
    before = {str(path): bridge.sha(path.read_bytes()) for path in SOURCE.rglob('*') if path.is_file()}
    result = component_browser.prepare_preview('C725895', tmp_path, lambda *values: seen.append(values))
    assert result['mesh'] and len(result['mesh'][0]) == 1970 and len(result['mesh'][1]) == 3927
    assert all(image is not None and len(image.getcolors(480 * 260)) > 3 for image in result['images'])
    assert all(bridge.sha(pathlib.Path(path).read_bytes()) == digest for path, digest in before.items())

def test_obj_rotation_changes_actual_rendered_geometry():
    mesh = preview_render.obj_mesh(next(SOURCE.rglob('*.obj')))
    assert preview_render.model(mesh, angle=32).tobytes() != preview_render.model(mesh, angle=77).tobytes()

def test_keyword_result_keeps_exact_lcsc_code_and_3d_availability(monkeypatch, tmp_path):
    class Output:
        returncode = 0
        stdout = b'Found 2:\n[  1] MODEL-A | C725895 | Maker A | 3D: yes\n[  2] MODEL-B | C8734 | Maker B | 3D: no\n'
        stderr = b''
    def run(command, **kwargs):
        assert command[2] == 'MODEL' and not kwargs.get('shell')
        return Output()
    monkeypatch.setattr(component_browser.subprocess, 'run', run)
    results = component_browser.search('MODEL', tmp_path, ROOT)
    assert [(row['code'], row['has_3d']) for row in results] == [('C725895', True), ('C8734', False)]

def test_search_from_local_cache_does_not_depend_on_network(monkeypatch, tmp_path):
    source = tmp_path / 'cache/source/C725895_sample'
    source.mkdir(parents=True)
    (source / 'download-complete.json').write_text('{}')
    (source.parent / 'C725895.json').write_text(json.dumps({'source': str(source)}))
    import shutil
    shutil.copy2(next(SOURCE.rglob('*.SchLib')), source / 'sample.SchLib')
    def offline(*args, **kwargs): raise AssertionError('The cached exact code should not need network access')
    monkeypatch.setattr(component_browser.subprocess, 'run', offline)
    assert component_browser.search('C725895', tmp_path / 'cache', ROOT)[0]['code'] == 'C725895'
