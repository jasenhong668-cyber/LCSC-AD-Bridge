"""Preserve native pin text attributes and remap fonts when merging SchLibs."""
import struct, zlib
import bridge

FONT_FIELDS = ('FONTNAME', 'SIZE', 'BOLD', 'ITALIC', 'UNDERLINE', 'STRIKEOUT', 'CHARSET')


def header(data):
    size = struct.unpack_from('<I', data)[0] & 0xffffff
    return bridge.fields(data[4:4 + size])


def named_block(name, raw):
    label, compressed = str(name).encode('ascii'), zlib.compress(raw)
    return bridge.block(b'\xd0' + bytes([len(label)]) + label + struct.pack('<I', len(compressed)) + compressed, 1)


def pin_text(rows, font_header):
    pins = [(flag, payload, p) for flag, payload, p in rows if bridge.pin_number(flag, payload, p) is not None]
    if not any(p.get('RECORD') == '2' for _, _, p in pins): return None
    result = bytearray(bridge.params({'HEADER': 'PinTextData', 'Weight': len(pins)}))
    for index, (flag, payload, pin) in enumerate(pins):
        if flag:
            color = struct.unpack_from('<I', payload, 13 + payload[12] + 9)[0]
            result += named_block(index, struct.pack('<BhIBhI', 0, 1, color, 0, 1, color))
            continue
        raw = bytearray()
        for item, prefix in (('NAME', 'PINNAME'), ('DESIGNATOR', 'PINDESIGNATOR')):
            mode = int(pin.get(prefix + '_POSITIONCONGLOMERATE', '16'))
            if mode not in (0, 16): raise ValueError('Custom source pin position needs native conversion')
            font_id = int(pin.get(item + '_CUSTOMFONTID', '1'))
            color = int(pin.get(item + '_CUSTOMCOLOR', font_header.get('COLOR' + str(font_id), '0')))
            raw += struct.pack('<BhI', mode, font_id, color)
        result += named_block(index, bytes(raw))
    return bytes(result)


def merge_fonts(sch, incoming):
    target, source = header(sch[('FileHeader',)]), header(incoming[('FileHeader',)])
    count = int(target.get('FONTIDCOUNT', '1'))
    def signature(values, index):
        defaults = {'FONTNAME': 'Times New Roman', 'SIZE': '10', 'BOLD': 'F', 'ITALIC': 'F', 'UNDERLINE': 'F', 'STRIKEOUT': 'F', 'CHARSET': '0'}
        return tuple(values.get(field + str(index), defaults[field]) for field in FONT_FIELDS)
    mapping = {}
    for index in range(1, int(source.get('FONTIDCOUNT', '1')) + 1):
        match = next((i for i in range(1, count + 1) if signature(target, i) == signature(source, index)), None)
        if match is None:
            count += 1
            match = count
            for field in FONT_FIELDS:
                if field + str(index) in source: target[field + str(match)] = source[field + str(index)]
        mapping[index] = match
    target['FONTIDCOUNT'] = str(count)
    sch[('FileHeader',)] = bridge.params(target)
    return mapping


def remap_pin_text(data, mapping):
    result = bytearray()
    for flag, payload, fields in bridge.records(data):
        if not flag:
            result += bridge.block(payload, flag)
            continue
        if len(payload) < 7 or payload[0] != 0xd0: raise ValueError('Invalid native pin text entry')
        length = payload[1]
        start = 2 + length
        if start + 4 > len(payload): raise ValueError('Truncated native pin text entry')
        size = struct.unpack_from('<I', payload, start)[0]
        if start + 4 + size != len(payload): raise ValueError('Invalid native pin text compression length')
        raw = bytearray(zlib.decompress(payload[start + 4:]))
        if len(raw) != 14 or raw[0] not in (0, 16) or raw[7] not in (0, 16): raise ValueError('Unsupported native pin text layout')
        for offset in (1, 8):
            old = struct.unpack_from('<h', raw, offset)[0]
            if old not in mapping: raise ValueError('Pin text references a missing source font')
            struct.pack_into('<h', raw, offset, mapping[old])
        result += named_block(payload[2:start].decode('ascii'), raw)
    return bytes(result)


def remap_rows(rows, mapping):
    result = []
    for flag, payload, fields in rows:
        if not flag and 'FONTID' in fields:
            fields = fields.copy()
            old = int(fields['FONTID'])
            if old not in mapping: raise ValueError('Symbol text references a missing source font')
            fields['FONTID'] = str(mapping[old])
            payload = bridge.params(fields)[4:]
        result.append((flag, payload, fields))
    return result
