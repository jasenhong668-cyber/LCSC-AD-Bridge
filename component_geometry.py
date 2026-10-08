"""Apply EasyEDA Pro's complete model placement before embedding STEP in Altium."""
import json, pathlib, re, struct, subprocess, sys, tempfile, zlib


def source_info(source_dir):
    metadata = list(pathlib.Path(source_dir).glob('*_footprint_easyeda.json'))
    step = list(pathlib.Path(source_dir).glob('*.step'))
    if not metadata: return None
    if len(metadata) != 1 or len(step) != 1: raise ValueError('Expected one source footprint JSON and one source STEP')
    source = json.loads(metadata[0].read_text(encoding='utf-8'))
    transform = source.get('result', {}).get('model_3d', {}).get('transform', '')
    try: values = [float(value) for value in transform.split(',')]
    except ValueError as error: raise ValueError('Invalid EasyEDA 3D transform') from error
    if len(values) != 9: raise ValueError('EasyEDA 3D transform must contain nine values')
    return step[0], values


def replace_body(payload, model_ids):
    if len(payload) < 22: raise ValueError('Truncated Altium 3D body')
    size = struct.unpack_from('<I', payload, 18)[0]
    if size < 2 or 22 + size > len(payload): raise ValueError('Invalid Altium 3D body parameters')
    data = payload[22:22 + size]
    match = re.search(rb'\|MODELID=([^|\x00]+)', data)
    if not match or match[1].decode('latin1') not in model_ids: return payload, False
    for name in (b'MODEL.2D.X', b'MODEL.2D.Y', b'MODEL.3D.DZ'):
        pattern = rb'\|' + re.escape(name) + rb'=[^|\x00]+'
        if not re.search(pattern, data): raise ValueError(f'Missing 3D placement field {name.decode()}')
        data = re.sub(pattern, b'|' + name + b'=0mil', data, count=1)
    for name in (b'MODEL.2D.ROTATION', b'MODEL.3D.ROTX', b'MODEL.3D.ROTY', b'MODEL.3D.ROTZ'):
        pattern = rb'\|' + re.escape(name) + rb'=[^|\x00]+'
        if not re.search(pattern, data): raise ValueError(f'Missing 3D rotation field {name.decode()}')
        data = re.sub(pattern, b'|' + name + b'=0.000', data, count=1)
    data = re.sub(rb'\|MODEL.CHECKSUM=[^|\x00]+', b'|MODEL.CHECKSUM=0', data, count=1)
    return payload[:18] + struct.pack('<I', len(data)) + data + payload[22 + size:], True


def replace_primitives(data, model_ids):
    import bridge
    offset = (struct.unpack_from('<I', data)[0] & 0xffffff) + 4
    if offset > len(data): raise ValueError('Invalid footprint header')
    result, count = bytearray(data[:offset]), 0
    while offset < len(data):
        start, kind = offset, data[offset]
        offset += 1
        if offset + 4 > len(data): raise ValueError('Truncated footprint primitive')
        word = struct.unpack_from('<I', data, offset)[0]
        size, flag = word & 0xffffff, word >> 24
        end = offset + 4 + size
        if end > len(data): raise ValueError('Truncated footprint primitive payload')
        if kind == 12:
            payload, changed = replace_body(data[offset + 4:end], model_ids)
            result += bytes([kind]) + bridge.block(payload, flag)
            count += changed
        else: result += data[start:end]
        offset = end
        for _ in range({2: 5, 5: 1}.get(kind, 0)):
            if offset + 4 > len(data): raise ValueError('Truncated auxiliary footprint block')
            size = struct.unpack_from('<I', data, offset)[0] & 0xffffff
            end = offset + 4 + size
            if end > len(data): raise ValueError('Truncated auxiliary footprint payload')
            result += data[offset:end]
            offset = end
    if not count: raise ValueError('Embedded STEP has no matching 3D body')
    return bytes(result), count


def normalize(pcb, footprint_key, source_dir, cache_dir):
    import bridge
    models = list(bridge.records(pcb.get(('Library', 'Models', 'Data'), b'')))
    if not models: return None
    info = source_info(source_dir)
    if info is None: return None
    raw_step, values = info
    if len(models) != 1: raise ValueError('Source footprint must have exactly one embedded STEP model')
    model_id = models[0][2].get('ID')
    if not model_id: raise ValueError('Source STEP model has no ID')
    embedded = zlib.decompress(pcb[('Library', 'Models', '0')])
    raw = raw_step.read_bytes()
    if bridge.sha(raw) != bridge.sha(embedded): raise ValueError('Source STEP does not match embedded STEP')
    root = bridge.ROOT / 'GeometryWorker.exe'
    if getattr(sys, 'frozen', False) and not root.is_file(): raise FileNotFoundError('GeometryWorker.exe is missing from the portable package')
    command = [str(root)] if root.is_file() else [sys.executable, str(pathlib.Path(__file__).with_name('geometry_worker.py'))]
    pathlib.Path(cache_dir).mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='model_', dir=cache_dir) as temp:
        folder = pathlib.Path(temp)
        request = {'step': str(raw_step), 'transform': values, 'output_step': str(folder / 'normalized.step'), 'output_mesh': str(folder / 'preview-mesh.json')}
        (folder / 'request.json').write_text(json.dumps(request), encoding='utf-8')
        try: completed = subprocess.run(command + ['--request', str(folder / 'request.json'), '--result', str(folder / 'result.json')], capture_output=True, timeout=240, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        except subprocess.TimeoutExpired as error: raise RuntimeError('3D geometry normalization timed out') from error
        if not (folder / 'result.json').is_file(): raise RuntimeError('3D geometry worker failed: ' + completed.stderr.decode('utf-8', errors='replace')[-1000:])
        report = json.loads((folder / 'result.json').read_text(encoding='utf-8'))
        if completed.returncode or report.get('status') != 'ok': raise RuntimeError('3D geometry normalization failed: ' + report.get('error', 'unknown error'))
        normalized = (folder / 'normalized.step').read_bytes()
        if not normalized.lstrip().startswith(b'ISO-10303-21;') or b'END-ISO-10303-21;' not in normalized: raise ValueError('Normalized STEP is invalid')
        pcb[('Library', 'Models', '0')] = zlib.compress(normalized)
        model = models[0][2].copy()
        for field in ('ROTX', 'ROTY', 'ROTZ', 'DZ', 'CHECKSUM'): model[field] = '0'
        pcb[('Library', 'Models', 'Data')] = bridge.params(model)
        pcb[(footprint_key, 'Data')], count = replace_primitives(pcb[(footprint_key, 'Data')], {model_id})
        step_path = pathlib.Path(cache_dir) / 'normalized.step'
        step_path.write_bytes(normalized)
        mesh_path = pathlib.Path(cache_dir) / 'preview-mesh.json'
        mesh_path.write_bytes((folder / 'preview-mesh.json').read_bytes())
        metadata = json.loads(next(pathlib.Path(source_dir).glob('*_footprint_easyeda.json')).read_text(encoding='utf-8'))
        source_model = metadata.get('result', {}).get('model_3d', {})
        return {**report, 'source_step_sha256': bridge.sha(raw), 'normalized_step_sha256': bridge.sha(normalized), 'normalized_body_count': count, 'source_transform': values, 'source_model': {'title': source_model.get('title', ''), 'uri': source_model.get('uri', '')}, 'normalized_step': str(step_path), 'preview_mesh': str(mesh_path)}
