"""Restore source symbol units lost by the flat conversion backend."""
import collections, json, math, pathlib, struct
import bridge


def source_units(source_dir):
    files = list(pathlib.Path(source_dir).glob('*_symbol_easyeda.json'))
    if not files: return None
    if len(files) != 1: raise ValueError('Expected one source symbol JSON')
    data = json.loads(files[0].read_text(encoding='utf-8'))['result']['dataStr']
    rows = [json.loads(line) for line in data.splitlines() if line.strip()] if isinstance(data, str) else data
    units, current, entries, attrs = [], 1, collections.defaultdict(list), {}
    for row in rows:
        kind = row[0]
        if kind == 'PART':
            units.append(str(row[1])); current = len(units)
        elif kind == 'ATTR' and row[2]: attrs.setdefault(str(row[2]), {})[str(row[3])] = str(row[4])
        elif kind == 'PIN' and row[1]: entries['pin'].append((current, row))
        elif kind == 'RECT': entries['14'].append((current, row))
        elif kind == 'POLY': entries['6'].append((current, row))
        elif kind == 'CIRCLE' and abs(float(row[4])) > 1e-6: entries['8'].append((current, row))
        elif kind == 'ELLIPSE' and min(abs(float(row[4])), abs(float(row[5]))) > 1e-6: entries['8'].append((current, row))
    return units, entries, attrs


def restore(rows, source_dir):
    info = source_units(source_dir)
    if info is None or len(info[0]) <= 1: return rows, None
    units, entries, attrs = info
    source_path = next(pathlib.Path(source_dir).glob('*_symbol_easyeda.json'))
    raw = json.loads(source_path.read_text(encoding='utf-8'))['result']['dataStr']
    source_rows = [json.loads(line) for line in raw.splitlines() if line.strip()] if isinstance(raw, str) else raw
    unsupported = {row[0] for row in source_rows if row[0] in ('ARC', 'BEZIER', 'TEXT', 'LINE', 'IMAGE')}
    if unsupported: raise ValueError('Unsupported source symbol unit geometry: ' + ', '.join(sorted(unsupported)))
    indices, result = collections.Counter(), []
    for flag, payload, fields in rows:
        number = bridge.pin_number(flag, payload, fields)
        kind = 'pin' if number is not None else fields.get('RECORD')
        values = fields.copy()
        if kind in ('pin', '14', '6', '8'):
            index = indices[kind]; indices[kind] += 1
            if index >= len(entries[kind]): raise ValueError('Source symbol unit geometry does not match conversion')
            part, source = entries[kind][index]
            if kind == 'pin':
                source_attrs = attrs.get(str(source[1]), {})
                expected = source_attrs.get('NUMBER', source_attrs.get('Pin Number', str(index + 1)))
                if number != expected.replace('|', '_'): raise ValueError('Source symbol pin order changed; unit assignment refused')
                length = float(source[6]) or 20.0
                angle = math.radians(float(source[7]))
                expected_xy = [round(float(source[4]) + length * math.cos(angle)), round(float(source[5]) + length * math.sin(angle))]
                if flag:
                    offset = 13 + payload[12]
                    xy = list(struct.unpack_from('<hh', payload, offset + 5))
                else: xy = [int(values.get('LOCATION.X', '0')), int(values.get('LOCATION.Y', '0'))]
                if xy != expected_xy: raise ValueError('Source symbol pin coordinates changed; unit assignment refused')
            if kind == '14':
                actual = [float(values.get(key, 0)) + float(values.get(key + '_FRAC', 0)) / 100000 for key in ('LOCATION.X', 'LOCATION.Y', 'CORNER.X', 'CORNER.Y')]
                if any(abs(a - float(b)) > 0.001 for a, b in zip(actual, source[2:6])): raise ValueError('Source rectangle coordinates changed; unit assignment refused')
            if flag:
                data = bytearray(payload); struct.pack_into('<h', data, 5, part); payload = bytes(data)
            else: values['OWNERPARTID'] = str(part)
        elif kind == '1':
            values.update(PARTCOUNT=str(len(units) + 1), CURRENTPARTID='1')
        elif kind in ('34', '41') and values.get('NAME') in ('Designator', 'Comment'):
            values['OWNERPARTID'] = '-1'
        if not flag and values != fields: payload = bridge.params(values)[4:]
        result.append((flag, payload, values))
    if any(indices[kind] != len(entries[kind]) for kind in ('pin', '14', '6', '8')): raise ValueError('Source symbol contains unsupported or missing unit geometry')
    return result, {'count': len(units), 'names': units, 'pin_counts': dict(collections.Counter(part for part, _ in entries['pin']))}
