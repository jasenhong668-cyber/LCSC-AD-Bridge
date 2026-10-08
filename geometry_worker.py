"""Isolated OCCT process: apply source placement to colored STEP and preview mesh."""
import argparse, json, math, pathlib, re, sys

def multiply(a, b): return [[sum(a[i][k] * b[k][j] for k in range(3)) for j in range(3)] for i in range(3)]
def apply(matrix, point): return [sum(matrix[i][j] * point[j] for j in range(3)) for i in range(3)]

def source_matrix(bounds, values):
    if len(values) != 9 or any(not math.isfinite(v) for v in values): raise ValueError('Invalid source 3D transform')
    spans = [bounds[i + 3] - bounds[i] for i in range(3)]
    if min(spans) <= 1e-8: raise ValueError('STEP has a degenerate bounding box')
    scale = [values[0] * 0.0254 / spans[0], values[1] * 0.0254 / spans[1], values[2] * 0.0254 / spans[2] if values[2] else 1.0]
    if min(scale) <= 0 or max(scale) > 1000: raise ValueError('Invalid source model size')
    # sizeZ=0 keeps the STEP height; it must never flatten the model.
    origin = [(bounds[0] + bounds[3]) / 2, (bounds[1] + bounds[4]) / 2, bounds[2]]
    z, x, y = map(math.radians, values[3:6])
    rz = [[math.cos(z), -math.sin(z), 0], [math.sin(z), math.cos(z), 0], [0, 0, 1]]
    rx = [[1, 0, 0], [0, math.cos(x), -math.sin(x)], [0, math.sin(x), math.cos(x)]]
    ry = [[math.cos(y), 0, math.sin(y)], [0, 1, 0], [-math.sin(y), 0, math.cos(y)]]
    rotation = multiply(multiply(rz, rx), ry)
    matrix = [[rotation[i][j] * scale[j] for j in range(3)] for i in range(3)]
    offset = [values[i + 6] * 0.0254 - v for i, v in enumerate(apply(matrix, origin))]
    return matrix, offset, {'source_bbox_mm': bounds, 'scale_xyz': scale, 'normalized_origin_mm': origin, 'rotation_order': 'ZXY', 'height_zero_policy': 'preserve source STEP height', 'matrix': matrix, 'translation_mm': offset}

def normalize(request):
    from OCP.Bnd import Bnd_Box
    from OCP.BRepBndLib import BRepBndLib
    from OCP.BRepBuilderAPI import BRepBuilderAPI_GTransform
    from OCP.BRepGProp import BRepGProp
    from OCP.GProp import GProp_GProps
    from OCP.gp import gp_GTrsf, gp_Mat, gp_XYZ
    from OCP.STEPCAFControl import STEPCAFControl_Reader, STEPCAFControl_Writer
    from OCP.IFSelect import IFSelect_RetDone
    from OCP.TDocStd import TDocStd_Document
    from OCP.TCollection import TCollection_ExtendedString
    from OCP.TDF import TDF_LabelSequence
    from OCP.XCAFDoc import XCAFDoc_DocumentTool, XCAFDoc_ColorGen, XCAFDoc_ColorSurf
    from OCP.TopExp import TopExp_Explorer
    from OCP.TopAbs import TopAbs_SOLID, TopAbs_FACE, TopAbs_REVERSED
    from OCP.TopoDS import TopoDS
    from OCP.Quantity import Quantity_Color
    from OCP.BRepMesh import BRepMesh_IncrementalMesh
    from OCP.BRep import BRep_Tool
    from OCP.TopLoc import TopLoc_Location
    document = TDocStd_Document(TCollection_ExtendedString('BinXCAF'))
    reader = STEPCAFControl_Reader()
    reader.SetColorMode(True)
    if reader.ReadFile(request['step']) != IFSelect_RetDone or not reader.Transfer(document): raise ValueError('Cannot parse STEP geometry')
    shapes = XCAFDoc_DocumentTool.ShapeTool_s(document.Main())
    colors = XCAFDoc_DocumentTool.ColorTool_s(document.Main())
    labels = TDF_LabelSequence()
    shapes.GetFreeShapes(labels)
    if not labels.Length(): raise ValueError('STEP contains no shapes')
    bounds = Bnd_Box()
    bounds.SetGap(0)
    originals = [shapes.GetShape_s(labels.Value(i)) for i in range(1, labels.Length() + 1)]
    for shape in originals: BRepBndLib.AddOptimal_s(shape, bounds, False, False)
    matrix, offset, report = source_matrix(list(bounds.Get()), request['transform'])
    transform = gp_GTrsf(gp_Mat(*[v for row in matrix for v in row]), gp_XYZ(*offset))
    output = TDocStd_Document(TCollection_ExtendedString('BinXCAF'))
    out_shapes = XCAFDoc_DocumentTool.ShapeTool_s(output.Main())
    out_colors = XCAFDoc_DocumentTool.ColorTool_s(output.Main())
    vertices, faces, palette = [], [], set()
    input_volume, output_volume, solids = 0.0, 0.0, 0
    def get_color(shape):
        color = Quantity_Color()
        for method in (colors.GetInstanceColor, colors.GetColor):
            for mode in (XCAFDoc_ColorSurf, XCAFDoc_ColorGen):
                if method(shape, mode, color): return color
        return None
    def rgb(color):
        if color is None: return (175, 179, 186)
        return tuple(round(255 * (12.92 * x if x <= 0.0031308 else 1.055 * x ** (1 / 2.4) - 0.055)) for x in (color.Red(), color.Green(), color.Blue()))
    for shape in originals:
        builder = BRepBuilderAPI_GTransform(shape, transform, True)
        result = builder.Shape()
        if result.IsNull(): raise ValueError('CAD transform produced no geometry')
        out_shapes.AddShape(result, False)
        properties = GProp_GProps()
        BRepGProp.VolumeProperties_s(shape, properties, 1e-9)
        input_volume += properties.Mass()
        BRepGProp.VolumeProperties_s(result, properties, 1e-9)
        output_volume += properties.Mass()
        root_color = get_color(shape)
        if root_color is not None: out_colors.SetColor(result, root_color, XCAFDoc_ColorSurf)
        exp = TopExp_Explorer(shape, TopAbs_SOLID)
        while exp.More():
            old_solid = exp.Current()
            solid_color = get_color(old_solid) or root_color
            solids += 1
            if solid_color is not None: out_colors.SetColor(builder.ModifiedShape(old_solid), solid_color, XCAFDoc_ColorSurf)
            exp.Next()
        BRepMesh_IncrementalMesh(result, 0.04, False, 0.35, True)
        exp = TopExp_Explorer(shape, TopAbs_FACE)
        while exp.More():
            old_face = exp.Current()
            color = get_color(old_face) or root_color
            new_face = TopoDS.Face_s(builder.ModifiedShape(old_face))
            if color is not None: out_colors.SetColor(new_face, color, XCAFDoc_ColorSurf)
            palette.add(rgb(color))
            location = TopLoc_Location()
            triangulation = BRep_Tool.Triangulation_s(new_face, location)
            if triangulation is not None:
                start = len(vertices)
                for i in range(1, triangulation.NbNodes() + 1):
                    point = triangulation.Node(i).Transformed(location.Transformation())
                    vertices.append([point.X(), point.Y(), point.Z()])
                for i in range(1, triangulation.NbTriangles() + 1):
                    tri = [start + j - 1 for j in triangulation.Triangle(i).Get()]
                    if new_face.Orientation() == TopAbs_REVERSED: tri.reverse()
                    faces.append([tri, rgb(color)])
            exp.Next()
    if not vertices or not faces: raise ValueError('Normalized STEP could not be triangulated')
    determinant = abs(matrix[0][0] * (matrix[1][1] * matrix[2][2] - matrix[1][2] * matrix[2][1]) - matrix[0][1] * (matrix[1][0] * matrix[2][2] - matrix[1][2] * matrix[2][0]) + matrix[0][2] * (matrix[1][0] * matrix[2][1] - matrix[1][1] * matrix[2][0]))
    if abs(output_volume - input_volume * determinant) > max(1e-5, abs(input_volume * determinant) * 1e-5): raise ValueError(f'CAD transformation changed volume unexpectedly: source={input_volume}, result={output_volume}, determinant={determinant}')
    writer = STEPCAFControl_Writer()
    writer.SetColorMode(True)
    if not writer.Transfer(output) or writer.Write(request['output_step']) != IFSelect_RetDone: raise ValueError('Cannot write normalized STEP')
    step_path = pathlib.Path(request['output_step'])
    source_stamp = re.search(rb"FILE_NAME\('[^']*','([^']*)'", pathlib.Path(request['step']).read_bytes())
    stamp = source_stamp[1] if source_stamp else b'1970-01-01T00:00:00'
    stable_step, changed = re.subn(rb"(FILE_NAME\('[^']*',')[^']*(')", lambda match: match[1] + stamp + match[2], step_path.read_bytes(), count=1)
    if changed != 1: raise ValueError('Normalized STEP header is invalid')
    step_path.write_bytes(stable_step)
    pathlib.Path(request['output_mesh']).write_text(json.dumps({'vertices': vertices, 'faces': faces}), encoding='utf-8')
    return {**report, 'source_volume_mm3': input_volume, 'normalized_volume_mm3': output_volume, 'source_solid_count': solids, 'preview_vertices': len(vertices), 'preview_triangles': len(faces), 'preview_colors': len(palette), 'normalized_step': request['output_step'], 'preview_mesh': request['output_mesh']}

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--request', required=True)
    parser.add_argument('--result', required=True)
    args = parser.parse_args()
    try: result = {'status': 'ok', **normalize(json.loads(pathlib.Path(args.request).read_text(encoding='utf-8')))}
    except Exception as error: result = {'status': 'error', 'error': str(error)}
    pathlib.Path(args.result).write_text(json.dumps(result, indent=2), encoding='utf-8')
    return 0 if result['status'] == 'ok' else 1

if __name__ == '__main__': sys.exit(main())
