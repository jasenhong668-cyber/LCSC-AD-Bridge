"""LCSC -> linked Altium libraries; native IntLib compilation runs inside AD."""
import argparse, datetime, hashlib, json, os, pathlib, re, shutil, struct, subprocess, sys, tempfile, uuid, zlib
import olefile
from cfb_native import write_cfb

VERSION = '0.5.1'
ROOT = pathlib.Path(sys.executable).parent if getattr(sys, 'frozen', False) else pathlib.Path(__file__).resolve().parent
RESOURCES = pathlib.Path(getattr(sys, '_MEIPASS', ROOT))
MODEL_RECORDS = {'44', '45', '46', '47', '48'}

def sha(data): return hashlib.sha256(data).hexdigest()
def uid(): return ''.join(chr(65 + byte % 26) for byte in uuid.uuid4().bytes[:8])
def block(payload, flags=0): return struct.pack('<I', (flags << 24) | len(payload)) + payload
def fields(payload): return {item.split('=', 1)[0].upper(): item.split('=', 1)[1] for item in payload.rstrip(b'\0').decode('latin1').split('|') if '=' in item}
def params(values): return block((''.join('|' + k + '=' + str(v) for k, v in values.items())).encode('latin1') + b'\0')

def records(data):
    pos = 0
    while pos < len(data):
        if pos + 4 > len(data): raise ValueError('Truncated Altium record header')
        word = struct.unpack_from('<I', data, pos)[0]
        size, flag = word & 0xffffff, word >> 24
        end = pos + 4 + size
        if not size or end > len(data): raise ValueError('Truncated or empty Altium record')
        payload = data[pos + 4:end]
        yield flag, payload, fields(payload) if not flag else {}
        pos = end

def read_streams(path):
    with olefile.OleFileIO(str(path)) as doc: return {tuple(s): doc.openstream(s).read() for s in doc.listdir()}

def pin_number(flag, payload, p):
    if p.get('RECORD') == '2': return p.get('DESIGNATOR', '')
    if not flag or len(payload) < 27 or struct.unpack_from('<I', payload)[0] != 2: return None
    pos = 12 + 1 + payload[12] + 3 + 6 + 4
    if pos >= len(payload): raise ValueError('Truncated binary schematic pin')
    pos += 1 + payload[pos]
    if pos >= len(payload) or pos + 1 + payload[pos] > len(payload): raise ValueError('Truncated binary pin designator')
    return payload[pos + 1:pos + 1 + payload[pos]].decode('latin1')

def footprint_primitives(data):
    def take(pos):
        if pos + 4 > len(data): raise ValueError('Truncated PCB record header')
        size = struct.unpack_from('<I', data, pos)[0] & 0xffffff
        end = pos + 4 + size
        if end > len(data): raise ValueError('Truncated PCB primitive')
        return data[pos + 4:end], end
    _, pos = take(0)
    while pos < len(data):
        kind, pos = data[pos], pos + 1
        if kind not in (1, 2, 3, 4, 5, 6, 11, 12): raise ValueError(f'Unsupported PCB primitive type {kind}')
        payload, pos = take(pos)
        yield kind, payload
        for _ in range({2: 5, 5: 1}.get(kind, 0)): _, pos = take(pos)

def native_pin(p):
    """AD 24 SchLib uses binary pins; ASCII pin records can crash placement."""
    def short(value):
        data = value.encode('latin1')
        if len(data) > 255: raise ValueError('Schematic pin text exceeds the native binary limit')
        return bytes([len(data)]) + data
    for key in ('LOCATION.X_FRAC', 'LOCATION.Y_FRAC', 'PINLENGTH_FRAC'):
        if int(p.get(key, '0')): raise ValueError('ASCII pins with fractional coordinates require native export')
    payload = struct.pack('<iBh5B', 2, 0, int(p.get('OWNERPARTID', '1')), int(p.get('OWNERPARTDISPLAYMODE', '0')), int(p.get('SYMBOL_INNEREDGE', '0')), int(p.get('SYMBOL_OUTEREDGE', '0')), int(p.get('SYMBOL_INSIDE', '0')), int(p.get('SYMBOL_OUTSIDE', '0')))
    visible = int(p.get('PINCONGLOMERATE', '24'))
    if p.get('NAME', '') == p.get('DESIGNATOR', '') and p.get('NAME', '').isdigit(): visible &= ~8
    payload += short(p.get('DESCRIPTION', '')) + struct.pack('<BBBhhhI', int(p.get('FORMALTYPE', '0')), int(p.get('ELECTRICAL', '4')), visible, int(p.get('PINLENGTH', '10')), int(p.get('LOCATION.X', '0')), int(p.get('LOCATION.Y', '0')), int(p.get('COLOR', '128')))
    payload += b''.join(short(p.get(key, '')) for key in ('NAME', 'DESIGNATOR', 'SWAPIDGROUP', 'SWAPIDPART', 'DEFAULTVALUE'))
    return block(payload, 1)


def simplify_native_pin(payload):
    if len(payload) < 29 or struct.unpack_from('<i', payload)[0] != 2: return payload
    description_end = 13 + payload[12]
    name_offset = description_end + 13
    if name_offset >= len(payload): return payload
    name_end = name_offset + 1 + payload[name_offset]
    if name_end >= len(payload): return payload
    designator_end = name_end + 1 + payload[name_end]
    if designator_end > len(payload): return payload
    name, designator = payload[name_offset + 1:name_end], payload[name_end + 1:designator_end]
    if not name or name != designator or not name.isdigit(): return payload
    result = bytearray(payload)
    result[description_end + 2] &= ~8
    return bytes(result)

def update_sch_weight(streams):
    header = streams[('FileHeader',)]
    size = struct.unpack_from('<I', header)[0] & 0xffffff
    values = fields(header[4:4 + size])
    values['WEIGHT'] = str(1 + sum(len(list(records(data))) for key, data in streams.items() if len(key) == 2 and key[1] == 'Data' and key[0] not in ('Library', 'Storage')))
    streams[('FileHeader',)] = params(values)

def check_mapping(pins, pads):
    missing = sorted(set(pins) - set(pads))
    extra = sorted(set(pads) - set(pins))
    if not pins or not pads or missing or extra: raise ValueError(f'Pin/pad mapping is incomplete: missing pads={missing}; unmapped numbered pads={extra}')

def check_symbol_units(rows):
    count = int(rows[0][2].get('PARTCOUNT', '2')) - 1
    if count < 1: raise ValueError('Symbol contains no usable unit')
    owners = []
    for flag, payload, values in rows[1:]:
        owner = struct.unpack_from('<h', payload, 5)[0] if flag and pin_number(flag, payload, values) is not None else int(values.get('OWNERPARTID', '-1'))
        if owner < -1 or owner > count: raise ValueError('Symbol primitive references a missing unit')
        if pin_number(flag, payload, values) is not None: owners.append(owner)
    return count, {part: sum(owner in (-1, 0, part) for owner in owners) for part in range(1, count + 1)}

def inspect_models(pcb, primitives, require_3d):
    metadata = list(records(pcb.get(('Library', 'Models', 'Data'), b'')))
    expected = pcb.get(('Library', 'Models', 'Header'), b'\0' * 4)
    if len(expected) != 4 or struct.unpack('<I', expected)[0] != len(metadata): raise ValueError('Embedded model count is inconsistent')
    models = []
    for index, (_, _, p) in enumerate(metadata):
        compressed = pcb.get(('Library', 'Models', str(index)))
        if not compressed or not p.get('ID') or p.get('EMBED', '').upper() not in ('TRUE', 'T'): raise ValueError('Incomplete embedded 3D model')
        inflater = zlib.decompressobj()
        step = inflater.decompress(compressed, 100 * 1024 * 1024)
        if not inflater.eof or inflater.unused_data or inflater.unconsumed_tail: raise ValueError('Invalid or oversized embedded STEP data')
        if not step.lstrip().startswith(b'ISO-10303-21;') or b'END-ISO-10303-21;' not in step: raise ValueError('Embedded model is not a complete STEP file')
        models.append({'id': p['ID'], 'name': p.get('NAME', ''), 'step_bytes': len(step), 'step_sha256': sha(step), 'compressed_sha256': sha(compressed)})
    if len({model['id'] for model in models}) != len(models): raise ValueError('Duplicate embedded model IDs can bind the wrong STEP')
    bodies = []
    for kind, payload in primitives:
        if kind != 12: continue
        match = re.search(rb'\|MODELID=([^|\x00]+)', payload)
        if not match: continue
        model_id = match[1].decode('latin1')
        if model_id not in {model['id'] for model in models}: raise ValueError(f'3D body references a missing model: {model_id}')
        transform = {k.decode('ascii'): v.decode('latin1') for k, v in re.findall(rb'\|(MODEL\.(?:2D\.[A-Z]+|3D\.[A-Z]+))=([^|\x00]+)', payload)}
        bodies.append({'model_id': model_id, 'transform': transform})
    if require_3d and (not models or not bodies): raise ValueError('This component has no bound STEP model; strict 3D mode refuses to import it')
    return models, bodies

def write_verified(path, streams):
    write_cfb(path, streams)
    if read_streams(path) != streams: raise ValueError('Compound-file round trip changed a library stream')

def prepare(sch_path, pcb_path, output, code=None, require_3d=True, require_source_geometry=False, progress=None):
    sch_path, pcb_path, output = pathlib.Path(sch_path).resolve(), pathlib.Path(pcb_path).resolve(), pathlib.Path(output).resolve()
    if output.exists(): raise FileExistsError(output)
    sch, pcb = read_streams(sch_path), read_streams(pcb_path)
    symbols = [(key, list(records(data))) for key, data in sch.items() if len(key) == 2 and key[1] == 'Data' and key[0] not in ('Library', 'Storage')]
    symbols = [(key, rows) for key, rows in symbols if any(p.get('RECORD') == '1' for _, _, p in rows)]
    footprints = [(key[0], fields(data)) for key, data in pcb.items() if len(key) == 2 and key[1] == 'Parameters']
    if len(symbols) != 1 or len(footprints) != 1: raise ValueError('Version 0.1 accepts one component and one footprint per input library')
    key, rows = symbols[0]
    if rows[0][2].get('RECORD') != '1': raise ValueError('Unexpected component owner record')
    symbol = rows[0][2].get('LIBREFERENCE', key[0])
    if any(ch in symbol for ch in '\r\n|'): raise ValueError('Unsupported component name')
    fp_key, fp_params = footprints[0]
    footprint = fp_params.get('PATTERN', fp_key)
    model_start = next((i for i, row in enumerate(rows) if row[2].get('RECORD') in MODEL_RECORDS), len(rows))
    if any(p.get('RECORD') not in MODEL_RECORDS for _, _, p in rows[model_start:]): raise ValueError('Model records interleave with symbol graphics; automatic rebinding refused')
    geometry = rows[:model_start]
    import symbol_parts
    geometry, units = symbol_parts.restore(geometry, sch_path.parent)
    check_symbol_units(geometry)
    rows = geometry + rows[model_start:]
    pins = [number for flag, payload, p in geometry if (number := pin_number(flag, payload, p)) is not None]
    if any(not number for number in pins): raise ValueError('Schematic has an unnamed pin')
    primitives = list(footprint_primitives(pcb[(fp_key, 'Data')]))
    pad_names = []
    for kind, payload in primitives:
        if kind != 2: continue
        if not payload or len(payload) < 1 + payload[0]: raise ValueError('Truncated pad number')
        name = payload[1:1 + payload[0]].decode('latin1')
        if name: pad_names.append(name)
    check_mapping(pins, pad_names)
    models, bodies = inspect_models(pcb, primitives, require_3d)
    source_footprint_sha = sha(pcb[(fp_key, 'Data')])
    output.mkdir(parents=True, exist_ok=False)
    import component_geometry
    if progress: progress('Aligning STEP origin, scale, rotation and height', 50)
    placement = component_geometry.normalize(pcb, fp_key, pcb_path.parent, output)
    if require_source_geometry and models and placement is None: raise ValueError('Source model transform metadata is required for reliable 3D alignment')
    if placement:
        primitives = list(footprint_primitives(pcb[(fp_key, 'Data')]))
        models, bodies = inspect_models(pcb, primitives, require_3d)
        if any(any(float(value.rstrip('mil')) for value in body['transform'].values()) for body in bodies): raise ValueError('Normalized 3D body still has an offset')
    original_geometry = b''.join(block(payload, flag) for flag, payload, _ in geometry)
    import schematic_text
    text_data = schematic_text.pin_text(geometry, schematic_text.header(sch[('FileHeader',)]))
    if text_data is not None and (key[0], 'PinTextData') not in sch: sch[(key[0], 'PinTextData')] = text_data
    data = b''.join(native_pin(p) if p.get('RECORD') == '2' else block(payload, flag) for flag, payload, p in geometry)
    normalized_geometry = data
    if code: data += params({'RECORD': 41, 'OWNERINDEX': 0, 'OWNERPARTID': -1, 'NAME': 'LCSC', 'TEXT': code, 'ISHIDDEN': 'T', 'UNIQUEID': uid()})
    count = len(geometry) + bool(code)
    data += params({'RECORD': 44, 'OWNERINDEX': 0})
    data += params({'RECORD': 45, 'OWNERINDEX': count, 'DESCRIPTION': 'PCB footprint', 'MODELNAME': footprint, 'MODELTYPE': 'PCBLIB', 'DATAFILECOUNT': 1, 'MODELDATAFILE0': 'Footprint.PcbLib', 'MODELDATAFILEENTITY0': footprint, 'MODELDATAFILEKIND0': 'PCBLib', 'ISCURRENT': 'T', 'UNIQUEID': uid()})
    data += params({'RECORD': 46, 'OWNERINDEX': count + 1})
    for pin in dict.fromkeys(pins): data += params({'RECORD': 47, 'OWNERINDEX': count + 2, 'DESINTF': pin, 'DESIMPCOUNT': 1, 'DESIMP0': pin, 'ISTRIVIAL': 'T', 'UNIQUEID': uid()})
    data += params({'RECORD': 48, 'OWNERINDEX': count + 1})
    sch[key] = data
    if units:
        header = fields(sch[('FileHeader',)][4:4 + (struct.unpack_from('<I', sch[('FileHeader',)])[0] & 0xffffff)])
        header['PARTCOUNT0'] = str(units['count'] + 1)
        sch[('FileHeader',)] = params(header)
    update_sch_weight(sch)
    write_verified(output / 'Part.SchLib', sch)
    write_verified(output / 'Footprint.PcbLib', pcb)
    if not read_streams(output / 'Part.SchLib')[key].startswith(normalized_geometry): raise ValueError('Normalized symbol graphics or pins changed')
    project = '[Design]\nVersion=1.0\nOpenOutputs=1\nOutputPath=Compiled\n\n[Document1]\nDocumentPath=Part.SchLib\n\n[Document2]\nDocumentPath=Footprint.PcbLib\n'
    library_name = code or ('Local_' + sha(sch_path.read_bytes() + pcb_path.read_bytes())[:12])
    (output / (library_name + '.LibPkg')).write_text(project, encoding='utf-8-sig')
    report = {'generator_version': VERSION, 'lcsc': code, 'symbol': symbol, 'footprint': footprint, 'symbol_pin_count': len(pins), 'unique_pin_count': len(set(pins)), 'numbered_pad_count': len(pad_names), 'unique_pad_count': len(set(pad_names)), 'pin_map': {pin: pin for pin in dict.fromkeys(pins)}, 'embedded_models': models, 'bodies': bodies, 'source_schlib': str(sch_path), 'source_pcblib': str(pcb_path), 'source_schlib_sha256': sha(sch_path.read_bytes()), 'source_pcblib_sha256': sha(pcb_path.read_bytes()), 'original_symbol_geometry_sha256': sha(original_geometry), 'source_footprint_primitive_sha256': source_footprint_sha, 'footprint_primitive_sha256': sha(pcb[(fp_key, 'Data')]), 'model_placement': placement, 'project': str(output / (library_name + '.LibPkg')), 'intlib': str(output / (library_name + '.IntLib')), 'schlib': str(output / 'Part.SchLib'), 'pcblib': str(output / 'Footprint.PcbLib'), 'static_binding_validation': 'passed', 'native_ad_compile_verified': False, 'native_ad_placement_verified': False, 'three_dimensional_alignment_visually_verified': False}
    report['symbol_units'] = units or {'count': int(geometry[0][2].get('PARTCOUNT', '2')) - 1}
    (output / 'binding-report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    return report

def normalize_code(value):
    code = value.strip().upper()
    if not re.fullmatch(r'C[1-9][0-9]{0,11}', code): raise ValueError('Enter an LCSC code such as C8734')
    return code

def fetch(code, cache, force=False, require_3d=True, progress=None, include_obj=False):
    code, cache = normalize_code(code), pathlib.Path(cache).resolve()
    source = cache / 'source' / code
    backend = ROOT / 'vendor' / 'lceda' / 'lceda.exe'
    if not backend.is_file(): raise FileNotFoundError(f'Conversion backend is missing: {backend}')
    pointer = cache / 'source' / (code + '.json')
    if not force and pointer.is_file():
        source = pathlib.Path(json.loads(pointer.read_text(encoding='utf-8'))['source']).resolve()
        if source.parent != (cache / 'source').resolve(): raise ValueError('Cached source pointer is outside the cache folder')
    marker = source / 'download-complete.json'
    if force or not marker.exists() or (include_obj and not any(source.rglob('*.obj'))):
        if progress: progress('Downloading symbol, footprint and STEP', 10)
        source.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix=code + '_', dir=source.parent) as temp:
            command = [str(backend), 'get', code, '--ad', '--step', '-o', temp]
            if include_obj: command.append('--obj')
            try: result = subprocess.run(command, capture_output=True, timeout=240, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            except subprocess.TimeoutExpired as error: raise RuntimeError(f'Download timed out after 240 seconds for {code}; check network connectivity and retry') from error
            sch_files, pcb_files = list(pathlib.Path(temp).rglob('*.SchLib')), list(pathlib.Path(temp).rglob('*.PcbLib'))
            if result.returncode or len(sch_files) != 1 or len(pcb_files) != 1:
                detail = result.stderr.decode('utf-8', errors='replace')[-2000:]
                raise RuntimeError(f'Download/conversion failed for {code}: {detail or "symbol or footprint unavailable"}')
            target = source.parent / (code + '_' + uuid.uuid4().hex[:8])
            shutil.copytree(temp, target)
            (target / 'download-complete.json').write_text(json.dumps({'code': code, 'backend_sha256': sha(backend.read_bytes()), 'version': 'JLC-Export-Workstation v0.8.6'}), encoding='utf-8')
            source = target
        # Stable pointer only becomes visible after a successful download.
        pointer.write_text(json.dumps({'source': str(source)}), encoding='utf-8')
    out = cache / 'bound' / code / (datetime.datetime.now().strftime('%Y%m%d_%H%M%S') + '_' + uuid.uuid4().hex[:8])
    if progress: progress('Checking symbol, numbered pads and embedded STEP', 45)
    sch_files, pcb_files = list(source.rglob('*.SchLib')), list(source.rglob('*.PcbLib'))
    if len(sch_files) != 1 or len(pcb_files) != 1: raise ValueError('Cached download contains multiple or missing libraries; refresh the source download')
    source_rows = next(rows for key, data in read_streams(sch_files[0]).items() if len(key) == 2 and key[1] == 'Data' and (rows := list(records(data))) and rows[0][2].get('RECORD') == '1')
    source_codes = set(re.findall(r'\bLCSC\s+(C\d+)\b', source_rows[0][2].get('COMPONENTDESCRIPTION', '')))
    if source_codes and source_codes != {code}: raise ValueError('Cached symbol belongs to a different LCSC component; refresh the source download')
    report = prepare(sch_files[0], pcb_files[0], out, code, require_3d, require_source_geometry=True, progress=progress)
    if progress: progress('Symbol, footprint, pin map and STEP verified', 65)
    return report

def response(path, report=None, error=None, job_id=None):
    if not path: return
    path = pathlib.Path(path).resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    values = {'status': 'error', 'error': str(error).replace('\n', ' ').replace('\r', ' ')} if error else {'status': 'ok', **{key: report[key] for key in ('lcsc', 'symbol', 'footprint', 'project', 'intlib', 'schlib', 'pcblib')}}
    if report:
        for key in ('expected_components', 'new_part', 'source_add_sch', 'source_add_pcb', 'message'): values[key] = report.get(key, '')
        if 'documents' in report:
            values['document_count'] = len(report['documents'])
            for index, source in enumerate(report['documents']): values['document' + str(index)] = source
        if 'remove_documents' in report:
            values['remove_document_count'] = len(report['remove_documents'])
            for index, source in enumerate(report['remove_documents']): values['remove_document' + str(index)] = source
        if 'bindings' in report:
            values['binding_count'] = len(report['bindings'])
            for index, part in enumerate(report['bindings']):
                values['binding_name' + str(index)] = part['name']
                values['binding_footprint' + str(index)] = part['footprint']
    if job_id: values['job_id'] = job_id
    temp = path.with_name(path.name + '.' + uuid.uuid4().hex + '.tmp')
    temp.write_text(''.join(f'{key}={value}\n' for key, value in values.items()), encoding='utf-8-sig')
    temp.replace(path)

def read_request(path):
    data = pathlib.Path(path).read_bytes()
    if data.startswith((b'\xff\xfe', b'\xfe\xff')): source = data.decode('utf-16')
    else:
        try: source = data.decode('utf-8-sig')
        except UnicodeDecodeError: source = data.decode('mbcs')
    return {line.split('=', 1)[0].strip(): line.split('=', 1)[1].strip() for line in source.splitlines() if '=' in line}

def job_progress(path, job_id, stage, percent):
    path = pathlib.Path(path)
    temp = path.with_name(path.name + '.' + uuid.uuid4().hex + '.tmp')
    temp.write_text(f'job_id={job_id}\nstage={stage}\npercent={percent}\n', encoding='utf-8-sig')
    temp.replace(path)

def run_ad_job(config_path, expected_job_id=None):
    global ROOT
    config = json.loads(pathlib.Path(config_path).read_text(encoding='utf-8'))
    script_root = pathlib.Path(config['script_root']).resolve()
    ROOT = pathlib.Path(config['backend_root']).resolve()
    claimed = None
    if expected_job_id and not re.fullmatch(r'[A-Za-z0-9_-]{1,80}', expected_job_id): raise ValueError('Invalid AD request identifier')
    for pending in script_root.glob('job_' + expected_job_id + '.ini' if expected_job_id else 'job_*.ini'):
        candidate = pending.with_name(pending.name + '.' + uuid.uuid4().hex + '.claimed')
        try: pending.rename(candidate)
        except FileNotFoundError: continue
        claimed = candidate
        break
    if not claimed: raise RuntimeError('No pending AD import request was found')
    request = read_request(claimed)
    job_id = request.get('job_id', '')
    if not re.fullmatch(r'[A-Za-z0-9_-]{1,80}', job_id): raise ValueError('Invalid AD request identifier')
    reply = script_root / ('response_' + job_id + '.ini')
    if request.get('mode') == 'browser':
        from component_browser import run_browser
        return run_browser(config, request, reply)
    progress_path = script_root / ('progress_' + job_id + '.ini')
    def update(stage, percent): job_progress(progress_path, job_id, stage, percent)
    try:
        update('Worker started', 5)
        report = fetch(request.get('code', ''), config['cache'], progress=update)
        if (script_root / ('cancel_' + job_id + '.ini')).exists(): raise RuntimeError('Import was cancelled before source library update')
        if request.get('mode') == 'my_library':
            update('Merging master source libraries and binding all components', 70)
            from library_import import append_to_library
            report = append_to_library(report, config['my_library'])
        response(reply, report, job_id=job_id)
        update('Sources ready; waiting for AD native compile', 85)
        return 0
    except Exception as error:
        response(reply, error=error, job_id=job_id)
        update('Import stopped; see the error response', 0)
        return 1

def write_text_if_changed(path, text, encoding='utf-8-sig'):
    data = text.encode(encoding)
    if not path.exists() or path.read_bytes() != data: path.write_bytes(data)

def ad_script_encoding():
    if os.name != 'nt': return 'utf-8'
    import ctypes
    page = ctypes.windll.kernel32.GetACP()
    return 'utf-8' if page == 65001 else f'cp{page}'

def install_script(destination, library_root='D:/AltiumDocuments/MyLibrary'):
    destination = pathlib.Path(destination).resolve()
    destination.mkdir(parents=True, exist_ok=True)
    launcher = ROOT / 'LCSC_AD_Launcher.exe' if getattr(sys, 'frozen', False) else ROOT / 'dist' / 'LCSC_AD_Launcher.exe'
    library_root = pathlib.Path(library_root).resolve()
    substitutions = {'@@EXECUTABLE@@': str(launcher), '@@ROOT@@': str(destination), '@@CACHE@@': str(ROOT / 'cache'), '@@LIBROOT@@': str(library_root)}
    script = (RESOURCES / 'templates' / 'BrowserImporter.pas.in').read_text(encoding='utf-8')
    for key, value in substitutions.items(): script = script.replace(key, value.replace("'", "''"))
    encoding = ad_script_encoding()
    # RunScript's direct loader interprets Pascal literals with the Windows ANSI code page.
    # UTF-8 BOM works in the editor but misreads Chinese paths when launched from a menu.
    try: script.encode(encoding)
    except UnicodeEncodeError as error: raise ValueError('AD 脚本编码不支持所选路径，请使用当前系统语言支持的路径或英文路径。') from error
    for stem, prefix in (('LCSCBridge', 'LB_'), ('LCSCBridge2024', 'L24_'), ('MyLibraryMaster', 'MB_'), ('MyLibraryImporter', 'MI_')):
        alias = script
        for identifier in ('TraceNativeStage', 'SaveNativeSource', 'CompileLibraryResponse'): alias = re.sub(r'\b' + identifier + r'\b', prefix + identifier, alias)
        write_text_if_changed(destination / (stem + '.pas'), alias, encoding=encoding)
        project = destination / (stem + '.PrjScr')
        # AD adds start-procedure and editor metadata; preserve valid saved projects.
        if not project.exists() or not re.search(r'^DocumentPath=' + re.escape(stem) + r'\.pas\s*$', project.read_text(encoding='utf-8-sig'), re.M): write_text_if_changed(project, '[Design]\nVersion=1.0\n\n[Document1]\nDocumentPath=' + stem + '.pas\n')
    parameters = f'ProjectName={destination / "MyLibraryMaster.PrjScr"}|ProcName=MyLibraryMaster.pas>ImportToMyLibrary'
    write_text_if_changed(destination / 'AD_Menu_Command.txt', 'Process: ScriptingSystem:RunScript\nParameters: ' + parameters + '\nCaption: LCSC -> MyLibrary\n')
    launcher.parent.mkdir(parents=True, exist_ok=True)
    config = {'script_root': str(destination), 'backend_root': str(ROOT), 'cache': str(ROOT / 'cache'), 'my_library': str(library_root)}
    write_text_if_changed(launcher.parent / 'launcher-config.json', json.dumps(config, ensure_ascii=False, indent=2), encoding='utf-8')
    return {'script_project': str(destination / 'MyLibraryMaster.PrjScr'), 'menu_parameters': parameters, 'script_encoding': encoding}

def main():
    parser = argparse.ArgumentParser(description='Import linked LCSC components into Altium Designer 2024')
    commands = parser.add_subparsers(dest='command', required=True)
    get = commands.add_parser('fetch')
    get.add_argument('code')
    get.add_argument('--cache', default=str(ROOT / 'cache'))
    get.add_argument('--response')
    get.add_argument('--force', action='store_true')
    get.add_argument('--allow-no-3d', action='store_true')
    local = commands.add_parser('prepare')
    local.add_argument('--sch', required=True)
    local.add_argument('--pcb', required=True)
    local.add_argument('--out', required=True)
    local.add_argument('--code')
    local.add_argument('--allow-no-3d', action='store_true')
    merge = commands.add_parser('add-to-library')
    merge.add_argument('--report', required=True)
    merge.add_argument('--library', default='D:/AltiumDocuments/MyLibrary')
    repair = commands.add_parser('repair-library')
    repair.add_argument('--library', default='D:/AltiumDocuments/MyLibrary')
    setup = commands.add_parser('install-script')
    setup.add_argument('--out', default=str(ROOT / 'AD'))
    setup.add_argument('--library', default='D:/AltiumDocuments/MyLibrary')
    args = parser.parse_args()
    try:
        if args.command == 'fetch': result = fetch(args.code, args.cache, args.force, not args.allow_no_3d)
        elif args.command == 'prepare': result = prepare(args.sch, args.pcb, args.out, normalize_code(args.code) if args.code else None, not args.allow_no_3d)
        elif args.command == 'add-to-library':
            from library_import import append_to_library
            result = append_to_library(json.loads(pathlib.Path(args.report).read_text(encoding='utf-8')), args.library)
        elif args.command == 'repair-library':
            from main_library import import_master
            result = import_master(None, args.library)
        else: result = install_script(args.out, args.library)
        response(getattr(args, 'response', None), result)
        print(json.dumps(result, ensure_ascii=True))
        return 0
    except Exception as error:
        response(getattr(args, 'response', None), error=error)
        print(json.dumps({'status': 'error', 'error': str(error)}, ensure_ascii=True), file=sys.stderr)
        return 1

if __name__ == '__main__': sys.exit(main())
