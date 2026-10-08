import json, pathlib, shutil, struct, sys, zlib
import pytest
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import bridge, library_import, main_library

ROOT = pathlib.Path(__file__).resolve().parents[1]
BASE = ROOT / 'qa/MyLibrary-baseline'

@pytest.fixture
def personal_library(tmp_path):
    target = tmp_path / 'MyLibrary'
    target.mkdir()
    for name in ('MyLibrary.LibPkg', 'MyParts.SchLib', 'MyFootprints.PcbLib'): shutil.copy2(BASE / name, target / name)
    return target

@pytest.fixture
def rp_report(tmp_path):
    # Keep the fixture independent of the retired 0.1 release/cache folder.
    source = ROOT / 'qa/RP2040-nativepins'
    return bridge.prepare(source / 'Part.SchLib', source / 'Footprint.PcbLib', tmp_path / 'incoming', 'C2040')

def test_import_enters_both_master_lists_and_preserves_existing_geometry(personal_library, rp_report):
    before_sch = bridge.read_streams(personal_library / 'MyParts.SchLib')
    before_pcb = bridge.read_streams(personal_library / 'MyFootprints.PcbLib')
    result = library_import.append_to_library(rp_report, personal_library)
    assert result['new_part'] is True and result['expected_components'] == 10
    assert pathlib.Path(result['schlib']).name == 'MyParts.SchLib'
    assert pathlib.Path(result['pcblib']).name == 'MyFootprints.PcbLib'
    assert len(result['documents']) == 2 and not (personal_library / '_Imported').exists()
    sch = bridge.read_streams(result['schlib'])
    pcb = bridge.read_streams(result['pcblib'])
    assert len(main_library.components(sch)) == len(main_library.footprints(pcb)) == 10
    for key, old_rows in main_library.components(before_sch):
        _, old_geometry = main_library.pins_and_geometry(old_rows)
        prefix = b''.join(bridge.block(payload, flag) for flag, payload, _ in old_geometry)
        assert sch[key].startswith(prefix)
    for key, data in before_sch.items():
        if len(key) > 1 and key[1] != 'Data': assert sch[key] == data
    for key, data in before_pcb.items():
        if key[0] not in ('Library', 'FileHeader', 'FileVersionInfo', 'SectionKeys'): assert pcb[key] == data
        if key[:2] == ('Library', 'Models') and key[-1].isdigit(): assert pcb[key] == data
    assert {part['model_file'] for part in result['bindings']} == {'MyFootprints.PcbLib'}
    assert all(len(part['pin_map']) == part['pins'] == part['pads'] and part['embedded_step'] for part in result['bindings'])
    model = result['bindings'][-1]['embedded_step'][0]
    assert model['step_sha256'] == rp_report['embedded_models'][0]['step_sha256']
    assert model['id'] != rp_report['embedded_models'][0]['id']
    assert pathlib.Path(result['backup'], 'MyParts.SchLib').read_bytes() == (BASE / 'MyParts.SchLib').read_bytes()

def test_reimport_does_not_duplicate_master_list_entries(personal_library, rp_report):
    first = library_import.append_to_library(rp_report, personal_library)
    second = library_import.append_to_library(rp_report, personal_library)
    assert first['new_part'] is True and second['new_part'] is False
    assert second['expected_components'] == 10
    assert len(main_library.footprints(bridge.read_streams(second['pcblib']))) == 10
    assert len(library_import.catalog(second['project'])) == 10

def test_existing_manual_part_keeps_code_description_and_number_mapping(personal_library, rp_report):
    report = {**rp_report, 'lcsc': 'C6186', 'symbol': 'AMS1117-3.3_C6186', 'model_placement': None}
    first = library_import.append_to_library(report, personal_library)
    second = library_import.append_to_library(report, personal_library)
    assert first['new_part'] is second['new_part'] is False
    assert second['symbol'] == 'AMS1117-3.3' and second['expected_components'] == 9
    parts = library_import.catalog(second['project'])
    assert 'C6186' in next(p['codes'] for p in parts if p['name'] == 'AMS1117-3.3')
    dc = next(p for p in second['bindings'] if p['name'] == 'DC005-2.5')
    assert dc['pin_map'] == {'4': '4', '2': '2', '3': '3'}

def test_name_collision_does_not_write_master_sources(personal_library, rp_report):
    originals = {name: (personal_library / name).read_bytes() for name in ('MyParts.SchLib', 'MyFootprints.PcbLib', 'MyLibrary.LibPkg')}
    with pytest.raises(ValueError, match='overwrite refused'): library_import.append_to_library({**rp_report, 'symbol': 'STM32F103C8T6'}, personal_library)
    assert all((personal_library / name).read_bytes() == data for name, data in originals.items())

def test_missing_old_import_references_are_removed_with_backup(personal_library):
    project = personal_library / 'MyLibrary.LibPkg'
    text = project.read_text(encoding='utf-8-sig') + '\n[Document3]\nDocumentPath=_Imported/COLD/missing.SchLib\n'
    project.write_text(text, encoding='utf-8-sig')
    result = main_library.import_master(None, personal_library)
    assert len(result['remove_documents']) == 1 and len(result['documents']) == 2
    assert '_Imported/COLD' not in project.read_text(encoding='utf-8-sig')
    assert '_Imported/COLD' in pathlib.Path(result['backup'], 'MyLibrary.LibPkg').read_text(encoding='utf-8-sig')

def test_second_file_write_failure_restores_first_source(personal_library, rp_report, monkeypatch):
    originals = {name: (personal_library / name).read_bytes() for name in ('MyParts.SchLib', 'MyFootprints.PcbLib', 'MyLibrary.LibPkg')}
    replace = pathlib.Path.replace
    def injected_failure(source, target):
        if pathlib.Path(target).name == 'MyFootprints.PcbLib': raise OSError('Simulated file lock')
        return replace(source, target)
    monkeypatch.setattr(pathlib.Path, 'replace', injected_failure)
    with pytest.raises(OSError, match='file lock'): library_import.append_to_library(rp_report, personal_library)
    assert all((personal_library / name).read_bytes() == data for name, data in originals.items())
