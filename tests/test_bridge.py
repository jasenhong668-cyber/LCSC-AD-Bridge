import pathlib, struct, sys, zlib
import pytest
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import bridge

ROOT = pathlib.Path(__file__).resolve().parents[1]
SOURCE = ROOT / 'qa' / 'live-C8734'

@pytest.fixture
def libraries():
    return next(SOURCE.rglob('*.SchLib')), next(SOURCE.rglob('*.PcbLib'))

def test_real_component_preserves_pcb_and_embedded_model(libraries, tmp_path):
    sch, pcb = libraries
    original = bridge.read_streams(pcb)
    result = bridge.prepare(sch, pcb, tmp_path / 'bound', 'C8734')
    assert result['unique_pin_count'] == result['unique_pad_count'] == 48
    assert result['pin_map'] == {str(i): str(i) for i in range(1, 49)}
    converted = bridge.read_streams(result['pcblib'])
    footprint_key = next(key[0] for key in original if len(key) == 2 and key[1] == 'Parameters')
    changed = {('Library', 'Models', '0'), ('Library', 'Models', 'Data'), (footprint_key, 'Data')}
    assert all(converted[key] == data for key, data in original.items() if key not in changed)
    assert [(kind, payload) for kind, payload in bridge.footprint_primitives(converted[(footprint_key, 'Data')]) if kind != 12] == [(kind, payload) for kind, payload in bridge.footprint_primitives(original[(footprint_key, 'Data')]) if kind != 12]
    data = bridge.read_streams(result['schlib'])
    rows = [row for key, stream in data.items() if len(key) == 2 and key[1] == 'Data' for row in bridge.records(stream)]
    model_index, model = next((i, p) for i, (_, _, p) in enumerate(rows) if p.get('RECORD') == '45')
    assert model['OWNERINDEX'] == str(model_index - 1)
    assert model['MODELDATAFILE0'] == 'Footprint.PcbLib'
    assert model['MODELDATAFILEENTITY0'] == result['footprint']
    assert model['ISCURRENT'] == 'T'
    maps = [p for _, _, p in rows if p.get('RECORD') == '47']
    assert all(p['OWNERINDEX'] == str(model_index + 1) for p in maps)
    assert {p['DESINTF']: p['DESIMP0'] for p in maps} == result['pin_map']
    external_step = next(SOURCE.rglob('*.step')).read_bytes()
    assert result['model_placement']['source_step_sha256'] == bridge.sha(external_step)
    assert result['embedded_models'][0]['step_sha256'] == result['model_placement']['normalized_step_sha256']
    assert result['native_ad_compile_verified'] is False

def test_incomplete_mapping_and_exposed_pad_are_rejected():
    with pytest.raises(ValueError, match='unmapped numbered pads'): bridge.check_mapping(['1', '2'], ['1', '2', '3'])
    with pytest.raises(ValueError, match='missing pads'): bridge.check_mapping(['1', '2', '3'], ['1', '2'])
    bridge.check_mapping(['1', '2', '3'], ['1', '2', '3'])
    bridge.check_mapping(['1', '1', '2'], ['1', '2', '2'])

def test_ascii_pins_are_normalized_for_native_ad_placement(libraries, tmp_path):
    sch, pcb = libraries
    result = bridge.prepare(sch, pcb, tmp_path / 'native', 'C8734')
    streams = bridge.read_streams(result['schlib'])
    data = next(data for key, data in streams.items() if len(key) == 2 and key[1] == 'Data')
    rows = list(bridge.records(data))
    pins = [(flag, payload, p) for flag, payload, p in rows if bridge.pin_number(flag, payload, p) is not None]
    assert len(pins) == 48 and all(flag == 1 for flag, _, _ in pins)
    size = struct.unpack_from('<I', streams[('FileHeader',)])[0] & 0xffffff
    assert bridge.fields(streams[('FileHeader',)][4:4 + size])['WEIGHT'] == str(len(rows) + 1)
    assert len(streams[('FileHeader',)]) == size + 4
    original = next(data for key, data in bridge.read_streams(sch).items() if len(key) == 2 and key[1] == 'Data')
    before = [p for _, _, p in bridge.records(original) if p.get('RECORD') == '2']
    for old, (_, payload, _) in zip(before, pins):
        offset = 13 + payload[12]
        assert struct.unpack_from('<hhhI', payload, offset + 3) == (int(old['PINLENGTH']), int(old['LOCATION.X']), int(old['LOCATION.Y']), int(old['COLOR']))
        assert bridge.pin_number(1, payload, {}) == old['DESIGNATOR']

def test_missing_3d_rejected_before_output_created(libraries, tmp_path):
    sch, pcb = libraries
    streams = bridge.read_streams(pcb)
    for key in list(streams):
        if key[:2] == ('Library', 'Models') and key[-1] not in ('Header', 'Data'): del streams[key]
    streams[('Library', 'Models', 'Header')] = struct.pack('<I', 0)
    streams[('Library', 'Models', 'Data')] = b''
    damaged = tmp_path / 'missing.PcbLib'
    bridge.write_verified(damaged, streams)
    with pytest.raises(ValueError, match='missing model'): bridge.prepare(sch, damaged, tmp_path / 'out')
    assert not (tmp_path / 'out').exists()

def test_invalid_step_is_rejected(libraries, tmp_path):
    sch, pcb = libraries
    streams = bridge.read_streams(pcb)
    streams[('Library', 'Models', '0')] = zlib.compress(b'not a STEP model')
    damaged = tmp_path / 'invalid-step.PcbLib'
    bridge.write_verified(damaged, streams)
    with pytest.raises(ValueError, match='complete STEP'): bridge.prepare(sch, damaged, tmp_path / 'out')

def test_missing_model_count_rejected(libraries, tmp_path):
    sch, pcb = libraries
    streams = bridge.read_streams(pcb)
    streams[('Library', 'Models', 'Header')] = struct.pack('<I', 2)
    damaged = tmp_path / 'bad-count.PcbLib'
    bridge.write_verified(damaged, streams)
    with pytest.raises(ValueError, match='count is inconsistent'): bridge.prepare(sch, damaged, tmp_path / 'out')

def test_truncated_records_are_rejected():
    with pytest.raises(ValueError, match='header'): list(bridge.records(b'\x01'))
    with pytest.raises(ValueError, match='Truncated'): list(bridge.records(struct.pack('<I', 100) + b'abc'))
    with pytest.raises(ValueError, match='Truncated'): list(bridge.footprint_primitives(struct.pack('<I', 100) + b'abc'))

@pytest.mark.parametrize('code', ['C8734;whoami', 'C8734 & x', '../C8734', 'C0', 'C', '1234', 'C1\nC2'])
def test_unsafe_or_ambiguous_codes_rejected(code):
    with pytest.raises(ValueError): bridge.normalize_code(code)

def test_existing_output_is_not_overwritten(libraries, tmp_path):
    sch, pcb = libraries
    target = tmp_path / 'out'
    target.mkdir()
    (target / 'Part.SchLib').write_bytes(b'existing user library')
    with pytest.raises(FileExistsError): bridge.prepare(sch, pcb, target)
    assert (target / 'Part.SchLib').read_bytes() == b'existing user library'

def test_script_generates_correct_paths_and_escapes_apostrophes(tmp_path):
    directory = tmp_path / "User's library"
    result = bridge.install_script(directory)
    script = (directory / 'LCSCBridge.pas').read_text(encoding='utf-8-sig')
    assert "User''s library" in script
    assert '@@' not in script
    assert result['script_project'].endswith('MyLibraryMaster.PrjScr')
    assert 'Project.DM_Compile' in script and 'CreateIntegratedLibrary(' not in script

def test_direct_menu_script_uses_system_ansi_for_chinese_paths(tmp_path, monkeypatch):
    monkeypatch.setattr(bridge, 'ad_script_encoding', lambda: 'cp936')
    directory, library = tmp_path / '中文脚本', tmp_path / '中文元件库'
    result = bridge.install_script(directory, library)
    data = (directory / 'MyLibraryMaster.pas').read_bytes()
    assert not data.startswith(b'\xef\xbb\xbf')
    assert str(library) in data.decode('cp936') and str(directory) in data.decode('cp936')
    assert result['script_encoding'] == 'cp936'
    assert 'Cannot find the launcher:' in data.decode('cp936')

def test_script_rejects_paths_outside_system_code_page(tmp_path, monkeypatch):
    monkeypatch.setattr(bridge, 'ad_script_encoding', lambda: 'cp1252')
    with pytest.raises(ValueError, match='AD 脚本编码'): bridge.install_script(tmp_path / '中文脚本')
    assert not list((tmp_path / '中文脚本').glob('*.pas'))

def test_repeated_setup_preserves_loaded_script_and_ad_project_metadata(tmp_path):
    import os
    directory = tmp_path / 'AD'
    bridge.install_script(directory)
    script, project = directory / 'MyLibraryMaster.pas', directory / 'MyLibraryMaster.PrjScr'
    saved_project = project.read_text(encoding='utf-8-sig') + '\n[Generic_ScriptingSystem]\nStartProcName=MyLibraryMaster.pas>ImportToMyLibrary\n'
    project.write_text(saved_project, encoding='utf-8-sig')
    for path in (script, project): os.utime(path, ns=(1000000000000000000, 1000000000000000000))
    before = {path: (path.read_bytes(), path.stat().st_mtime_ns) for path in (script, project)}
    bridge.install_script(directory)
    assert before == {path: (path.read_bytes(), path.stat().st_mtime_ns) for path in (script, project)}

@pytest.mark.parametrize('encoding', ['utf-8-sig', 'utf-16', 'gbk'])
def test_ad_request_encoding_with_chinese_paths(tmp_path, encoding):
    path = tmp_path / 'request.ini'
    path.write_bytes('code=C8734\njob_id=ABCDEFGH\nexample=测试目录\n'.encode(encoding))
    assert bridge.read_request(path) == {'code': 'C8734', 'job_id': 'ABCDEFGH', 'example': '测试目录'}

def test_async_job_claims_request_and_isolates_response(tmp_path, monkeypatch):
    script = tmp_path / 'AD'
    script.mkdir()
    config = tmp_path / 'launcher-config.json'
    config.write_text(__import__('json').dumps({'script_root': str(script), 'backend_root': str(ROOT), 'cache': str(tmp_path / 'cache')}), encoding='utf-8')
    (script / 'job_ABCDEFGH.ini').write_text('code=C8734\njob_id=ABCDEFGH\n', encoding='utf-16')
    expected = {'lcsc': 'C8734', 'symbol': 'STM32F103C8T6', 'footprint': 'LQFP48', 'project': 'C8734.LibPkg', 'intlib': 'C8734.IntLib', 'schlib': 'Part.SchLib', 'pcblib': 'Footprint.PcbLib'}
    monkeypatch.setattr(bridge, 'ROOT', ROOT)
    monkeypatch.setattr(bridge, 'fetch', lambda code, cache, **kwargs: expected)
    assert bridge.run_ad_job(config) == 0
    result = bridge.read_request(script / 'response_ABCDEFGH.ini')
    assert result['status'] == 'ok' and result['job_id'] == 'ABCDEFGH'
    assert result['lcsc'] == 'C8734'
    assert not (script / 'job_ABCDEFGH.ini').exists()
    assert len(list(script.glob('*.claimed'))) == 1
    assert not (script / 'response_OTHER.ini').exists()
    progress = bridge.read_request(script / 'progress_ABCDEFGH.ini')
    assert progress['job_id'] == result['job_id'] and progress['percent'] == '85'
    assert 'AD native compile' in progress['stage']

def test_async_failure_produces_matching_error_response(tmp_path, monkeypatch):
    script = tmp_path / 'AD'
    script.mkdir()
    config = tmp_path / 'config.json'
    config.write_text(__import__('json').dumps({'script_root': str(script), 'backend_root': str(ROOT), 'cache': str(tmp_path / 'cache')}), encoding='utf-8')
    (script / 'job_ERRORJOB.ini').write_text('code=C8734\njob_id=ERRORJOB\n', encoding='utf-8')
    monkeypatch.setattr(bridge, 'ROOT', ROOT)
    def failed(code, cache, **kwargs): raise ValueError('Missing embedded STEP model')
    monkeypatch.setattr(bridge, 'fetch', failed)
    assert bridge.run_ad_job(config) == 1
    result = bridge.read_request(script / 'response_ERRORJOB.ini')
    assert result['status'] == 'error' and result['job_id'] == 'ERRORJOB'
    assert result['error'] == 'Missing embedded STEP model'
    progress = bridge.read_request(script / 'progress_ERRORJOB.ini')
    assert progress['job_id'] == result['job_id'] and progress['percent'] == '0'

def test_cancelled_download_does_not_modify_personal_library(tmp_path, monkeypatch):
    import json, library_import
    script = tmp_path / 'AD'
    script.mkdir()
    config = tmp_path / 'config.json'
    config.write_text(json.dumps({'script_root': str(script), 'backend_root': str(ROOT), 'cache': str(tmp_path / 'cache'), 'my_library': str(tmp_path / 'MyLibrary')}), encoding='utf-8')
    (script / 'job_CANCELJOB.ini').write_text('code=C8734\njob_id=CANCELJOB\nmode=my_library\n', encoding='utf-8')
    (script / 'cancel_CANCELJOB.ini').write_text('cancelled=True\n', encoding='utf-8')
    monkeypatch.setattr(bridge, 'ROOT', ROOT)
    monkeypatch.setattr(bridge, 'fetch', lambda code, cache, **kwargs: {})
    def unexpected_append(report, root): raise AssertionError('A cancelled request must not append a source library')
    monkeypatch.setattr(library_import, 'append_to_library', unexpected_append)
    assert bridge.run_ad_job(config) == 1
    result = bridge.read_request(script / 'response_CANCELJOB.ini')
    assert result['status'] == 'error' and 'cancelled' in result['error']

def test_download_timeout_does_not_publish_cache_or_source_library(tmp_path, monkeypatch):
    import subprocess
    backend = tmp_path / 'vendor/lceda/lceda.exe'
    backend.parent.mkdir(parents=True)
    backend.write_bytes(b'test-backend')
    monkeypatch.setattr(bridge, 'ROOT', tmp_path)
    def timed_out(*args, **kwargs): raise subprocess.TimeoutExpired(args[0], 240)
    monkeypatch.setattr(bridge.subprocess, 'run', timed_out)
    stages = []
    with pytest.raises(RuntimeError, match='timed out after 240 seconds'): bridge.fetch('C7420348', tmp_path / 'cache', progress=lambda stage, percent: stages.append((stage, percent)))
    assert stages == [('Downloading symbol, footprint and STEP', 10)]
    assert not (tmp_path / 'cache/source/C7420348.json').exists()
    assert not list((tmp_path / 'cache/source').glob('*/download-complete.json'))

def test_explicit_job_does_not_claim_an_older_request(tmp_path, monkeypatch):
    import json, component_browser
    script = tmp_path / 'AD'
    script.mkdir()
    config = tmp_path / 'config.json'
    config.write_text(json.dumps({'script_root': str(script), 'backend_root': str(ROOT)}), encoding='utf-8')
    for name in ('OLDJOB', 'NEWJOB'): (script / ('job_' + name + '.ini')).write_text('mode=browser\njob_id=' + name + '\n', encoding='utf-8')
    monkeypatch.setattr(component_browser, 'run_browser', lambda config, request, reply: 0 if request['job_id'] == 'NEWJOB' and reply.name == 'response_NEWJOB.ini' else 1)
    assert bridge.run_ad_job(config, 'NEWJOB') == 0
    assert (script / 'job_OLDJOB.ini').exists() and not (script / 'job_NEWJOB.ini').exists()
