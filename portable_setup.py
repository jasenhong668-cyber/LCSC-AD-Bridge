"""Per-user deployment of the verified 0.5 importer; no hard-coded computer paths."""
import argparse, ctypes, datetime, hashlib, io, json, os, pathlib, re, subprocess, sys, uuid, zlib
import olefile
import bridge, main_library

PRODUCT, VERSION = 'LCSC_AD_Bridge', '0.5.0'
BEGIN, END = '// BEGIN LCSC_AD_BRIDGE_INSTALL_V05', '// END LCSC_AD_BRIDGE_INSTALL_V05'
PROFILE_NAME = re.compile(r'Altium Designer \{[0-9A-Fa-f-]{36}\}')
MENUS = (('MNSchematicMenu', 'MNSchematic_Help10'), ('MNSchLibMenu', 'MNSchematic_SchLibMenuHelp10'), ('MNPCBMenu', 'MNPCB_Help10'), ('MNPCBLibMenu', 'MNPCBLib_Help10'), ('MNPCB3DRMenu', 'MNPCB3DR_Help10'), ('MNNoDocument', 'MNNoDocument_Help'))
CORE_FILES = ('MyParts.SchLib', 'MyFootprints.PcbLib', 'MyLibrary.LibPkg')

def digest(data): return hashlib.sha256(data).hexdigest()
def inside(path, root): return pathlib.Path(path).resolve().is_relative_to(pathlib.Path(root).resolve())
def system_encoding(): return 'utf-8' if os.name != 'nt' else ('utf-8' if ctypes.windll.kernel32.GetACP() == 65001 else f'cp{ctypes.windll.kernel32.GetACP()}')
def package_root(): return pathlib.Path(sys.executable).parent if getattr(sys, 'frozen', False) else pathlib.Path(__file__).resolve().parent

def ad_is_running():
    if os.name != 'nt': return False
    result = subprocess.run(['tasklist', '/FI', 'IMAGENAME eq X2.EXE', '/FO', 'CSV', '/NH'], capture_output=True, creationflags=subprocess.CREATE_NO_WINDOW, check=True)
    return bool(re.search(rb'"X2\.EXE"\s*,', result.stdout, re.I))

def discover_profiles():
    if os.name != 'nt': return []
    import winreg
    found = {}
    roaming = pathlib.Path(os.environ['APPDATA']) / 'Altium'
    for hive in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
        for view in (winreg.KEY_WOW64_64KEY, winreg.KEY_WOW64_32KEY):
            try: base = winreg.OpenKey(hive, r'SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall', 0, winreg.KEY_READ | view)
            except OSError: continue
            with base:
                for index in range(winreg.QueryInfoKey(base)[0]):
                    name = winreg.EnumKey(base, index)
                    if not PROFILE_NAME.fullmatch(name): continue
                    with winreg.OpenKey(base, name) as key:
                        values = {}
                        for field in ('DisplayName', 'DisplayVersion', 'InstallLocation'):
                            try: values[field] = winreg.QueryValueEx(key, field)[0]
                            except OSError: values[field] = ''
                    if not str(values['DisplayVersion']).startswith('24.'): continue
                    profile = roaming / name
                    executable = pathlib.Path(values['InstallLocation']) / 'X2.EXE'
                    if not profile.is_dir() or not executable.is_file(): continue
                    found[name] = {'id': name, 'path': str(profile), 'version': values['DisplayVersion'], 'exe': str(executable), 'label': f"AD {values['DisplayVersion']} — {name}"}
    return sorted(found.values(), key=lambda p: p['label'])

def strip_importer_menu(text):
    text = re.sub(r'\r?\n' + re.escape(BEGIN) + r'\r?\n.*?' + re.escape(END) + r'\r?\n', '', text, flags=re.S)
    # AD's native save removes comments and canonicalizes whitespace and quoted enum values.
    for plid in ('PLMyLibrary:ImportV05', 'PLLCSCBridgeV05:Import'):
        pattern = r'^PL ' + re.escape(plid) + r'\b[^\r\n]*\bEnd[ \t]*\r?\n?'
        for match in list(re.finditer(pattern, text, re.M)):
            if "Command='ScriptingSystem:RunScript'" not in match[0] or 'MyLibraryMaster.pas>ImportToMyLibrary' not in match[0]: raise ValueError('同名旧菜单内容无法确认，未覆盖。请先在 AD 移除该旧入口。')
        text = re.sub(pattern, '', text, flags=re.M)
        if re.search(r'^PL ' + re.escape(plid) + r'\b', text, re.M): raise ValueError('同名旧菜单格式无法确认，未覆盖。')
    for name, _ in MENUS:
        for insertion, link, plid in ((name + '_InsertMyLibraryV05', name + '_MyLibraryV05', 'PLMyLibrary:ImportV05'), ('LCSCBridgeV05_' + name, 'LCSCBridgeV05_' + name + '_Command', 'PLLCSCBridgeV05:Import')):
            pattern = r'^Insertion ' + re.escape(insertion) + r'\b[^\r\n]*\r?\n[ \t]*Link ' + re.escape(link) + r"[ \t]+PLID='" + re.escape(plid) + r"'[ \t]+End[ \t]*\r?\nEnd[ \t]*\r?\n?"
            text = re.sub(pattern, '', text, flags=re.M)
            if re.search(r'^Insertion ' + re.escape(insertion) + r'\b', text, re.M): raise ValueError('同名旧菜单插入项无法确认，未覆盖。')
    return re.sub(r"^[ \t]*Link (?:SchCustom|PCBCustom|PCBLibCustom|SchLibCustom)_MyLibraryV05[ \t]+PLID='PLMyLibrary:ImportV05'[ \t]+End[ \t]*\r?\n?", '', text, flags=re.M)

def menu_bytes(original, runtime, encoding):
    if original.startswith((b'\xff\xfe', b'\xfe\xff')): raise ValueError('已有 DXP.RCS 使用 UTF-16。为避免本机已观察到的启动问题，请先用 AD 原生保存菜单，再安装。')
    codec = 'utf-8-sig' if original.startswith(b'\xef\xbb\xbf') else encoding
    text = strip_importer_menu(original.decode(codec))
    runtime = pathlib.Path(runtime).resolve()
    for value in (str(runtime),):
        if any(c in value for c in "'|\r\n"): raise ValueError('安装路径不能包含单引号、竖线或换行。')
    caption = '立创元件'
    try: caption.encode(codec)
    except UnicodeEncodeError: caption = 'LCSC Parts'
    parameters = f'ProjectName={runtime / "AD/MyLibraryMaster.PrjScr"}|ProcName=MyLibraryMaster.pas>ImportToMyLibrary'
    block = f"\r\n{BEGIN}\r\nPL PLLCSCBridgeV05:Import Command='ScriptingSystem:RunScript' Caption='{caption}' Description='LCSC importer v0.5' Params='{parameters}' Image='' Shortcut1='' Shortcut2='' End\r\n"
    for name, reference in MENUS: block += f"Insertion LCSCBridgeV05_{name} TargetID='{name}' InsertType=After RefID0='{reference}'\r\n    Link LCSCBridgeV05_{name}_Command PLID='PLLCSCBridgeV05:Import' End\r\nEnd\r\n"
    try: return (text + block + END + '\r\n').encode(codec), caption
    except UnicodeEncodeError as error: raise ValueError('当前 AD 菜单编码不支持所选路径，请选择英文路径。') from error

def remove_menu_bytes(original, encoding):
    codec = 'utf-8-sig' if original.startswith(b'\xef\xbb\xbf') else encoding
    text = original.decode(codec)
    return strip_importer_menu(text).encode(codec)

class Registry:
    def __init__(self, profile_id):
        import winreg
        self.api = winreg
        self.path = f'Software\\Altium\\{profile_id}\\DesignExplorer\\Preferences\\IntegratedLibrary\\Loaded Libraries'
    def read(self):
        result, w = {}, self.api
        try: key = w.OpenKey(w.HKEY_CURRENT_USER, self.path)
        except FileNotFoundError: return result
        with key:
            for i in range(w.QueryInfoKey(key)[1]):
                name, value, kind = w.EnumValue(key, i)
                result[name] = [kind, value]
        return result
    def apply(self, values):
        w = self.api
        with w.CreateKey(w.HKEY_CURRENT_USER, self.path) as key:
            for name, item in values.items():
                if item is not None: w.SetValueEx(key, name, 0, item[0], item[1])
                else:
                    try: w.DeleteValue(key, name)
                    except FileNotFoundError: pass

class JsonRegistry:
    """A file-backed registry exclusively for bounded installation simulations."""
    def __init__(self, path): self.path = pathlib.Path(path)
    def read(self): return json.loads(self.path.read_text(encoding='utf-8')) if self.path.exists() else {}
    def apply(self, values):
        data = self.read()
        for name, item in values.items():
            if item is None: data.pop(name, None)
            else: data[name] = item
        self.path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')

def library_registration(values, library):
    library = str(pathlib.Path(library).resolve())
    existing = [int(m[1]) for name, item in values.items() if (m := re.fullmatch(r'Library(\d+)', name)) and os.path.normcase(str(item[1])) == os.path.normcase(library)]
    slots = {int(m[1]) for name in values if (m := re.fullmatch(r'(?:Library|LibraryRelativePath|LibraryActivated|LibraryViewSettings)(\d+)', name))}
    index = existing[0] if existing else next(i for i in range(10000) if i not in slots)
    wanted = {f'Library{index}': [1, library], f'LibraryRelativePath{index}': [1, library], f'LibraryActivated{index}': [1, '1'], f'LibraryViewSettings{index}': [1, '']}
    return {name: item for name, item in wanted.items() if values.get(name) != item}

class Transaction:
    def __init__(self, roots, backup):
        self.roots, self.backup, self.files, self.directories = [pathlib.Path(p).resolve() for p in roots], pathlib.Path(backup), {}, []
        self.backup.mkdir(parents=True, exist_ok=False)
    def watch(self, path):
        path = pathlib.Path(path).resolve()
        if not any(inside(path, root) for root in self.roots): raise ValueError(f'路径超出安装范围：{path}')
        if path in self.files: return path
        saved = self.backup / str(len(self.files))
        if path.exists(): saved.write_bytes(path.read_bytes())
        stat = path.stat() if path.exists() else None
        self.files[path] = {'saved': saved if path.exists() else None, 'after': None, 'times': (stat.st_atime_ns, stat.st_mtime_ns) if stat else None}
        return path
    def parents(self, path):
        missing, parent = [], path.parent
        while not parent.exists():
            missing.append(parent)
            parent = parent.parent
        for directory in reversed(missing):
            directory.mkdir()
            self.directories.append(directory)
    def capture(self, path):
        path = pathlib.Path(path).resolve()
        self.files[path]['after'] = digest(path.read_bytes()) if path.exists() else None
    def write(self, path, data):
        path = pathlib.Path(path).resolve()
        if path.is_file() and path.read_bytes() == data: return
        self.watch(path)
        self.parents(path)
        temporary = path.with_name(path.name + '.install-' + uuid.uuid4().hex + '.tmp')
        try:
            temporary.write_bytes(data)
            temporary.replace(path)
        finally:
            if temporary.exists(): temporary.unlink()
        self.capture(path)
    def journal(self):
        data = {str(path): {'backup_file': str(item['saved']) if item['saved'] else None, 'installed_sha256': item['after']} for path, item in self.files.items()}
        (self.backup / 'files.json').write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
    def rollback(self):
        failures = []
        for path, item in reversed(list(self.files.items())):
            current = digest(path.read_bytes()) if path.exists() else None
            if current != item['after']:
                failures.append(str(path))
                continue
            if item['saved']:
                original = item['saved'].read_bytes()
                if current == digest(original): continue
                path.write_bytes(original)
                os.utime(path, ns=item['times'])
            elif path.exists(): path.unlink()
        for directory in reversed(self.directories):
            try: directory.rmdir()
            except OSError: pass
        self.journal()
        if failures: raise RuntimeError('以下文件在安装期间被外部修改，未覆盖；请从备份恢复：' + ', '.join(failures))

def verify_package(source):
    source = pathlib.Path(source).resolve()
    manifest = json.loads((source / 'package-manifest.json').read_text(encoding='utf-8'))
    if manifest.get('product') != PRODUCT or manifest.get('version') != VERSION: raise ValueError('资料包版本或产品标识不匹配。')
    for relative, expected in manifest['files'].items():
        path = (source / relative).resolve()
        if not inside(path, source) or not path.is_file() or digest(path.read_bytes()) != expected: raise ValueError(f'资料包校验失败：{relative}。请重新完整解压。')
    required = ('runtime/LCSC_AD_Bridge.exe', 'runtime/LCSC_AD_Launcher.exe', 'runtime/GeometryWorker.exe', 'runtime/vendor/lceda/lceda.exe', *(f'starter-library/{name}' for name in CORE_FILES))
    if any(name not in manifest['files'] for name in required): raise ValueError('资料包清单缺少必需文件。')
    if any(not (name.startswith('runtime/') or name.startswith('starter-library/')) for name in manifest['files']): raise ValueError('清单包含非运行程序或初始库文件。')
    return manifest

def bindings_signature(bindings):
    return {p['name']: {'footprint': p['footprint'], 'pin_map': p['pin_map'], 'step_sha256': sorted(m['step_sha256'] for m in p['embedded_step'])} for p in bindings}

def inspect_library(root):
    root = pathlib.Path(root)
    if any(not (root / name).is_file() for name in CORE_FILES): raise ValueError('主库目录须包含 MyParts.SchLib、MyFootprints.PcbLib 和 MyLibrary.LibPkg 三个文件。')
    bindings = main_library.validate(bridge.read_streams(root / CORE_FILES[0]), bridge.read_streams(root / CORE_FILES[1]))
    if any(p['model_file'] != 'MyFootprints.PcbLib' or p['pin_map'] != {n: n for n in p['pin_map']} for p in bindings): raise ValueError('主库的默认封装或逐引脚映射不符合当前导入机制，请先修复库。')
    text, documents = __import__('library_import').project_documents(root / CORE_FILES[2])
    if {p.resolve() for _, p in documents} != {(root / name).resolve() for name in CORE_FILES[:2]}: raise ValueError('库工程必须仅引用两份实际主源库。')
    if not re.search(r'^OutputPath=Compiled\s*$', text, re.M | re.I): raise ValueError('库工程 OutputPath 应设置为 Compiled，请在 AD 修改后保存。')
    compiled = root / 'Compiled/MyLibrary.IntLib'
    if compiled.exists():
        streams, nested = bridge.read_streams(compiled), []
        for kind in ('SchLib', 'PCBLib'):
            data = next(v for k, v in streams.items() if len(k) == 2 and k[0] == kind)
            if data[0] != 2: raise ValueError('集成库压缩格式不支持，请在 AD 2024 重新编译。')
            with olefile.OleFileIO(io.BytesIO(zlib.decompress(data[1:]))) as lib: nested.append({tuple(k): lib.openstream(k).read() for k in lib.listdir()})
        if bindings_signature(main_library.validate(*nested)) != bindings_signature(bindings): raise ValueError('现有 IntLib 与源库不一致，请先在 AD 编译主库工程。')
    return bindings

def install_package(source, runtime, library, profile, register=True, simulation_root=None, progress=None, fail_stage=None):
    source, runtime, library, profile_path = pathlib.Path(source).resolve(), pathlib.Path(runtime).resolve(), pathlib.Path(library).resolve(), pathlib.Path(profile['path']).resolve()
    notify = progress or (lambda message, percent: None)
    notify('校验资料包和安装环境', 5)
    manifest = verify_package(source)
    if not str(profile['version']).startswith('24.'): raise ValueError('此资料包面向 AD 2024，请选择 AD 24 用户设置。')
    if simulation_root:
        simulation_root = pathlib.Path(simulation_root).resolve()
        if any(not inside(p, simulation_root) for p in (runtime, library, profile_path)): raise ValueError('模拟安装必须全部位于指定测试目录。')
        registry = JsonRegistry(simulation_root / 'mock-libraries.json')
        backup_parent = simulation_root / 'backups'
    else:
        if not PROFILE_NAME.fullmatch(profile['id']) or profile_path.name != profile['id'] or not profile_path.is_dir() or not pathlib.Path(profile['exe']).is_file(): raise ValueError('AD 用户配置无效，请先启动一次 AD 2024，再退出安装。')
        if ad_is_running(): raise ValueError('请先保存文件并完全退出 Altium Designer，再安装。')
        registry = Registry(profile['id'])
        backup_parent = pathlib.Path(os.environ['LOCALAPPDATA']) / 'LCSC_AD_Bridge/InstallBackups'
    for p in (runtime, library):
        if any(c in str(p) for c in "'|\r\n"): raise ValueError('安装和主库路径不能包含单引号、竖线或换行。')
    if any(inside(a, b) or inside(b, a) for a, b in ((runtime, source), (library, source), (runtime, library), (profile_path, runtime), (profile_path, library))): raise ValueError('插件、主库、AD 设置和资料包目录必须彼此独立。')
    marker = runtime / '.lcsc-install.json'
    if runtime.exists() and any(runtime.iterdir()):
        if not marker.is_file() or json.loads(marker.read_text(encoding='utf-8')).get('product') != PRODUCT: raise ValueError('插件目标目录已有其他文件，请选择空目录；不会覆盖旧便携包或其他程序。')
    is_new = not library.exists() or not any(library.iterdir())
    bindings = inspect_library(source / 'starter-library' if is_new else library)
    menu_path = profile_path / 'DXP.RCS'
    menu, caption = menu_bytes(menu_path.read_bytes() if menu_path.exists() else b'', runtime, system_encoding())
    tx = Transaction((runtime, library, profile_path), backup_parent / (datetime.datetime.now().strftime('%Y%m%d_%H%M%S') + '_' + uuid.uuid4().hex[:8]))
    registry_before, registry_written = {}, {}
    try:
        notify('复制运行程序和依赖', 20)
        for relative in manifest['files']:
            if relative.startswith('runtime/'): tx.write(runtime / relative.removeprefix('runtime/'), (source / relative).read_bytes())
        if is_new:
            notify('创建空的原理图库和 PCB 封装库', 40)
            for relative in manifest['files']:
                if relative.startswith('starter-library/'): tx.write(library / relative.removeprefix('starter-library/'), (source / relative).read_bytes())
        if fail_stage == 'files': raise RuntimeError('Simulated file-stage failure')
        notify('生成本机脚本与路径配置', 55)
        external = [runtime / 'launcher-config.json', runtime / 'AD/AD_Menu_Command.txt'] + [runtime / ('AD/' + stem + suffix) for stem in ('LCSCBridge', 'LCSCBridge2024', 'MyLibraryMaster', 'MyLibraryImporter') for suffix in ('.pas', '.PrjScr')]
        for path in external:
            tx.watch(path)
            tx.parents(path)
        try:
            result = subprocess.run([str(runtime / 'LCSC_AD_Bridge.exe'), 'install-script', '--out', str(runtime / 'AD'), '--library', str(library)], capture_output=True, timeout=90, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            if result.returncode: raise RuntimeError('生成脚本失败：' + result.stderr.decode('utf-8', errors='replace')[-1000:])
        finally:
            for path in external: tx.capture(path)
        config = json.loads((runtime / 'launcher-config.json').read_text(encoding='utf-8'))
        if pathlib.Path(config['backend_root']).resolve() != runtime or pathlib.Path(config['my_library']).resolve() != library: raise RuntimeError('生成后的运行路径不匹配。')
        script = (runtime / 'AD/MyLibraryMaster.pas').read_text(encoding=bridge.ad_script_encoding())
        expected = "BridgeExecutable := '" + str(runtime / 'LCSC_AD_Launcher.exe').replace("'", "''") + "';"
        if expected not in script or not (runtime / 'LCSC_AD_Launcher.exe').is_file(): raise RuntimeError('启动器或 AD 脚本路径校验失败。')
        if not simulation_root and ad_is_running(): raise RuntimeError('安装期间 AD 被打开，已停止并恢复文件。')
        notify('合并顶部按钮，保留其他菜单', 75)
        tx.write(menu_path, menu)
        if fail_stage == 'menu': raise RuntimeError('Simulated menu-stage failure')
        compiled = library / 'Compiled/MyLibrary.IntLib'
        if register and compiled.is_file():
            values = registry.read()
            registry_written = library_registration(values, compiled)
            registry_before = {name: values.get(name) for name in registry_written}
            (tx.backup / 'registry-before.json').write_text(json.dumps({'key': str(registry.path), 'values': registry_before}, ensure_ascii=False, indent=2), encoding='utf-8')
            registry.apply(registry_written)
        if fail_stage == 'registry': raise RuntimeError('Simulated registry-stage failure')
        receipt = {'product': PRODUCT, 'version': VERSION, 'installed_at': datetime.datetime.now().astimezone().isoformat(), 'runtime': str(runtime), 'library': str(library), 'profile': profile, 'caption': caption, 'script_encoding': bridge.ad_script_encoding(), 'launcher_verified': True, 'seed_copied': is_new, 'components': len(bindings), 'library_registered': register and compiled.is_file(), 'backup': str(tx.backup), 'simulation': bool(simulation_root), 'package_id': manifest['package_id']}
        tx.write(marker, json.dumps(receipt, ensure_ascii=False, indent=2).encode('utf-8'))
        tx.write(runtime / '安装结果.json', json.dumps(receipt, ensure_ascii=False, indent=2).encode('utf-8'))
        tx.journal()
        notify('安装完成，启动 AD 后点击顶部按钮即可使用', 100)
        return receipt
    except Exception as error:
        rollback_error = None
        try:
            now = registry.read()
            if any(now.get(name) not in (item, registry_before[name]) for name, item in registry_written.items()): raise RuntimeError('注册库设置被外部修改，未自动覆盖。')
            registry.apply(registry_before)
        except Exception as failure: rollback_error = failure
        try: tx.rollback()
        except Exception as failure: rollback_error = rollback_error or failure
        raise RuntimeError(f'{error}\n' + ('恢复失败：' + str(rollback_error) if rollback_error else '本次文件和注册库修改已恢复。') + f'\n备份：{tx.backup}') from error

def documents_folder():
    if os.name == 'nt':
        buffer = ctypes.create_unicode_buffer(32768)
        if ctypes.windll.shell32.SHGetFolderPathW(None, 5, None, 0, buffer) == 0: return pathlib.Path(buffer.value)
    return pathlib.Path.home() / 'Documents'

def show_gui(source):
    import queue, threading, tkinter as tk
    from tkinter import filedialog, messagebox, ttk
    root = tk.Tk()
    root.title('立创元件导入 0.5 · 新电脑安装向导')
    root.geometry('800x590')
    root.minsize(750, 570)
    root.columnconfigure(1, weight=1)
    base = pathlib.Path.home() / 'AltiumDocuments'
    runtime, library, selection, register = tk.StringVar(value=str(base / 'LCSC_AD_Bridge_v0.5')), tk.StringVar(value=str(base / 'MyLibrary')), tk.StringVar(), tk.BooleanVar(value=True)
    profiles = discover_profiles()
    ttk.Label(root, text='安装后，在 AD 顶部点击“立创元件”即可搜索、预览并导入主库。', wraplength=740).grid(row=0, column=0, columnspan=3, sticky='w', padx=22, pady=(22, 15))
    def browse(variable):
        value = filedialog.askdirectory(parent=root, title='选择文件夹', initialdir=str(documents_folder()), mustexist=False)
        if value: variable.set(value)
    for row, label, variable in ((1, '插件安装目录', runtime), (2, '主元件库目录', library)):
        ttk.Label(root, text=label).grid(row=row, column=0, sticky='w', padx=22, pady=10)
        ttk.Entry(root, textvariable=variable).grid(row=row, column=1, sticky='ew', pady=10)
        ttk.Button(root, text='浏览…', command=lambda v=variable: browse(v)).grid(row=row, column=2, padx=15, pady=10)
    ttk.Label(root, text='AD 2024 用户设置').grid(row=3, column=0, sticky='w', padx=22, pady=10)
    combo = ttk.Combobox(root, textvariable=selection, values=[p['label'] for p in profiles], state='readonly')
    combo.grid(row=3, column=1, columnspan=2, sticky='ew', padx=(0, 22), pady=10)
    if len(profiles) == 1: selection.set(profiles[0]['label'])
    def refresh():
        profiles[:] = discover_profiles()
        combo['values'] = [p['label'] for p in profiles]
        selection.set(profiles[0]['label'] if len(profiles) == 1 else '')
    ttk.Button(root, text='重新检测 AD', command=refresh).grid(row=4, column=0, sticky='w', padx=22, pady=5)
    ttk.Label(root, text='先安装并启动一次 AD 2024，再保存文件、退出 AD 后安装。', wraplength=560).grid(row=4, column=1, columnspan=2, sticky='w', pady=5)
    ttk.Checkbutton(root, text='使用已有主库时，将其 IntLib 添加到 Components 面板', variable=register).grid(row=5, column=0, columnspan=3, sticky='w', padx=22, pady=10)
    manifest = verify_package(source)
    ttk.Label(root, text='全新安装从空库开始：符号、封装和 3D 模型均为 0。\n首次导入成功后，AD 自动生成并安装 MyLibrary.IntLib。\n已有完整主库会保留；无需 Python，搜索和下载需要联网。', wraplength=740).grid(row=6, column=0, columnspan=3, sticky='w', padx=22, pady=12)
    status, percent, messages = tk.StringVar(value='就绪。请选择安装目录和 AD 用户设置。'), tk.DoubleVar(value=0), queue.Queue()
    ttk.Progressbar(root, variable=percent, maximum=100).grid(row=7, column=0, columnspan=3, sticky='ew', padx=22, pady=8)
    ttk.Label(root, textvariable=status, wraplength=740).grid(row=8, column=0, columnspan=3, sticky='w', padx=22, pady=5)
    busy = [False]
    def install():
        selected = next((p for p in profiles if p['label'] == selection.get()), None)
        if selected is None:
            messagebox.showerror('未选择 AD 2024', '先启动一次 AD 2024 并退出，然后点击“重新检测 AD”并选择用户设置。', parent=root)
            return
        values = runtime.get(), library.get(), register.get()
        busy[0] = True
        install_button['state'] = 'disabled'
        status.set('开始校验资料包…')
        def worker():
            try:
                receipt = install_package(source, values[0], values[1], selected, values[2], progress=lambda text, value: messages.put(('progress', (text, value))))
                messages.put(('done', receipt))
            except Exception as error: messages.put(('error', str(error)))
        threading.Thread(target=worker, daemon=True).start()
    def poll():
        while not messages.empty():
            kind, data = messages.get_nowait()
            if kind == 'progress':
                status.set(data[0])
                percent.set(data[1])
            else:
                busy[0] = False
                install_button['state'] = 'normal'
                if kind == 'error':
                    status.set('安装未完成。')
                    messagebox.showerror('安装未完成', data, parent=root)
                else:
                    status.set(f"安装完成。启动 AD 后点击“{data['caption']}”。备份：{data['backup']}")
                    messagebox.showinfo('安装完成', f"顶部按钮：{data['caption']}\n主库元件：{data['components']} 个\n\n现在启动 AD 2024，打开原理图或 PCB，点击顶部按钮即可使用。", parent=root)
        root.after(100, poll)
    def close():
        if busy[0]: messagebox.showinfo('正在安装', '请等待安装完成或恢复完成后再关闭。', parent=root)
        else: root.destroy()
    install_button = ttk.Button(root, text='安装 / 修复配置', command=install)
    install_button.grid(row=9, column=1, sticky='e', padx=8, pady=20)
    ttk.Button(root, text='关闭', command=close).grid(row=9, column=2, padx=15, pady=20)
    root.protocol('WM_DELETE_WINDOW', close)
    root.update_idletasks()
    root.minsize(max(750, root.winfo_reqwidth()), max(570, root.winfo_reqheight()))
    root.after(100, poll)
    root.mainloop()

def main():
    parser = argparse.ArgumentParser(description='LCSC AD 0.5 deployment package')
    parser.add_argument('--package', type=pathlib.Path, default=package_root())
    parser.add_argument('--verify-package', action='store_true')
    parser.add_argument('--simulate', type=pathlib.Path, help='Bounded isolated deployment test; never modifies real AD settings')
    parser.add_argument('--report', type=pathlib.Path, help='Write CLI verification/simulation result as UTF-8 JSON')
    args = parser.parse_args()
    if args.verify_package:
        manifest = verify_package(args.package)
        result = {'status': 'ok', 'version': VERSION, 'files': len(manifest['files']), 'seed_components': manifest['seed_components']}
        if args.report: args.report.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
        if sys.stdout is not None: print(json.dumps(result))
        return 0
    if args.simulate:
        root = args.simulate.resolve()
        root.mkdir(parents=True, exist_ok=True)
        profile = root / 'ADProfile'
        profile.mkdir(exist_ok=True)
        receipt = install_package(args.package, root / 'Runtime', root / 'MyLibrary', {'id': 'SIMULATION', 'path': str(profile), 'version': '24.5.1', 'exe': ''}, simulation_root=root)
        if args.report: args.report.write_text(json.dumps(receipt, ensure_ascii=False, indent=2), encoding='utf-8')
        if sys.stdout is not None: print(json.dumps(receipt, ensure_ascii=True))
        return 0
    show_gui(args.package)
    return 0

if __name__ == '__main__':
    try: sys.exit(main())
    except Exception as error:
        if '--verify-package' in sys.argv or '--simulate' in sys.argv:
            if sys.stdout is not None: print(json.dumps({'status': 'error', 'error': str(error)}, ensure_ascii=True))
            sys.exit(1)
        import tkinter as tk
        from tkinter import messagebox
        root = tk.Tk()
        root.withdraw()
        messagebox.showerror('安装程序未能启动', str(error), parent=root)
        root.destroy()
        sys.exit(1)
