"""Create zero-component AD source libraries, retaining native document scaffolding."""
import pathlib, struct
import bridge, main_library

PROJECT = '[Design]\nVersion=1.0\nOutputPath=Compiled\n\n[Document1]\nDocumentPath=MyParts.SchLib\n\n[Document2]\nDocumentPath=MyFootprints.PcbLib\n\n[Generic_EDE]\nOutputDir=Compiled\n'

def create_empty(source, destination):
    source, destination = pathlib.Path(source), pathlib.Path(destination)
    if destination.exists() and any(destination.iterdir()): raise FileExistsError('Empty-library output must be an empty directory')
    destination.mkdir(parents=True, exist_ok=True)
    original_sch, original_pcb = bridge.read_streams(source / 'MyParts.SchLib'), bridge.read_streams(source / 'MyFootprints.PcbLib')
    sch = {key: data for key, data in original_sch.items() if key[0] in ('FileHeader', 'Storage')}
    main_library.update_sch_index(sch)
    pcb = {key: data for key, data in original_pcb.items() if key[0] in ('FileHeader', 'Library', 'SectionKeys') and not (len(key) == 3 and key[1] == 'Models' and key[2].isdigit())}
    library_data = pcb[('Library', 'Data')]
    size = (struct.unpack_from('<I', library_data)[0] & 0xffffff) + 4
    values = bridge.fields(library_data[4:size])
    values['FILENAME'] = 'MyFootprints.PcbLib'
    values['CURRENT2D3DVIEWSTATE'] = '2D'
    pcb[('Library', 'Data')] = bridge.params(values) + struct.pack('<I', 0)
    pcb[('Library', 'Models', 'Header')] = struct.pack('<I', 0)
    pcb[('Library', 'Models', 'Data')] = b''
    main_library.update_pcb_index(pcb)
    bridge.write_verified(destination / 'MyParts.SchLib', sch)
    bridge.write_verified(destination / 'MyFootprints.PcbLib', pcb)
    (destination / 'MyLibrary.LibPkg').write_text(PROJECT, encoding='utf-8-sig')
    assert main_library.components(sch) == [] and main_library.footprints(pcb) == {}
    assert pcb[('Library', 'Models', 'Data')] == b'' and not any(key[-1].isdigit() for key in pcb if len(key) == 3 and key[1] == 'Models')
    return {'symbols': 0, 'footprints': 0, 'embedded_models': 0}
