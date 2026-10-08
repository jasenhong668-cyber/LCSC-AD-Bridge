import json, os, pathlib, shutil, subprocess, types
import pytest
import bridge, empty_library, portable_setup as setup

ROOT = pathlib.Path(__file__).resolve().parents[1]
MASTER = ROOT / 'assets/empty-library'

@pytest.fixture
def kit(tmp_path):
    source = tmp_path / 'Kit'
    runtime = source / 'runtime'
    seed = source / 'starter-library'
    runtime.mkdir(parents=True)
    for name in ('LCSC_AD_Bridge.exe', 'LCSC_AD_Launcher.exe', 'GeometryWorker.exe', 'vendor/lceda/lceda.exe'):
        path = runtime / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b'TEST PAYLOAD ' + name.encode())
    empty_library.create_empty(MASTER, seed)
    files = {str(p.relative_to(source)).replace('\\', '/'): setup.digest(p.read_bytes()) for p in source.rglob('*') if p.is_file()}
    (source / 'package-manifest.json').write_text(json.dumps({'product': setup.PRODUCT, 'version': setup.VERSION, 'package_id': 'TEST', 'seed_components': 0, 'files': files}), encoding='utf-8')
    return source

@pytest.fixture
def generated_scripts(monkeypatch):
    def run(args, **kwargs):
        with monkeypatch.context() as context:
            context.setattr(bridge, 'ROOT', pathlib.Path(args[0]).parent)
            context.setattr(bridge, 'sys', types.SimpleNamespace(frozen=True))
            bridge.install_script(args[3], args[5])
        return subprocess.CompletedProcess(args, 0, b'{}', b'')
    monkeypatch.setattr(setup.subprocess, 'run', run)

def profile(root):
    directory = root / 'ADProfile'
    directory.mkdir(parents=True, exist_ok=True)
    return {'id': 'SIMULATION', 'path': str(directory), 'version': '24.5.1', 'exe': ''}

@pytest.mark.parametrize('codec', ['cp936', 'utf-8', 'utf-8-sig', 'cp1252'])
def test_menu_preserves_original_settings_and_is_idempotent(tmp_path, codec):
    original = "PL UserCommand Command='PCB:Select' Caption='User custom' End\r\nTree Other Caption='Other' TopLevel=True End\r\n".encode(codec)
    menu, caption = setup.menu_bytes(original, tmp_path / 'Runtime', codec)
    again, _ = setup.menu_bytes(menu, tmp_path / 'Runtime', codec)
    assert again == menu
    assert setup.remove_menu_bytes(menu, codec) == original
    assert caption == ('LCSC Parts' if codec == 'cp1252' else '立创元件')
    assert menu.decode('utf-8-sig' if codec == 'utf-8-sig' else codec).count('Insertion LCSCBridgeV05_') == 6

def test_menu_migrates_only_known_legacy_entry(tmp_path):
    original = "PL PLMyLibrary:ImportV05 Command='ScriptingSystem:RunScript' Params='ProjectName=Old|ProcName=MyLibraryMaster.pas>ImportToMyLibrary' End\nTree SchCustom Caption='[Custom]' TopLevel=True\n    Link SchCustom_MyLibraryV05 PLID='PLMyLibrary:ImportV05' End\n    Link UserLink PLID='UserPL' End\nEnd\nInsertion MNSchLibMenu_InsertMyLibraryV05 TargetID='MNSchLibMenu'\n    Link MNSchLibMenu_MyLibraryV05 PLID='PLMyLibrary:ImportV05' End\nEnd\n"
    menu, _ = setup.menu_bytes(original.encode('cp936'), tmp_path / 'New Runtime', 'cp936')
    assert b'PLMyLibrary:ImportV05' not in menu
    assert b'Link UserLink' in menu
    assert b'New Runtime' in menu

def test_menu_refuses_ambiguous_legacy_and_unsupported_encoding(tmp_path):
    with pytest.raises(ValueError, match='同名'): setup.menu_bytes(b"PL PLMyLibrary:ImportV05 Command='Another:Process' End\n", tmp_path, 'cp936')
    with pytest.raises(ValueError, match='UTF-16'): setup.menu_bytes('Tree Test End'.encode('utf-16'), tmp_path, 'cp936')
    with pytest.raises(ValueError, match='英文路径'): setup.menu_bytes(b'', tmp_path / '中文', 'cp1252')
    with pytest.raises(ValueError, match='单引号'): setup.menu_bytes(b'', tmp_path / "User's folder", 'cp936')

def test_menu_removes_native_saved_orphan_legacy_links(tmp_path):
    original = "Tree SchCustom Caption='[Custom]' TopLevel='True' \r\n    Link SchCustom_MyLibraryV05 PLID='PLMyLibrary:ImportV05'      End\r\n    Link Keep PLID='UserCommand'      End\r\nEnd\r\nInsertion MNSchLibMenu_InsertMyLibraryV05 TargetID='MNSchLibMenu' InsertType='After' RefID0='Help' \r\n    Link MNSchLibMenu_MyLibraryV05 PLID='PLMyLibrary:ImportV05'      End\r\nEnd\r\n"
    menu, _ = setup.menu_bytes(original.encode('cp936'), tmp_path / 'Runtime', 'cp936')
    assert b'PLMyLibrary:ImportV05' not in menu and b'InsertMyLibraryV05' not in menu
    assert b"Link Keep PLID='UserCommand'      End" in menu
    assert setup.menu_bytes(menu, tmp_path / 'Runtime', 'cp936')[0] == menu

def test_menu_reinstall_after_ad_native_save_removes_unmarked_owned_items(tmp_path):
    original = b"PL UserCommand Command='PCB:Select' Caption='Keep' End\r\n"
    native = "PL PLLCSCBridgeV05:Import Command='ScriptingSystem:RunScript' Params='ProjectName=Old|ProcName=MyLibraryMaster.pas>ImportToMyLibrary' Caption='立创元件' DefaultChecked=0  End\r\n"
    for name, reference in setup.MENUS: native += f"Insertion LCSCBridgeV05_{name} TargetID='{name}' InsertType='After' RefID0='{reference}' \r\n    Link LCSCBridgeV05_{name}_Command PLID='PLLCSCBridgeV05:Import'      End\r\nEnd\r\n"
    fixed, _ = setup.menu_bytes(original + native.encode('cp936'), tmp_path / 'New Runtime', 'cp936')
    assert fixed.count(b'PL PLLCSCBridgeV05:Import ') == 1 and fixed.count(b'Insertion LCSCBridgeV05_') == 6
    assert b'ProjectName=Old' not in fixed and original in fixed
    assert setup.remove_menu_bytes(original + native.encode('cp936'), 'cp936') == original

def test_registry_registration_does_not_overwrite_other_libraries(tmp_path):
    values = {'Library0': [1, 'Other.IntLib'], 'LibraryActivated0': [1, '0'], 'LibraryRelativePath1': [1, 'OrphanMetadata']}
    wanted = setup.library_registration(values, tmp_path / 'MyLibrary.IntLib')
    assert set(wanted) == {'Library2', 'LibraryRelativePath2', 'LibraryActivated2', 'LibraryViewSettings2'}
    combined = {**values, **wanted}
    assert setup.library_registration(combined, tmp_path / 'MyLibrary.IntLib') == {}
    assert values['Library0'][1] == 'Other.IntLib'

def test_transaction_rolls_back_files_and_timestamps(tmp_path):
    target = tmp_path / 'files'
    target.mkdir()
    existing = target / 'original.txt'
    existing.write_bytes(b'ORIGINAL')
    os.utime(existing, ns=(1000000000000000000, 1000000000000000000))
    tx = setup.Transaction([target], tmp_path / 'backup')
    tx.write(existing, b'CHANGED')
    tx.write(target / 'newdir/new.txt', b'NEW')
    tx.rollback()
    assert existing.read_bytes() == b'ORIGINAL'
    assert existing.stat().st_mtime_ns == 1000000000000000000
    assert not (target / 'newdir').exists()

def test_transaction_refuses_paths_outside_scope_and_preserves_external_changes(tmp_path):
    target = tmp_path / 'files'
    target.mkdir()
    tx = setup.Transaction([target], tmp_path / 'backup')
    with pytest.raises(ValueError, match='超出'): tx.write(tmp_path / 'outside.txt', b'BAD')
    file = target / 'original.txt'
    tx.write(file, b'INSTALLED')
    file.write_bytes(b'OTHER PROGRAM EDIT')
    with pytest.raises(RuntimeError, match='外部修改'): tx.rollback()
    assert file.read_bytes() == b'OTHER PROGRAM EDIT'

def test_package_corruption_is_rejected_before_install(kit, tmp_path):
    (kit / 'runtime/GeometryWorker.exe').write_bytes(b'CORRUPTED')
    with pytest.raises(ValueError, match='校验失败'): setup.verify_package(kit)

def test_manifest_path_escape_is_rejected(kit):
    manifest = json.loads((kit / 'package-manifest.json').read_text())
    manifest['files']['../escape.txt'] = 'bad'
    (kit / 'package-manifest.json').write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match='校验失败'): setup.verify_package(kit)

def test_install_relocates_paths_preserves_user_library_and_saved_project(kit, tmp_path, generated_scripts):
    isolated = tmp_path / '新用户 中文目录 with spaces'
    p = profile(isolated)
    original_menu = b"PL Existing Command='PCB:Select' Caption='Existing' End\r\n"
    menu = pathlib.Path(p['path']) / 'DXP.RCS'
    menu.write_bytes(original_menu)
    runtime, library = isolated / 'Runtime', isolated / 'MyLibrary'
    first = setup.install_package(kit, runtime, library, p, simulation_root=isolated)
    assert first['seed_copied'] and first['components'] == 0 and not first['library_registered']
    config = json.loads((runtime / 'launcher-config.json').read_text(encoding='utf-8'))
    assert pathlib.Path(config['backend_root']) == runtime
    assert pathlib.Path(config['my_library']) == library
    script = (runtime / 'AD/MyLibraryMaster.pas').read_text(encoding=bridge.ad_script_encoding())
    assert str(library) in script and str(runtime / 'LCSC_AD_Launcher.exe') in script
    assert 'E:\\codex\\1004' not in script and 'D:\\AltiumDocuments\\MyLibrary' not in script
    streams = bridge.read_streams(library / 'MyParts.SchLib')
    streams[('UserNotes',)] = b'KEEP MY OWN NOTES'
    staged = library / 'MyParts.edited.SchLib'
    bridge.write_verified(staged, streams)
    staged.replace(library / 'MyParts.SchLib')
    library_bytes = {n: (library / n).read_bytes() for n in setup.CORE_FILES}
    project = runtime / 'AD/MyLibraryMaster.PrjScr'
    project.write_text(project.read_text(encoding='utf-8-sig') + '\n[Generic_ScriptingSystem]\nStartProcName=MyLibraryMaster.pas>ImportToMyLibrary\n', encoding='utf-8-sig')
    os.utime(project, ns=(1000000000000000000, 1000000000000000000))
    project_before = project.read_bytes(), project.stat().st_mtime_ns
    menu.write_bytes(menu.read_bytes() + b"PL ExtraUserCommand Command='PCB:Select' Caption='Extra' End\r\n")
    second = setup.install_package(kit, runtime, library, p, simulation_root=isolated)
    assert not second['seed_copied']
    assert library_bytes == {n: (library / n).read_bytes() for n in setup.CORE_FILES}
    assert project_before == (project.read_bytes(), project.stat().st_mtime_ns)
    assert b'Existing' in menu.read_bytes() and b'ExtraUserCommand' in menu.read_bytes()
    assert menu.read_bytes().count(b'Insertion LCSCBridgeV05_') == 6
    values = setup.JsonRegistry(isolated / 'mock-libraries.json').read()
    assert not values

def test_installer_rejects_launcher_path_mismatch_and_rolls_back(kit, tmp_path, monkeypatch):
    isolated = tmp_path / 'WrongGeneratedPath'
    p = profile(isolated)
    menu = pathlib.Path(p['path']) / 'DXP.RCS'
    menu.write_bytes(b'Tree Keep End\n')
    def run(args, **kwargs):
        with monkeypatch.context() as context:
            context.setattr(bridge, 'ROOT', pathlib.Path(args[0]).parent)
            context.setattr(bridge, 'sys', types.SimpleNamespace(frozen=True))
            bridge.install_script(args[3], args[5])
        script = pathlib.Path(args[3]) / 'MyLibraryMaster.pas'
        text = script.read_text(encoding=bridge.ad_script_encoding()).replace('LCSC_AD_Launcher.exe', 'Missing.exe')
        script.write_text(text, encoding=bridge.ad_script_encoding())
        return subprocess.CompletedProcess(args, 0, b'{}', b'')
    monkeypatch.setattr(setup.subprocess, 'run', run)
    with pytest.raises(RuntimeError, match='脚本路径校验失败'): setup.install_package(kit, isolated / 'Runtime', isolated / 'MyLibrary', p, simulation_root=isolated)
    assert menu.read_bytes() == b'Tree Keep End\n'
    assert not (isolated / 'Runtime').exists() and not (isolated / 'MyLibrary').exists()

@pytest.mark.parametrize('stage', ['files', 'menu', 'registry'])
def test_failed_install_restores_menu_registry_and_new_files(kit, tmp_path, generated_scripts, stage):
    isolated = tmp_path / 'Rollback'
    p = profile(isolated)
    menu = pathlib.Path(p['path']) / 'DXP.RCS'
    original_menu = b'Tree Original TopLevel=True End\r\n'
    menu.write_bytes(original_menu)
    registry = setup.JsonRegistry(isolated / 'mock-libraries.json')
    registry.apply({'Library0': [1, 'KEEP.IntLib'], 'LibraryActivated0': [1, '1']})
    before = registry.read()
    library = isolated / 'MyLibrary'
    preserved = {}
    if stage == 'registry':
        for name in (*setup.CORE_FILES, 'Compiled/MyLibrary.IntLib'):
            path = library / name
            path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(MASTER / name, path)
            preserved[name] = path.read_bytes()
    with pytest.raises(RuntimeError, match='已恢复'): setup.install_package(kit, isolated / 'Runtime', isolated / 'MyLibrary', p, simulation_root=isolated, fail_stage=stage)
    assert menu.read_bytes() == original_menu
    assert registry.read() == before
    assert not (isolated / 'Runtime').exists()
    if preserved: assert preserved == {name: (library / name).read_bytes() for name in preserved}
    else: assert not library.exists()

def test_first_component_import_into_empty_libraries_and_duplicate_does_not_grow(tmp_path):
    reports = list((ROOT / 'tests/fixtures/C7365889').glob('*/binding-report.json'))
    assert reports, 'A verified real converted component is required for the empty-library integration test'
    report = json.loads(max(reports, key=lambda p: p.stat().st_mtime).read_text(encoding='utf-8'))
    output = tmp_path / 'FirstImport'
    empty_library.create_empty(MASTER, output)
    assert setup.inspect_library(output) == []
    result = __import__('main_library').import_master(report, output)
    assert result['expected_components'] == 1 and result['new_part']
    bindings = setup.inspect_library(output)
    assert len(bindings) == 1 and bindings[0]['embedded_step']
    again = __import__('main_library').import_master(report, output)
    assert again['expected_components'] == 1 and not again['new_part']
    assert len(setup.inspect_library(output)) == 1

def test_first_import_removes_exact_native_blank_placeholder(tmp_path):
    native = ROOT / 'qa/NativeEmptyReference'
    output = tmp_path / 'NativeBlank'
    shutil.copytree(native, output)
    sch, pcb = bridge.read_streams(output / 'MyParts.SchLib'), bridge.read_streams(output / 'MyFootprints.PcbLib')
    library = __import__('main_library')
    assert library.native_empty_placeholder(sch, pcb) == ('Component_1', 'Data')
    assert setup.inspect_library(output) == []
    report_path = max((ROOT / 'tests/fixtures/C7365889').glob('*/binding-report.json'), key=lambda p: p.stat().st_mtime)
    result = library.import_master(json.loads(report_path.read_text(encoding='utf-8')), output)
    assert result['expected_components'] == 1
    assert ('Component_1', 'Data') not in bridge.read_streams(output / 'MyParts.SchLib')
    assert len(setup.inspect_library(output)) == 1

@pytest.mark.parametrize('change', ['drawing', 'comment', 'renamed'])
def test_native_placeholder_detection_never_removes_user_work(tmp_path, change):
    sch = bridge.read_streams(ROOT / 'qa/NativeEmptyReference/MyParts.SchLib')
    pcb = bridge.read_streams(ROOT / 'qa/NativeEmptyReference/MyFootprints.PcbLib')
    key = ('Component_1', 'Data')
    if change == 'drawing': sch[key] += bridge.params({'RECORD': '14', 'LOCATION.X': '10', 'LOCATION.Y': '10'})
    else:
        revised = bytearray()
        for flag, payload, values in bridge.records(sch[key]):
            if change == 'comment' and values.get('NAME') == 'Comment':
                values['TEXT'] = 'My custom note'
                revised += bridge.params(values)
            elif change == 'renamed' and values.get('RECORD') == '1':
                values['LIBREFERENCE'] = 'MyPart'
                revised += bridge.params(values)
            else: revised += bridge.block(payload, flag)
        sch[key] = bytes(revised)
    assert __import__('main_library').native_empty_placeholder(sch, pcb) is None
    with pytest.raises(ValueError, match='default PCB footprint'): __import__('main_library').validate(sch, pcb)

def test_no_mutations_while_altium_is_running(kit, tmp_path, monkeypatch):
    isolated = tmp_path / 'LiveBlocked'
    p = profile(isolated)
    p['id'] = 'Altium Designer {00000000-0000-0000-0000-000000000000}'
    path = pathlib.Path(p['path']).with_name(p['id'])
    pathlib.Path(p['path']).rename(path)
    p['path'] = str(path)
    executable = isolated / 'X2.EXE'
    executable.write_bytes(b'AD')
    p['exe'] = str(executable)
    monkeypatch.setattr(setup, 'ad_is_running', lambda: True)
    with pytest.raises(ValueError, match='完全退出'): setup.install_package(kit, isolated / 'Runtime', isolated / 'MyLibrary', p)
    assert not (isolated / 'Runtime').exists() and not (isolated / 'MyLibrary').exists()

def test_nonempty_unowned_runtime_and_incomplete_library_are_not_overwritten(kit, tmp_path, generated_scripts):
    isolated = tmp_path / 'Protected'
    p = profile(isolated)
    runtime, library = isolated / 'Runtime', isolated / 'MyLibrary'
    runtime.mkdir()
    file = runtime / 'user.txt'
    file.write_bytes(b'USER FILE')
    with pytest.raises(ValueError, match='不会覆盖'): setup.install_package(kit, runtime, library, p, simulation_root=isolated)
    assert file.read_bytes() == b'USER FILE'
    library.mkdir()
    user = library / 'MyParts.SchLib'
    user.write_bytes(b'USER LIBRARY')
    with pytest.raises(ValueError, match='三个文件'): setup.install_package(kit, isolated / 'OtherRuntime', library, p, simulation_root=isolated)
    assert user.read_bytes() == b'USER LIBRARY'
