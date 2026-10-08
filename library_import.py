"""Add independent symbol/footprint sources to an existing integrated library project."""
import datetime, hashlib, json, pathlib, re, shutil, struct, uuid
import bridge

def project_documents(project):
    text = pathlib.Path(project).read_text(encoding='utf-8-sig')
    entries = []
    for section, body in re.findall(r'^\[(Document\d+)\]\s*\n(.*?)(?=^\[|\Z)', text, re.M | re.S):
        match = re.search(r'^DocumentPath=(.+)$', body, re.M | re.I)
        if match:
            path = pathlib.Path(match[1].strip())
            entries.append((section, path if path.is_absolute() else pathlib.Path(project).parent / path))
    return text, entries

def catalog(project):
    _, documents = project_documents(project)
    parts = []
    for _, path in documents:
        if path.suffix.lower() != '.schlib': continue
        if not path.is_file(): raise FileNotFoundError(f'Existing source library is missing: {path}')
        for key, data in bridge.read_streams(path).items():
            if len(key) != 2 or key[-1] != 'Data' or key[0] in ('Library', 'Storage'): continue
            rows = list(bridge.records(data))
            component = next((p for _, _, p in rows if p.get('RECORD') == '1'), None)
            if not component: continue
            codes = set()
            for _, _, p in rows:
                if p.get('RECORD') == '41' and p.get('NAME', '').upper() == 'LCSC': codes.add(p.get('TEXT', '').upper())
                for value in p.values(): codes.update(re.findall(r'\bLCSC\s+(C[1-9][0-9]+)\b', value, re.I))
            models = [p for _, _, p in rows if p.get('RECORD') == '45' and p.get('MODELTYPE', '').upper() == 'PCBLIB']
            parts.append({'name': component.get('LIBREFERENCE', key[0]), 'codes': sorted(codes), 'schlib': str(path.resolve()), 'models': models})
    names = [part['name'] for part in parts]
    if len(set(names)) != len(names): raise ValueError('Existing library has duplicated Design Item IDs; resolve them before importing')
    return parts

def save_pending(root, report):
    _, documents = project_documents(pathlib.Path(root) / 'MyLibrary.LibPkg')
    report['documents'] = [str(path.resolve()) for _, path in documents if path.suffix.lower() in ('.schlib', '.pcblib')]
    bridge.response(pathlib.Path(root) / 'LCSC-import-pending.ini', report)
    return report

def relink_schlib(path, pcb_name):
    streams = bridge.read_streams(path)
    changes = 0
    for key, data in list(streams.items()):
        if len(key) != 2 or key[-1] != 'Data' or key[0] in ('Library', 'Storage'): continue
        rows, changed = [], False
        for flag, payload, p in bridge.records(data):
            if p.get('RECORD') == '45' and p.get('MODELTYPE', '').upper() == 'PCBLIB':
                p['MODELDATAFILE0'] = pcb_name
                p.pop('%UTF8%MODELDATAFILE0', None)
                rows.append(bridge.params(p))
                changes += 1
                changed = True
            else: rows.append(bridge.block(payload, flag))
        if changed: streams[key] = b''.join(rows)
    if changes != 1: raise ValueError('Incoming component must have exactly one PCB footprint model')
    return streams

def append_independent_sources(report, library_root):
    root = pathlib.Path(library_root).resolve()
    project = root / 'MyLibrary.LibPkg'
    if not project.is_file(): raise FileNotFoundError(f'MyLibrary project is missing: {project}')
    original = project.read_bytes()
    text, documents = project_documents(project)
    existing = catalog(project)
    code = bridge.normalize_code(report['lcsc'])
    same_code = [part for part in existing if code in part['codes']]
    target = root / 'Compiled' / 'MyLibrary.IntLib'
    if same_code:
        if len(same_code) != 1: raise ValueError(f'LCSC code {code} occurs in multiple components')
        part = same_code[0]
        current = [model for model in part['models'] if model.get('ISCURRENT', '').upper() in ('T', 'TRUE')]
        if len(current) != 1: raise ValueError('Existing component does not have one default PCB footprint')
        return save_pending(root, {**report, 'symbol': part['name'], 'footprint': current[0]['MODELNAME'], 'project': str(project), 'intlib': str(target), 'expected_components': len(existing), 'new_part': False, 'source_add_sch': '', 'source_add_pcb': '', 'message': f'{code} already exists as {part["name"]}; source library was preserved'})
    if report['symbol'] in {part['name'] for part in existing}: raise ValueError('A different component already uses this Design Item ID; automatic overwrite refused')
    stamp = datetime.datetime.now().strftime('%Y%m%d_%H%M%S') + '_' + uuid.uuid4().hex[:8]
    dest = root / '_Imported' / code / stamp
    dest.mkdir(parents=True, exist_ok=False)
    sch = dest / (code + '.SchLib')
    pcb = dest / (code + '.PcbLib')
    bridge.write_verified(sch, relink_schlib(report['schlib'], pcb.name))
    shutil.copy2(report['pcblib'], pcb)
    if pcb.read_bytes() != pathlib.Path(report['pcblib']).read_bytes(): raise ValueError('Footprint or embedded STEP changed while importing')
    maximum = max((int(section[8:]) for section, _ in documents), default=0)
    updated = text.rstrip() + '\n\n'
    for index, source in enumerate((sch, pcb), maximum + 1):
        updated += f'[Document{index}]\nDocumentPath={source.relative_to(root)}\nAnnotationEnabled=1\nDoLibraryUpdate=1\nDoDatabaseUpdate=1\n\n'
    backup = root / '_SourceBackup' / ('LCSC_Importer_' + stamp)
    backup.mkdir(parents=True, exist_ok=False)
    for name in ('MyLibrary.LibPkg', 'MyParts.SchLib', 'MyFootprints.PcbLib'):
        source = root / name
        if source.is_file(): shutil.copy2(source, backup / name)
    if target.is_file(): shutil.copy2(target, backup / 'MyLibrary.IntLib')
    if project.read_bytes() != original: raise RuntimeError('MyLibrary project changed during preparation; retry after saving it in AD')
    temporary = root / ('MyLibrary.LibPkg.' + stamp + '.tmp')
    temporary.write_text(updated, encoding='utf-8-sig')
    temporary.replace(project)
    final = {**report, 'project': str(project), 'intlib': str(target), 'schlib': str(sch), 'pcblib': str(pcb), 'expected_components': len(existing) + 1, 'new_part': True, 'source_add_sch': str(sch), 'source_add_pcb': str(pcb), 'backup': str(backup), 'message': f'Added {report["symbol"]} to MyLibrary source project; native AD compilation is pending'}
    (dest / 'import-report.json').write_text(json.dumps(final, ensure_ascii=False, indent=2), encoding='utf-8')
    refreshed = catalog(project)
    if len(refreshed) != len(existing) + 1 or not any(part['name'] == report['symbol'] and code in part['codes'] for part in refreshed):
        shutil.copy2(backup / 'MyLibrary.LibPkg', project)
        raise ValueError('Combined source project failed validation; original project was restored')
    return save_pending(root, final)

def append_to_library(report, library_root):
    from main_library import import_master
    return import_master(report, library_root)
