"""Import into the user's two master source libraries, then compile one IntLib."""
import datetime, json, pathlib, re, shutil, struct, uuid, zlib
import bridge

def components(streams):
    return [(key, list(bridge.records(data))) for key, data in streams.items() if len(key) == 2 and key[1] == 'Data' and any(p.get('RECORD') == '1' for _, _, p in bridge.records(data))]

def footprints(streams):
    return {bridge.fields(data).get('PATTERN', key[0]): key[0] for key, data in streams.items() if len(key) == 2 and key[1] == 'Parameters'}

def pins_and_geometry(rows):
    start = next((i for i, (_, _, p) in enumerate(rows) if p.get('RECORD') in bridge.MODEL_RECORDS), len(rows))
    if any(p.get('RECORD') not in bridge.MODEL_RECORDS for _, _, p in rows[start:]): raise ValueError('Component model records interleave with symbol graphics')
    geometry = rows[:start]
    pins = [value for flag, payload, p in geometry if (value := bridge.pin_number(flag, payload, p)) is not None]
    if any(not value for value in pins): raise ValueError('A schematic pin has no number')
    return pins, geometry

def bind(rows, footprint):
    pins, geometry = pins_and_geometry(rows)
    data = b''.join(bridge.native_pin(p) if p.get('RECORD') == '2' else bridge.block(payload, flag) for flag, payload, p in geometry)
    count = len(geometry)
    old_model = next((p for _, _, p in rows if p.get('RECORD') == '45' and p.get('ISCURRENT', '').upper() in ('T', 'TRUE')), {})
    data += bridge.params({'RECORD': 44, 'OWNERINDEX': 0, 'INDEXINSHEET': -1})
    model = {'RECORD': 45, 'OWNERINDEX': count, 'INDEXINSHEET': -1, 'DESCRIPTION': old_model.get('DESCRIPTION', 'PCB footprint'), 'MODELNAME': footprint, 'MODELTYPE': 'PCBLIB', 'DATAFILECOUNT': 1, 'MODELDATAFILE0': 'MyFootprints.PcbLib', 'MODELDATAFILEENTITY0': footprint, 'MODELDATAFILEKIND0': 'PCBLib', 'ISCURRENT': 'T', 'UNIQUEID': old_model.get('UNIQUEID', bridge.uid())}
    if '%UTF8%DESCRIPTION' in old_model: model['%UTF8%DESCRIPTION'] = old_model['%UTF8%DESCRIPTION']
    data += bridge.params(model)
    data += bridge.params({'RECORD': 46, 'OWNERINDEX': count + 1, 'INDEXINSHEET': -1})
    for number in dict.fromkeys(pins): data += bridge.params({'RECORD': 47, 'OWNERINDEX': count + 2, 'INDEXINSHEET': -1, 'DESINTF': number, 'DESIMPCOUNT': 1, 'DESIMP0': number, 'ISTRIVIAL': 'T', 'UNIQUEID': bridge.uid()})
    data += bridge.params({'RECORD': 48, 'OWNERINDEX': count + 1, 'INDEXINSHEET': -1})
    return data

def native_empty_placeholder(sch, pcb):
    patterns = footprints(pcb)
    if patterns and native_empty_footprint(pcb) is None: return None
    if pcb.get(('Library', 'Models', 'Data'), b'') or pcb.get(('Library', 'Models', 'Header'), b'\0' * 4) != b'\0' * 4: return None
    parts = components(sch)
    if len(parts) != 1: return None
    key, rows = parts[0]
    if key[0].upper() != 'COMPONENT_1' or {k for k in sch if k[0] == key[0]} != {key}: return None
    if [p.get('RECORD') for _, _, p in rows] != ['1', '34', '41', '44'] or any(flag for flag, _, _ in rows): return None
    header, designator, comment, models = [p for _, _, p in rows]
    if header.get('LIBREFERENCE', '').upper() != 'COMPONENT_1' or header.get('COMPONENTDESCRIPTION', '') or header.get('PARTCOUNT') != '2': return None
    if designator.get('NAME') != 'Designator' or designator.get('TEXT') != '*' or comment.get('NAME') != 'Comment' or comment.get('TEXT') != '*': return None
    if models != {'RECORD': '44'}: return None
    return key


def native_empty_footprint(pcb):
    patterns = footprints(pcb)
    if len(patterns) != 1: return None
    name, key = next(iter(patterns.items()))
    if name.upper() != 'PCBCOMPONENT_1' or list(bridge.footprint_primitives(pcb[(key, 'Data')])): return None
    return key

def validate(sch, pcb):
    if native_empty_placeholder(sch, pcb): return []
    patterns = footprints(pcb)
    report = []
    for key, rows in components(sch):
        bridge.check_symbol_units(rows)
        name = rows[0][2].get('LIBREFERENCE', key[0])
        current = [p for _, _, p in rows if p.get('RECORD') == '45' and p.get('MODELTYPE', '').upper() == 'PCBLIB' and p.get('ISCURRENT', '').upper() in ('T', 'TRUE')]
        if len(current) != 1: raise ValueError(f'{name} must have exactly one default PCB footprint')
        model = current[0]
        footprint = model['MODELNAME']
        if footprint not in patterns: raise ValueError(f'{name} references a footprint absent from MyFootprints.PcbLib: {footprint}')
        pins, _ = pins_and_geometry(rows)
        primitives = list(bridge.footprint_primitives(pcb[(patterns[footprint], 'Data')]))
        pads = [payload[1:1 + payload[0]].decode('latin1') for kind, payload in primitives if kind == 2 and payload[0]]
        bridge.check_mapping(pins, pads)
        maps = {p['DESINTF']: p['DESIMP0'] for _, _, p in rows if p.get('RECORD') == '47'}
        models, bodies = bridge.inspect_models(pcb, primitives, True)
        used = {body['model_id'] for body in bodies}
        report.append({'name': name, 'storage': key[0], 'footprint': footprint, 'model_file': model.get('MODELDATAFILE0', ''), 'pins': len(pins), 'pads': len(pads), 'pin_map': maps, 'body_model_ids': sorted(used), 'embedded_step': [m for m in models if m['id'] in used]})
    return report

def rebind_all(sch, pcb):
    for key, rows in components(sch):
        models = [p for _, _, p in rows if p.get('RECORD') == '45' and p.get('MODELTYPE', '').upper() == 'PCBLIB' and p.get('ISCURRENT', '').upper() in ('T', 'TRUE')]
        if len(models) != 1: raise ValueError(f'{key[0]} has an ambiguous or missing default footprint')
        sch[key] = bind(rows, models[0]['MODELNAME'])
    update_sch_index(sch)
    report = validate(sch, pcb)
    for part in report:
        pins, _ = pins_and_geometry(list(bridge.records(sch[(part['storage'], 'Data')])))
        if part['model_file'] != 'MyFootprints.PcbLib' or part['pin_map'] != {number: number for number in set(pins)}: raise ValueError('Master-library binding validation failed')
    return report

def update_sch_index(sch):
    header = sch[('FileHeader',)]
    size = struct.unpack_from('<I', header)[0] & 0xffffff
    fields = {k: v for k, v in bridge.fields(header[4:4 + size]).items() if not re.fullmatch(r'(?:LIBREF|PARTCOUNT|COMPDESCR|%UTF8%COMPDESCR)\d+', k)}
    parts = components(sch)
    fields['COMPCOUNT'] = len(parts)
    for index, (key, rows) in enumerate(parts):
        p = rows[0][2]
        fields['LIBREF' + str(index)] = p.get('LIBREFERENCE', key[0])
        fields['PARTCOUNT' + str(index)] = p.get('PARTCOUNT', '2')
        for source, target in (('COMPONENTDESCRIPTION', 'COMPDESCR'), ('%UTF8%COMPONENTDESCRIPTION', '%UTF8%COMPDESCR')):
            if source in p: fields[target + str(index)] = p[source]
    sch[('FileHeader',)] = bridge.params(fields)
    bridge.update_sch_weight(sch)

def string_block(value):
    data = value.encode('latin1')
    if len(data) > 255: raise ValueError('PCB footprint name is too long')
    return bridge.block(bytes([len(data)]) + data)

def update_pcb_index(pcb):
    patterns = footprints(pcb)
    data = pcb[('Library', 'Data')]
    size = (struct.unpack_from('<I', data)[0] & 0xffffff) + 4
    pcb[('Library', 'Data')] = data[:size] + struct.pack('<I', len(patterns)) + b''.join(string_block(name) for name in patterns)
    aliases = [(name, key) for name, key in patterns.items() if name != key or len(name) >= 31]
    pcb[('SectionKeys',)] = struct.pack('<I', len(aliases)) + b''.join(string_block(name) + string_block(key) for name, key in aliases)
    toc = []
    for name, key in patterns.items():
        pads = sum(kind == 2 for kind, _ in bridge.footprint_primitives(pcb[(key, 'Data')]))
        toc.append(f'Name={name}|Pad Count={pads}|Height=0|Description=\r\n')
    pcb[('Library', 'ComponentParamsTOC', 'Header')] = struct.pack('<I', 1)
    pcb[('Library', 'ComponentParamsTOC', 'Data')] = bridge.block(''.join(toc).encode('latin1'))

def add_footprint(pcb, incoming, code, desired):
    patterns = footprints(pcb)
    if len(footprints(incoming)) != 1: raise ValueError('Incoming PcbLib must contain one footprint')
    source_name, source_key = next(iter(footprints(incoming).items()))
    name = desired
    if name in patterns:
        if identical_footprint(pcb, patterns[name], incoming, source_key): return name
        name = desired + '_' + code
    if name in patterns:
        if identical_footprint(pcb, patterns[name], incoming, source_key): return name
        digest = bridge.sha(incoming[(source_key, 'Data')] + incoming.get(('Library', 'Models', '0'), b''))[:8]
        name = desired + '_' + code + '_' + digest
        if name in patterns:
            if identical_footprint(pcb, patterns[name], incoming, source_key): return name
            raise ValueError('An incompatible footprint with this LCSC code already exists')
    target_key = name if len(name) <= 31 and name not in {key[0] for key in pcb} else 'LCSC_' + code
    if target_key in {key[0] for key in pcb}: target_key = 'LCSC_' + code + '_' + bridge.sha(name.encode('latin1'))[:8]
    if target_key in {key[0] for key in pcb}: raise ValueError('PCB storage-name collision')
    models = list(bridge.records(incoming[('Library', 'Models', 'Data')]))
    existing_models = list(bridge.records(pcb[('Library', 'Models', 'Data')]))
    replacements = {}
    for index, (flag, payload, p) in enumerate(models):
        old = p['ID']
        new = '{' + str(uuid.uuid4()).upper() + '}'
        replacements[old.encode('latin1')] = new.encode('latin1')
        p['ID'] = new
        pcb[('Library', 'Models', str(len(existing_models) + index))] = incoming[('Library', 'Models', str(index))]
        pcb[('Library', 'Models', 'Data')] += bridge.params(p)
    pcb[('Library', 'Models', 'Header')] = struct.pack('<I', len(existing_models) + len(models))
    for key, data in incoming.items():
        if key[0] != source_key: continue
        if key[1:] == ('Data',):
            size = (struct.unpack_from('<I', data)[0] & 0xffffff) + 4
            data = string_block(name) + data[size:]
            for old, new in replacements.items(): data = data.replace(b'|MODELID=' + old, b'|MODELID=' + new)
        elif key[1:] == ('Parameters',):
            values = bridge.fields(data)
            values['PATTERN'] = name
            values.pop('UNICODE__PATTERN', None)
            data = bridge.params(values)
        pcb[(target_key,) + key[1:]] = data
    update_pcb_index(pcb)
    return name


def identical_footprint(pcb, key, incoming, source_key):
    if pcb[(key, 'Data')] != incoming[(source_key, 'Data')]: return False
    signatures = []
    for streams, storage in ((pcb, key), (incoming, source_key)):
        models, bodies = bridge.inspect_models(streams, list(bridge.footprint_primitives(streams[(storage, 'Data')])), False)
        used = {body['model_id'] for body in bodies}
        metadata = {p['ID']: p for _, _, p in bridge.records(streams.get(('Library', 'Models', 'Data'), b''))}
        signatures.append({m['id']: (m['step_sha256'], tuple(metadata[m['id']].get(field, '0') for field in ('ROTX', 'ROTY', 'ROTZ', 'DZ'))) for m in models if m['id'] in used})
    return signatures[0] == signatures[1]

def add_symbol(sch, incoming, name, footprint):
    parts = components(incoming)
    if len(parts) != 1: raise ValueError('Incoming SchLib must contain one symbol')
    key, rows = parts[0]
    if len(name) > 31: raise ValueError('Schematic Design Item ID exceeds the 31-character native storage limit')
    target = name
    if target in {key[0] for key in sch}: raise ValueError('Schematic storage-name collision')
    import schematic_text
    mapping = schematic_text.merge_fonts(sch, incoming)
    sch[(target, 'Data')] = bind(schematic_text.remap_rows(rows, mapping), footprint)
    for source, data in incoming.items():
        if source[0] == key[0] and source[1:] != ('Data',): sch[(target,) + source[1:]] = schematic_text.remap_pin_text(data, mapping) if source[1:] == ('PinTextData',) else data
    update_sch_index(sch)


def refresh_existing(sch, pcb, key, rows, footprint, report):
    import component_geometry
    placement = report.get('model_placement')
    if not placement: raise ValueError('Existing component repair needs verified source 3D placement')
    pattern = footprints(pcb).get(footprint)
    if not pattern: raise ValueError('Existing symbol has no matching master footprint')
    footprint_data = pcb[(pattern, 'Data')]
    matches = [(index, p) for index, (_, _, p) in enumerate(bridge.records(pcb[('Library', 'Models', 'Data')])) if p.get('ID', '').encode('latin1') in footprint_data]
    if len(matches) != 1: raise ValueError('Existing footprint does not have exactly one model')
    index, model = matches[0]
    current = bridge.sha(zlib.decompress(pcb[('Library', 'Models', str(index))]))
    if current not in (placement['source_step_sha256'], placement['normalized_step_sha256']): raise ValueError('Existing STEP differs from the downloaded source; automatic repair refused')
    incoming = bridge.read_streams(report['pcblib'])
    normalized = incoming[('Library', 'Models', '0')]
    if bridge.sha(zlib.decompress(normalized)) != placement['normalized_step_sha256']: raise ValueError('Normalized STEP checksum changed')
    if current != placement['normalized_step_sha256']:
        new_model_id = '{' + str(uuid.uuid4()).upper() + '}'
        records = list(bridge.records(pcb[('Library', 'Models', 'Data')]))
        shared = any(storage != pattern and model['ID'].encode('latin1') in pcb[(storage, 'Data')] for storage in footprints(pcb).values())
        target_index = len(records) if shared else index
        pcb[('Library', 'Models', str(target_index))] = normalized
        data = bytearray()
        for position, (flag, payload, values) in enumerate(records):
            if position == index and not shared:
                values = values.copy()
                values['ID'] = new_model_id
                for field in ('ROTX', 'ROTY', 'ROTZ', 'DZ', 'CHECKSUM'): values[field] = '0'
                data += bridge.params(values)
            else: data += bridge.block(payload, flag)
        if shared:
            values = model.copy(); values['ID'] = new_model_id
            for field in ('ROTX', 'ROTY', 'ROTZ', 'DZ', 'CHECKSUM'): values[field] = '0'
            data += bridge.params(values)
            pcb[('Library', 'Models', 'Header')] = struct.pack('<I', len(records) + 1)
        pcb[('Library', 'Models', 'Data')] = bytes(data)
        pcb[(pattern, 'Data')], _ = component_geometry.replace_primitives(footprint_data, {model['ID']})
        pcb[(pattern, 'Data')] = pcb[(pattern, 'Data')].replace(b'|MODELID=' + model['ID'].encode('latin1'), b'|MODELID=' + new_model_id.encode('latin1'))
    import symbol_parts
    rows, units = symbol_parts.restore(rows, pathlib.Path(report['source_schlib']).parent)
    updated = bytearray()
    for flag, payload, values in rows:
        if flag and payload[:4] == struct.pack('<i', 2): payload = bridge.simplify_native_pin(payload)
        updated += bridge.block(payload, flag)
    sch[key] = bytes(updated)
    incoming_sch = bridge.read_streams(report['schlib'])
    source_key, source_rows = components(incoming_sch)[0]
    source_text = incoming_sch.get((source_key[0], 'PinTextData'))
    if source_text is not None and (key[0], 'PinTextData') not in sch:
        if pins_and_geometry(rows)[0] != pins_and_geometry(source_rows)[0]: raise ValueError('Source pin order changed; automatic text repair refused')
        import schematic_text
        mapping = schematic_text.merge_fonts(sch, incoming_sch)
        sch[(key[0], 'PinTextData')] = schematic_text.remap_pin_text(source_text, mapping)
    return current != placement['normalized_step_sha256']

def clean_project(project):
    from library_import import project_documents
    text, docs = project_documents(project)
    remove = []
    for section, path in docs:
        if path.is_file(): continue
        if '_Imported' not in path.parts: raise FileNotFoundError(f'Project source is missing: {path}')
        remove.append(str(path.resolve()))
        text = re.sub(r'^\[' + re.escape(section) + r'\]\s*\n.*?(?=^\[|\Z)', '', text, flags=re.M | re.S)
    for _, path in docs:
        if path.is_file() and path.suffix.lower() in ('.schlib', '.pcblib') and path.name not in ('MyParts.SchLib', 'MyFootprints.PcbLib'): raise ValueError('Save or consolidate other existing source libraries before importing into the master libraries')
    return text, remove

def commit(root, sch, pcb, project_text, original, report):
    stamp = datetime.datetime.now().strftime('%Y%m%d_%H%M%S') + '_' + uuid.uuid4().hex[:8]
    backup = root / '_SourceBackup' / ('MasterLibrary_' + stamp)
    backup.mkdir(parents=True, exist_ok=False)
    compiled = root / 'Compiled/MyLibrary.IntLib'
    for name in original: shutil.copy2(root / name, backup / name)
    if compiled.exists(): shutil.copy2(compiled, backup / 'MyLibrary.IntLib')
    (backup / 'operation.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    staged = {}
    for name, streams in (('MyParts.SchLib', sch), ('MyFootprints.PcbLib', pcb)):
        staged[name] = root / (name + '.' + stamp + '.tmp')
        bridge.write_verified(staged[name], streams)
    staged['MyLibrary.LibPkg'] = root / ('MyLibrary.LibPkg.' + stamp + '.tmp')
    staged['MyLibrary.LibPkg'].write_text(project_text, encoding='utf-8-sig')
    if any((root / name).read_bytes() != data for name, data in original.items()): raise RuntimeError('Master source library changed during import; retry after saving it in AD')
    completed = []
    try:
        for name, path in staged.items():
            path.replace(root / name)
            completed.append(name)
    except Exception:
        for name in completed:
            recovery = root / (name + '.' + stamp + '.rollback')
            recovery.write_bytes(original[name])
            recovery.replace(root / name)
        raise
    report['backup'] = str(backup)
    (root / 'master-binding-report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    return report

def import_master(report, library_root):
    from library_import import save_pending
    root = pathlib.Path(library_root).resolve()
    original = {name: (root / name).read_bytes() for name in ('MyParts.SchLib', 'MyFootprints.PcbLib', 'MyLibrary.LibPkg')}
    sch, pcb = bridge.read_streams(root / 'MyParts.SchLib'), bridge.read_streams(root / 'MyFootprints.PcbLib')
    project_text, remove = clean_project(root / 'MyLibrary.LibPkg')
    placeholder = native_empty_placeholder(sch, pcb)
    if placeholder:
        sch.pop(placeholder)
        empty_footprint = native_empty_footprint(pcb)
        if empty_footprint:
            for key in list(pcb):
                if key[0] == empty_footprint: del pcb[key]
            update_pcb_index(pcb)
        update_sch_index(sch)
    before = components(sch)
    rebind_all(sch, pcb)
    code, symbol, footprint = '', '', ''
    added, repaired = False, False
    if report:
        code = bridge.normalize_code(report['lcsc'])
        matched = []
        for key, rows in before:
            values = '\n'.join(value for _, _, p in rows for value in p.values())
            parameter_codes = [p.get('TEXT', '') for _, _, p in rows if p.get('RECORD') == '41' and p.get('NAME', '').upper() == 'LCSC']
            if code in parameter_codes or re.search(r'\bLCSC\s+' + code + r'\b', values): matched.append((key, rows))
        if len(matched) > 1: raise ValueError('Multiple master-library symbols use this LCSC code')
        if matched:
            key, rows = matched[0]
            symbol = rows[0][2].get('LIBREFERENCE', key[0])
            footprint = next(p['MODELNAME'] for _, _, p in rows if p.get('RECORD') == '45' and p.get('ISCURRENT', '').upper() in ('T', 'TRUE'))
            shared = sum(any(p.get('RECORD') == '45' and p.get('MODELNAME') == footprint and p.get('ISCURRENT', '').upper() in ('T', 'TRUE') for _, _, p in part_rows) for _, part_rows in before) > 1
            if report.get('manual_step_override') or (shared and report.get('model_placement')):
                footprint = add_footprint(pcb, bridge.read_streams(report['pcblib']), code, report['footprint'])
                sch[key] = bind(rows, footprint)
                rows = list(bridge.records(sch[key]))
            if report.get('model_placement'): repaired = refresh_existing(sch, pcb, key, rows, footprint, report)
        else:
            symbol = report['symbol']
            if symbol in {rows[0][2].get('LIBREFERENCE', key[0]) for key, rows in before}: raise ValueError('A different component uses this Design Item ID; overwrite refused')
            footprint = add_footprint(pcb, bridge.read_streams(report['pcblib']), code, report['footprint'])
            add_symbol(sch, bridge.read_streams(report['schlib']), symbol, footprint)
            added = True
    bindings = rebind_all(sch, pcb)
    if not bindings: raise ValueError('The master library is empty; import a component before rebuilding')
    first = bindings[0]
    result = {**(report or {}), 'lcsc': code, 'symbol': symbol or first['name'], 'footprint': footprint or first['footprint'], 'project': str(root / 'MyLibrary.LibPkg'), 'intlib': str(root / 'Compiled/MyLibrary.IntLib'), 'schlib': str(root / 'MyParts.SchLib'), 'pcblib': str(root / 'MyFootprints.PcbLib'), 'expected_components': len(bindings), 'new_part': added, 'repaired_existing_model': repaired, 'source_add_sch': str(root / 'MyParts.SchLib'), 'source_add_pcb': str(root / 'MyFootprints.PcbLib'), 'remove_documents': remove, 'bindings': bindings, 'message': 'Master schematic and PCB libraries are linked; native AD compile is pending'}
    commit(root, sch, pcb, project_text, original, result)
    return save_pending(root, result)
