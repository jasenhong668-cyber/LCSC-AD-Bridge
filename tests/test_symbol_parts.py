import json, struct, zlib
import pytest
import bridge, main_library, symbol_parts


def source(tmp_path):
    rows = [['PART', 'MCU.1', {}], ['RECT', 'a', -70, 135, 70, -134], ['PIN', 'p1', 1, None, -90, 125, 20, 0], ['ATTR', 'n1', 'p1', 'NUMBER', '10'], ['PART', 'MCU.2', {}], ['RECT', 'b', -40, 30, 40, -30], ['PIN', 'p2', 1, None, 60, 10, 20, 180], ['ATTR', 'n2', 'p2', 'NUMBER', '23']]
    (tmp_path / 'sample_symbol_easyeda.json').write_text(json.dumps({'result': {'dataStr': '\n'.join(json.dumps(row) for row in rows)}}), encoding='utf-8')
    values = [{'RECORD': '1', 'PARTCOUNT': '2'}, {'RECORD': '2', 'DESIGNATOR': '10', 'OWNERPARTID': '1', 'LOCATION.X': '-70', 'LOCATION.Y': '125'}, {'RECORD': '2', 'DESIGNATOR': '23', 'OWNERPARTID': '1', 'LOCATION.X': '40', 'LOCATION.Y': '10'}, {'RECORD': '14', 'OWNERPARTID': '1', 'LOCATION.X': '-70', 'LOCATION.Y': '135', 'CORNER.X': '70', 'CORNER.Y': '-134'}, {'RECORD': '14', 'OWNERPARTID': '1', 'LOCATION.X': '-40', 'LOCATION.Y': '30', 'CORNER.X': '40', 'CORNER.Y': '-30'}]
    return list(bridge.records(b''.join(bridge.params(p) for p in values)))


def test_flat_source_restores_unit_owners_without_moving_pins(tmp_path):
    rows = source(tmp_path)
    restored, report = symbol_parts.restore(rows, tmp_path)
    assert report['count'] == 2 and report['pin_counts'] == {1: 1, 2: 1}
    assert [p.get('OWNERPARTID') for _, _, p in restored[1:]] == ['1', '2', '1', '2']
    assert restored[0][2]['PARTCOUNT'] == '3'
    assert [(p['LOCATION.X'], p['LOCATION.Y'], p['DESIGNATOR']) for _, _, p in restored if p.get('RECORD') == '2'] == [(p['LOCATION.X'], p['LOCATION.Y'], p['DESIGNATOR']) for _, _, p in rows if p.get('RECORD') == '2']


def test_existing_native_symbol_can_be_repaired_and_repeated(tmp_path):
    rows = source(tmp_path)
    rows = [next(bridge.records(bridge.native_pin(p))) if p.get('RECORD') == '2' else row for row in rows for p in [row[2]]]
    restored, _ = symbol_parts.restore(rows, tmp_path)
    assert [struct.unpack_from('<h', payload, 5)[0] for flag, payload, _ in restored if flag] == [1, 2]
    assert symbol_parts.restore(restored, tmp_path)[0] == restored


def test_edited_pin_geometry_refuses_source_unit_assignment(tmp_path):
    rows = source(tmp_path)
    rows[1][2]['LOCATION.X'] = '-71'
    with pytest.raises(ValueError, match='coordinates changed'): symbol_parts.restore(rows, tmp_path)


def model_library(step):
    model = bridge.params({'ID': 'same-id', 'EMBED': 'TRUE'})
    body = b'|MODELID=same-id|'
    data = main_library.string_block('QFP') + bytes([12]) + bridge.block(body)
    return {('QFP', 'Data'): data, ('Library', 'Models', 'Data'): model, ('Library', 'Models', 'Header'): struct.pack('<I', 1), ('Library', 'Models', '0'): zlib.compress(b'ISO-10303-21;\n' + step + b'\nEND-ISO-10303-21;')}


def test_identical_footprint_bytes_cannot_reuse_a_different_step():
    first, second = model_library(b'model-a'), model_library(b'model-b')
    assert first[('QFP', 'Data')] == second[('QFP', 'Data')]
    assert not main_library.identical_footprint(first, 'QFP', second, 'QFP')
    assert main_library.identical_footprint(first, 'QFP', first, 'QFP')


def test_duplicate_model_ids_are_rejected_before_binding():
    pcb = model_library(b'model-a')
    pcb[('Library', 'Models', 'Header')] = struct.pack('<I', 2)
    pcb[('Library', 'Models', 'Data')] *= 2
    pcb[('Library', 'Models', '1')] = pcb[('Library', 'Models', '0')]
    with pytest.raises(ValueError, match='Duplicate embedded model IDs'): bridge.inspect_models(pcb, [], False)


def test_out_of_range_unit_ownership_is_rejected():
    rows = list(bridge.records(bridge.params({'RECORD': '1', 'PARTCOUNT': '2'}) + bridge.native_pin({'DESIGNATOR': '1', 'OWNERPARTID': '2'})))
    with pytest.raises(ValueError, match='missing unit'): bridge.check_symbol_units(rows)


def test_step_replacement_validation_keeps_original_report(tmp_path):
    import component_browser
    path = tmp_path / 'invalid.step'; path.write_bytes(b'wrong model')
    report = {'lcsc': 'C123'}
    with pytest.raises(ValueError, match='STEP'): component_browser.replace_step(report, path, tmp_path)
    assert report == {'lcsc': 'C123'} and not (tmp_path / 'overrides').exists()


def test_native_default_empty_libraries_are_recognized_without_removing_user_graphics():
    rows = [{'RECORD':'1','LIBREFERENCE':'COMPONENT_1','PARTCOUNT':'2'}, {'RECORD':'34','NAME':'Designator','TEXT':'*'}, {'RECORD':'41','NAME':'Comment','TEXT':'*'}, {'RECORD':'44'}]
    sch = {('COMPONENT_1','Data'): b''.join(bridge.params(p) for p in rows)}
    pcb = {('PCBComponent_1','Parameters'): bridge.params({'PATTERN':'PCBComponent_1'}), ('PCBComponent_1','Data'): main_library.string_block('PCBComponent_1')}
    assert main_library.native_empty_placeholder(sch,pcb) == ('COMPONENT_1','Data')
    pcb[('PCBComponent_1','Data')] += bytes([4]) + bridge.block(b'track')
    assert main_library.native_empty_placeholder(sch,pcb) is None
