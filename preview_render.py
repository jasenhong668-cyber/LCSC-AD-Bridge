"""Render actual Altium source geometry and downloaded OBJ mesh previews."""
import json, math, pathlib, struct
from PIL import Image, ImageDraw, ImageFont
import bridge, main_library

def font(size=14):
    try: return ImageFont.truetype('C:/Windows/Fonts/arial.ttf', size)
    except OSError: return ImageFont.load_default()

def fit(points, size, margin=22):
    xs, ys = zip(*points)
    x0, x1, y0, y1 = min(xs), max(xs), min(ys), max(ys)
    scale = min((size[0] - margin * 2) / max(x1 - x0, 1), (size[1] - margin * 2) / max(y1 - y0, 1))
    return lambda x, y: ((x - (x0 + x1) / 2) * scale + size[0] / 2, size[1] / 2 - (y - (y0 + y1) / 2) * scale), scale

def coord(p, key): return float(p.get(key, 0)) + float(p.get(key + '_FRAC', 0)) / 100000

def schematic(report, size=(480, 260)):
    rows = main_library.components(bridge.read_streams(report.get('schlib', report['source_schlib'])))[0][1]
    shapes, points, pins = [], [], []
    for flag, data, p in rows:
        record = p.get('RECORD')
        if record == '2':
            visible = int(p.get('PINCONGLOMERATE', 0))
            x, y, length, rotation = coord(p, 'LOCATION.X'), coord(p, 'LOCATION.Y'), coord(p, 'PINLENGTH'), visible & 3
            name, number = p.get('NAME', ''), p.get('DESIGNATOR', '')
        elif flag and bridge.pin_number(flag, data, p) is not None:
            offset = 13 + data[12]
            length, x, y = struct.unpack_from('<hhh', data, offset + 3)
            visible = data[offset + 2]
            rotation = visible & 3
            pos = offset + 13
            name = data[pos + 1:pos + 1 + data[pos]].decode('latin1')
            number = bridge.pin_number(flag, data, p)
        else:
            if record in ('6', '7'):
                vertices = [(coord(p, 'X' + str(i)), coord(p, 'Y' + str(i))) for i in range(1, int(p.get('LOCATIONCOUNT', 0)) + 1)]
                if vertices: shapes.append(('line', vertices)); points.extend(vertices)
            elif record in ('13', '14'):
                vertices = [(coord(p, 'LOCATION.X'), coord(p, 'LOCATION.Y')), (coord(p, 'CORNER.X'), coord(p, 'CORNER.Y'))]
                shapes.append(('rect', vertices)); points.extend(vertices)
            elif record in ('8', '11', '12'):
                x, y, radius = coord(p, 'LOCATION.X'), coord(p, 'LOCATION.Y'), coord(p, 'RADIUS')
                ry = coord(p, 'SECONDARYRADIUS') or radius
                vertices = [(x - radius, y - ry), (x + radius, y + ry)]
                shapes.append(('ellipse', vertices)); points.extend(vertices)
            continue
        dx, dy = [(1, 0), (0, 1), (-1, 0), (0, -1)][rotation]
        end = (x + length * dx, y + length * dy)
        pins.append((x, y, end, dx, dy, name if visible & 8 else '', number if visible & 16 else ''))
        points.extend(((x, y), end))
    if not points: raise ValueError('No supported schematic geometry to preview')
    transform, scale = fit(points, size, 28)
    image = Image.new('RGB', size, '#faf9f5')
    draw = ImageDraw.Draw(image)
    for kind, vertices in shapes:
        screen = [transform(x, y) for x, y in vertices]
        if kind == 'line': draw.line(screen, fill='#913537', width=2)
        else:
            bounds = [min(p[0] for p in screen), min(p[1] for p in screen), max(p[0] for p in screen), max(p[1] for p in screen)]
            if kind == 'rect': draw.rectangle(bounds, outline='#913537', width=2)
            else: draw.ellipse(bounds, outline='#913537', width=2)
    text_font = font(max(7, min(14, round(scale * 4))))
    for x, y, end, dx, dy, name, number in pins:
        start, tip = transform(x, y), transform(*end)
        draw.line((start, tip), fill='#913537', width=2)
        anchor = 'lm' if dx > 0 else 'rm' if dx < 0 else 'mm'
        if name: draw.text(transform(end[0] + dx * 3, end[1] + dy * 3), name, fill='#223449', font=text_font, anchor=anchor)
        if number: draw.text(((start[0] + tip[0]) / 2, (start[1] + tip[1]) / 2 - 5), number, fill='#436bb1', font=text_font, anchor='ms')
    return image

def primitives(data):
    pos = (struct.unpack_from('<I', data)[0] & 0xffffff) + 4
    while pos < len(data):
        kind = data[pos]
        pos += 1
        blocks = []
        for _ in range({2: 6, 5: 2}.get(kind, 1)):
            size = struct.unpack_from('<I', data, pos)[0] & 0xffffff
            pos += 4
            if pos + size > len(data): raise ValueError('Truncated footprint preview primitive')
            blocks.append(data[pos:pos + size])
            pos += size
        yield kind, blocks

def footprint(report, size=(480, 230)):
    pcb = bridge.read_streams(report.get('pcblib', report['source_pcblib']))
    key = next(iter(main_library.footprints(pcb).values()))
    pads, tracks, arcs, points = [], [], [], []
    for kind, blocks in primitives(pcb[(key, 'Data')]):
        if kind == 2:
            data = blocks[4]
            x, y, width, height = struct.unpack_from('<iiii', data, 13)
            hole = struct.unpack_from('<I', data, 45)[0]
            rotation = struct.unpack_from('<d', data, 52)[0]
            number = blocks[0][1:1 + blocks[0][0]].decode('latin1')
            pads.append((x, y, width, height, hole, data[49], rotation, number))
            points.extend(((x - max(width, height) / 2, y - max(width, height) / 2), (x + max(width, height) / 2, y + max(width, height) / 2)))
        elif kind == 4 and len(blocks[0]) >= 33:
            data = blocks[0]
            x1, y1, x2, y2, width = struct.unpack_from('<iiiii', data, 13)
            tracks.append((x1, y1, x2, y2, width, data[0]))
            points.extend(((x1, y1), (x2, y2)))
        elif kind == 1 and len(blocks[0]) >= 45:
            data = blocks[0]
            x, y, radius = struct.unpack_from('<iii', data, 13)
            start, end = struct.unpack_from('<dd', data, 25)
            width = struct.unpack_from('<i', data, 41)[0]
            arcs.append((x, y, radius, start, end, width))
            points.extend(((x - radius, y - radius), (x + radius, y + radius)))
    if not points: raise ValueError('No pads or tracks to preview')
    transform, scale = fit(points, size, 20)
    image = Image.new('RGB', size, '#11171d')
    draw = ImageDraw.Draw(image)
    for x1, y1, x2, y2, width, layer in tracks: draw.line((transform(x1, y1), transform(x2, y2)), fill='#f3d46b' if layer in (33, 34) else '#7ebbc0', width=max(1, min(6, round(width * scale))))
    for x, y, radius, start, end, width in arcs:
        if end <= start: end += 360
        vertices = [transform(x + radius * math.cos(math.radians(a)), y + radius * math.sin(math.radians(a))) for a in [start + (end - start) * i / 72 for i in range(73)]]
        draw.line(vertices, fill='#f3d46b', width=max(1, min(5, round(width * scale))))
    for x, y, width, height, hole, shape, rotation, number in pads:
        center = transform(x, y)
        w, h = width * scale, height * scale
        color = '#50bec1' if hole else '#d36872'
        if shape == 1 and abs(width - height) < 10:
            draw.ellipse((center[0] - w / 2, center[1] - h / 2, center[0] + w / 2, center[1] + h / 2), fill=color, outline='#f5dfa0', width=1)
        else:
            angle = math.radians(rotation)
            corners = [(x + a * math.cos(angle) - b * math.sin(angle), y + a * math.sin(angle) + b * math.cos(angle)) for a, b in [(-width / 2, -height / 2), (width / 2, -height / 2), (width / 2, height / 2), (-width / 2, height / 2)]]
            draw.polygon([transform(*p) for p in corners], fill=color, outline='#f5dfa0')
        if hole:
            r = max(1, hole * scale / 2)
            draw.ellipse((center[0] - r, center[1] - r, center[0] + r, center[1] + r), fill='#11171d', outline='#e1ebee')
        if min(w, h) > 11 and not hole: draw.text(center, number, fill='white', font=font(min(13, round(min(w, h) / 2))), anchor='mm')
    return image

def obj_mesh(path):
    vertices, faces, materials, material = [], [], {}, 'default'
    directory = pathlib.Path(path).parent
    material_paths = [pathlib.Path(path)] + list(directory.glob('*.mtl'))
    for filename in material_paths:
        current = 'default'
        for line in filename.read_text(encoding='utf-8', errors='replace').splitlines():
            values = line.split()
            if values and values[0] == 'newmtl': current = ' '.join(values[1:])
            if values and values[0] == 'Kd': materials[current] = tuple(max(0, min(255, round(float(v) * 255))) for v in values[1:4])
    for line in pathlib.Path(path).read_text(encoding='utf-8', errors='replace').splitlines():
        values = line.split()
        if not values: continue
        if values[0] == 'v': vertices.append(tuple(float(v) for v in values[1:4]))
        elif values[0] == 'usemtl': material = ' '.join(values[1:])
        elif values[0] == 'f':
            indices = [int(v.split('/')[0]) for v in values[1:]]
            indices = [v - 1 if v > 0 else len(vertices) + v for v in indices]
            for i in range(1, len(indices) - 1): faces.append(((indices[0], indices[i], indices[i + 1]), materials.get(material, (175, 179, 186))))
    if not vertices or not faces or any(i < 0 or i >= len(vertices) for face, _ in faces for i in face): raise ValueError('OBJ mesh is missing or invalid')
    return vertices, faces


def with_footprint(report, mesh):
    vertices, faces = list(mesh[0]), list(mesh[1])
    pcb = bridge.read_streams(report['pcblib'])
    key = next(iter(main_library.footprints(pcb).values()))
    for kind, blocks in primitives(pcb[(key, 'Data')]):
        if kind != 2: continue
        data = blocks[4]
        x, y, width, height = [value * 2.54e-6 for value in struct.unpack_from('<iiii', data, 13)]
        hole = struct.unpack_from('<I', data, 45)[0] * 2.54e-6
        angle = math.radians(struct.unpack_from('<d', data, 52)[0])
        shape, start = data[49], len(vertices)
        def vertex(a, b): return [x + a * math.cos(angle) - b * math.sin(angle), y + a * math.sin(angle) + b * math.cos(angle), .01]
        if not hole and shape != 1:
            vertices.extend(vertex(a, b) for a, b in [(-width / 2, -height / 2), (width / 2, -height / 2), (width / 2, height / 2), (-width / 2, height / 2)])
            faces.extend([((start, start + 1, start + 2), (205, 168, 65)), ((start, start + 2, start + 3), (205, 168, 65))])
            continue
        for i in range(32):
            theta = math.tau * i / 32
            c, s = math.cos(theta), math.sin(theta)
            radius = min(width / (2 * max(abs(c), 1e-9)), height / (2 * max(abs(s), 1e-9))) if shape != 1 else 1 / math.sqrt((c / (width / 2)) ** 2 + (s / (height / 2)) ** 2)
            vertices.extend((vertex(radius * c, radius * s), vertex(hole * c / 2, hole * s / 2)))
        for i in range(32):
            a, b = start + i * 2, start + ((i + 1) % 32) * 2
            faces.extend([((a, b, b + 1), (205, 168, 65)), ((a, b + 1, a + 1), (205, 168, 65))])
    return vertices, faces

def model(mesh, size=(480, 260), angle=32, elevation=25):
    vertices, faces = mesh
    az, el = math.radians(angle), math.radians(elevation)
    projected = []
    for x, y, z in vertices:
        a, b = x * math.cos(az) - y * math.sin(az), x * math.sin(az) + y * math.cos(az)
        projected.append((a, z * math.cos(el) - b * math.sin(el), b * math.cos(el) + z * math.sin(el)))
    transform, _ = fit([(x, y) for x, y, _ in projected], size, 22)
    image = Image.new('RGB', size, '#11171d')
    draw = ImageDraw.Draw(image)
    for indices, color in sorted(faces, key=lambda f: sum(projected[i][2] for i in f[0]) / 3):
        a, b, c = [projected[i] for i in indices]
        u, v = [b[i] - a[i] for i in range(3)], [c[i] - a[i] for i in range(3)]
        normal = (u[1] * v[2] - u[2] * v[1], u[2] * v[0] - u[0] * v[2], u[0] * v[1] - u[1] * v[0])
        length = math.sqrt(sum(n * n for n in normal)) or 1
        shade = 0.65 + 0.35 * abs((normal[0] * -0.3 + normal[1] * 0.4 + normal[2] * 0.85) / length)
        draw.polygon([transform(projected[i][0], projected[i][1]) for i in indices], fill=tuple(round(channel * shade) for channel in color))
    return image
