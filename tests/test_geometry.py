import json, pathlib, shutil, struct, sys, zlib
import pytest
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import bridge, geometry_worker, main_library, schematic_text

ROOT = pathlib.Path(__file__).resolve().parents[1]
SOURCE = ROOT / 'tests/fixtures/C7365889'


@pytest.fixture(scope='module')
def prepared(tmp_path_factory):
    folder = tmp_path_factory.mktemp('source-placement')
    sch = next(path for path in SOURCE.rglob('*.SchLib') if path.with_suffix('.step').exists())
    return bridge.prepare(sch, sch.with_suffix('.PcbLib'), folder / 'incoming', 'C7365889', True, True)


def test_source_transform_centers_rotates_and_offsets_in_millimeters():
    bounds = [-1, -3, -2, 5, 1, 4]
    matrix, offset, report = geometry_worker.source_matrix(bounds, [6 / .0254, 4 / .0254, 0, 90, 0, 0, 1 / .0254, 2 / .0254, -2 / .0254])
    center = geometry_worker.apply(matrix, [2, -1, -2])
    assert [center[i] + offset[i] for i in range(3)] == pytest.approx([1, 2, -2])
    point = geometry_worker.apply(matrix, [5, -1, -2])
    assert [point[i] + offset[i] for i in range(3)] == pytest.approx([1, 5, -2])
    assert report['scale_xyz'] == pytest.approx([1, 1, 1])


def test_real_connector_preserves_pads_and_pin_locations_and_fixes_labels(prepared):
    old_pcb, new_pcb = bridge.read_streams(prepared['source_pcblib']), bridge.read_streams(prepared['pcblib'])
    key = next(iter(main_library.footprints(new_pcb).values()))
    before = list(bridge.footprint_primitives(old_pcb[(key, 'Data')]))
    after = list(bridge.footprint_primitives(new_pcb[(key, 'Data')]))
    assert [(k, p) for k, p in before if k != 12] == [(k, p) for k, p in after if k != 12]
    assert all(float(value.rstrip('mil')) == 0 for body in prepared['bodies'] for value in body['transform'].values())
    placement = prepared['model_placement']
    assert placement['translation_mm'] == pytest.approx([-2.53999492, 1.27, .000006])
    assert placement['source_solid_count'] == 3 and placement['preview_colors'] >= 3
    old_rows = main_library.components(bridge.read_streams(prepared['source_schlib']))[0][1]
    new_sch = bridge.read_streams(prepared['schlib'])
    new_key, new_rows = main_library.components(new_sch)[0]
    source_pins = [p for _, _, p in old_rows if p.get('RECORD') == '2']
    pins = [p for f, p, v in new_rows if bridge.pin_number(f, p, v) is not None]
    assert len(pins) == len(source_pins) == 6
    for old, payload in zip(source_pins, pins):
        position = 13 + payload[12]
        assert not (payload[position + 2] & 8) and payload[position + 2] & 16
        assert struct.unpack_from('<hhh', payload, position + 3) == (int(old['PINLENGTH']), int(old['LOCATION.X']), int(old['LOCATION.Y']))
    text = new_sch[(new_key[0], 'PinTextData')]
    entries = [p for f, p, _ in bridge.records(text) if f]
    assert len(entries) == 6
    for payload in entries:
        start = 2 + payload[1]
        attributes = zlib.decompress(payload[start + 4:])
        assert struct.unpack('<BhIBhI', attributes) == (16, 2, 0, 16, 2, 0)


def test_independent_download_preparations_produce_same_step(prepared, tmp_path):
    again = bridge.prepare(prepared['source_schlib'], prepared['source_pcblib'], tmp_path / 'again', 'C7365889', True, True)
    assert again['model_placement']['normalized_step_sha256'] == prepared['model_placement']['normalized_step_sha256']


def test_repair_and_repeat_keep_master_counts_and_pads(prepared, tmp_path):
    base = next((ROOT / 'qa/GeneralRepairTrial/_SourceBackup').glob('MasterLibrary_*'))
    target = tmp_path / 'MyLibrary'
    target.mkdir()
    for name in ('MyParts.SchLib', 'MyFootprints.PcbLib', 'MyLibrary.LibPkg'): shutil.copy2(base / name, target / name)
    original = bridge.read_streams(target / 'MyFootprints.PcbLib')
    first = main_library.import_master(prepared, target)
    second = main_library.import_master(prepared, target)
    assert first['repaired_existing_model'] is True and second['repaired_existing_model'] is False
    before_models, _ = bridge.inspect_models(original, list(bridge.footprint_primitives(original[(main_library.footprints(original)[prepared['footprint']], 'Data')])), True)
    original_ids = {model['id'] for model in before_models}
    repaired = next(part for part in first['bindings'] if part['name'] == prepared['symbol'])
    repeated = next(part for part in second['bindings'] if part['name'] == prepared['symbol'])
    assert not set(repaired['body_model_ids']) & original_ids
    assert repaired['body_model_ids'] == repeated['body_model_ids']
    assert first['expected_components'] == second['expected_components'] == 19
    current = bridge.read_streams(second['pcblib'])
    pattern = main_library.footprints(current)[prepared['footprint']]
    assert [(k, p) for k, p in bridge.footprint_primitives(original[(pattern, 'Data')]) if k != 12] == [(k, p) for k, p in bridge.footprint_primitives(current[(pattern, 'Data')]) if k != 12]
    assert len(main_library.footprints(current)) == 19
    sch = bridge.read_streams(second['schlib'])
    rows = next(rows for key, rows in main_library.components(sch) if key[0] == 'TSW-103-23-T-D')
    assert main_library.pins_and_geometry(rows)[0] == ['1', '6', '3', '4', '5', '2']
    text = sch[('TSW-103-23-T-D', 'PinTextData')]
    for flag, payload, _ in bridge.records(text):
        if not flag: continue
        attributes = zlib.decompress(payload[2 + payload[1] + 4:])
        assert struct.unpack_from('<h', attributes, 1)[0] == 1
    assert schematic_text.header(sch[('FileHeader',)])['FONTIDCOUNT'] == '1'


def test_functional_pin_name_remains_visible():
    pin = {'NAME': 'GND', 'DESIGNATOR': '1', 'PINCONGLOMERATE': '24'}
    payload = next(bridge.records(bridge.native_pin(pin)))[1]
    assert payload[15] & 8
