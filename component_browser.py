"""Search/select/preview window; only the explicit import action changes libraries."""
import json, pathlib, queue, re, shutil, subprocess, threading, time, uuid, zlib
import tkinter as tk
from tkinter import ttk, messagebox, filedialog
from PIL import ImageTk
import bridge, main_library, preview_render

def decode(data):
    try: return data.decode('utf-8')
    except UnicodeDecodeError: return data.decode('gbk', errors='replace')

def search(keyword, cache, backend_root):
    keyword = keyword.strip()
    if not keyword or len(keyword) > 100 or any(ord(c) < 32 for c in keyword): raise ValueError('请输入立创 C 编号或型号关键字，最长 100 个字符。')
    cache = pathlib.Path(cache).resolve()
    if re.fullmatch(r'[cC][1-9][0-9]{0,11}', keyword):
        pointer = cache / 'source' / (keyword.upper() + '.json')
        if pointer.is_file():
            source = pathlib.Path(json.loads(pointer.read_text(encoding='utf-8'))['source']).resolve()
            if source.parent == cache / 'source' and (source / 'download-complete.json').is_file():
                sch = next(source.rglob('*.SchLib'), None)
                if sch:
                    root = main_library.components(bridge.read_streams(sch))[0][1][0][2]
                    return [{'code': keyword.upper(), 'name': root.get('LIBREFERENCE', sch.stem), 'manufacturer': '已下载的本地缓存', 'has_3d': None}]
    backend = pathlib.Path(backend_root) / 'vendor/lceda/lceda.exe'
    result = subprocess.run([str(backend), 'search', keyword, '--limit', '20'], capture_output=True, timeout=90, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    output = decode(result.stdout)
    if result.returncode: raise RuntimeError(decode(result.stderr)[-1600:] or output[-1600:] or '搜索失败，请稍后重试。')
    matches = re.findall(r'^\[\s*\d+\]\s+(.+?)\s+\|\s+(C\d+)\s+\|\s+(.+?)\s+\|\s+3D:\s*(yes|no)\s*$', output, re.M)
    return [{'code': code, 'name': name, 'manufacturer': manufacturer, 'has_3d': model == 'yes'} for name, code, manufacturer, model in matches]

def prepare_preview(code, cache, progress):
    report = bridge.fetch(code, cache, require_3d=False, progress=progress)
    source = pathlib.Path(report['source_pcblib']).parent
    placement = report.get('model_placement')
    if placement:
        data = json.loads(pathlib.Path(placement['preview_mesh']).read_text(encoding='utf-8'))
        mesh = preview_render.with_footprint(report, (data['vertices'], [(tuple(face), tuple(color)) for face, color in data['faces']]))
    else:
        obj = next(source.glob('*.obj'), None)
        mesh = preview_render.obj_mesh(obj) if obj else None
    return preview_report(report, mesh)

def preview_report(report, mesh=None):
    if mesh is None and report.get('model_placement'):
        data = json.loads(pathlib.Path(report['model_placement']['preview_mesh']).read_text(encoding='utf-8'))
        mesh = preview_render.with_footprint(report, (data['vertices'], data['faces']))
    images = [preview_render.schematic(report), preview_render.footprint(report), preview_render.model(mesh) if mesh else None]
    root = main_library.components(bridge.read_streams(report['source_schlib']))[0][1][0][2]
    description = root.get('%UTF8%COMPONENTDESCRIPTION', root.get('COMPONENTDESCRIPTION', ''))
    if '%UTF8%COMPONENTDESCRIPTION' in root: description = description.encode('latin1').decode('utf-8', errors='replace')
    else: description = description.encode('latin1').decode('gbk', errors='replace')
    return {'report': report, 'images': images, 'mesh': mesh, 'description': description}

def replace_step(report, step, cache):
    raw = pathlib.Path(step).read_bytes()
    if not raw.lstrip().startswith(b'ISO-10303-21;') or b'END-ISO-10303-21;' not in raw: raise ValueError('请选择完整的 STEP 文件。')
    pcb = bridge.read_streams(report['source_pcblib'])
    models = list(bridge.records(pcb.get(('Library', 'Models', 'Data'), b'')))
    if len(models) != 1: raise ValueError('替换 STEP 目前要求来源封装包含一个模型。')
    root = pathlib.Path(cache) / 'overrides' / (report['lcsc'] + '_' + uuid.uuid4().hex[:8])
    root.mkdir(parents=True, exist_ok=False)
    for path in pathlib.Path(report['source_schlib']).parent.glob('*_symbol_easyeda.json'): shutil.copy2(path, root / path.name)
    sch = root / 'Source.SchLib'; shutil.copy2(report['source_schlib'], sch)
    metadata_path = next(pathlib.Path(report['source_pcblib']).parent.glob('*_footprint_easyeda.json'))
    metadata = json.loads(metadata_path.read_text(encoding='utf-8'))
    metadata['result']['model_3d'].update(title=pathlib.Path(step).stem, uri='')
    (root / 'Source_footprint_easyeda.json').write_text(json.dumps(metadata), encoding='utf-8')
    (root / 'Replacement.step').write_bytes(raw)
    pcb[('Library', 'Models', '0')] = zlib.compress(raw)
    model = models[0][2].copy(); model['NAME'] = 'Replacement.step'
    pcb[('Library', 'Models', 'Data')] = bridge.params(model)
    bridge.write_verified(root / 'Source.PcbLib', pcb)
    result = bridge.prepare(sch, root / 'Source.PcbLib', root / 'bound', report['lcsc'], True, True)
    result['manual_step_override'] = {'path': str(pathlib.Path(step).resolve()), 'sha256': bridge.sha(raw)}
    pathlib.Path(root / 'bound/binding-report.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    return preview_report(result)

class Browser:
    def __init__(self, config, request, reply):
        self.config, self.request, self.reply = config, request, pathlib.Path(reply)
        self.messages, self.results, self.prepared = queue.Queue(), {}, {}
        self.busy, self.closed, self.imported, self.current, self.started, self.angle = False, False, False, None, time.monotonic(), 32
        self.root = tk.Tk()
        self.root.title('立创元件导入 · MyLibrary ' + bridge.VERSION)
        self.root.configure(bg='#303438')
        self.root.geometry(f'{min(1320, self.root.winfo_screenwidth() - 100)}x{min(810, self.root.winfo_screenheight() - 100)}+40+35')
        self.root.minsize(930, 660)
        self.root.protocol('WM_DELETE_WINDOW', self.close)
        self.root.columnconfigure(0, weight=3)
        self.root.columnconfigure(1, weight=2, minsize=360)
        self.root.rowconfigure(1, weight=1)
        style = ttk.Style(self.root)
        style.theme_use('clam')
        style.configure('.', font=('Microsoft YaHei UI', 10), foreground='#e7eaef', background='#303438')
        style.configure('Treeview', background='#24282c', fieldbackground='#24282c', foreground='#e4e8ed', rowheight=31, borderwidth=0)
        style.configure('Treeview.Heading', background='#42484e', foreground='white', relief='flat')
        style.map('Treeview', background=[('selected', '#386a91')], foreground=[('selected', 'white')])
        style.configure('TButton', background='#454d56', foreground='white', padding=(13, 6))
        style.map('TButton', background=[('active', '#5b7489'), ('disabled', '#353a40')], foreground=[('disabled', '#818891')])
        style.configure('Accent.TButton', background='#33779e')
        style.configure('TProgressbar', background='#53b8a5', troughcolor='#25292e', borderwidth=0)
        top = ttk.Frame(self.root, padding=15)
        top.grid(row=0, column=0, sticky='nsew')
        top.columnconfigure(0, weight=1)
        ttk.Label(top, text='立创 C 编号 / 型号关键字').grid(row=0, column=0, columnspan=2, sticky='w', pady=(0, 8))
        self.query = tk.StringVar(value=request.get('code') or 'C725895')
        self.entry = tk.Entry(top, textvariable=self.query, bg='#23282d', fg='white', insertbackground='white', relief='flat', font=('Segoe UI', 12))
        self.entry.grid(row=1, column=0, sticky='ew', ipady=7)
        self.entry.bind('<Return>', lambda event: self.do_search())
        self.search_button = ttk.Button(top, text='搜索', style='Accent.TButton', command=self.do_search)
        self.search_button.grid(row=1, column=1, padx=(10, 0))
        table = ttk.Frame(self.root, padding=(15, 0, 15, 0))
        table.grid(row=1, column=0, sticky='nsew')
        table.rowconfigure(0, weight=1)
        table.columnconfigure(0, weight=1)
        self.tree = ttk.Treeview(table, columns=('code', 'fp', '3d', 'name', 'description'), show='headings', selectmode='browse')
        for key, title, width in [('code', '立创编号', 108), ('fp', 'FP', 42), ('3d', '3D', 42), ('name', '名称 / 型号', 220), ('description', '厂家 / 描述', 260)]:
            self.tree.heading(key, text=title)
            self.tree.column(key, width=width, minwidth=35, stretch=key in ('name', 'description'))
        self.tree.grid(row=0, column=0, sticky='nsew')
        scroll = ttk.Scrollbar(table, orient='vertical', command=self.tree.yview)
        scroll.grid(row=0, column=1, sticky='ns')
        self.tree.configure(yscrollcommand=scroll.set)
        horizontal = ttk.Scrollbar(table, orient='horizontal', command=self.tree.xview)
        horizontal.grid(row=1, column=0, sticky='ew')
        self.tree.configure(xscrollcommand=horizontal.set)
        self.tree.bind('<<TreeviewSelect>>', self.select)
        self.detail = tk.Text(table, height=6, wrap='word', bg='#303438', fg='#bfcbd5', relief='flat', font=('Microsoft YaHei UI', 9), padx=8, pady=8, state='disabled')
        self.detail.grid(row=2, column=0, columnspan=2, sticky='ew', pady=(12, 0))
        right = ttk.Frame(self.root, padding=(0, 15, 15, 0))
        right.grid(row=0, column=1, rowspan=2, sticky='nsew')
        right.columnconfigure(0, weight=1)
        self.canvases, self.photos, self.images = [], [None] * 3, [None] * 3
        for i, title in enumerate(('原理图预览', 'PCB 封装预览', '3D 模型预览')):
            frame = ttk.Frame(right, padding=8)
            frame.grid(row=i, column=0, sticky='nsew', pady=(0, 9))
            frame.columnconfigure(0, weight=1)
            frame.rowconfigure(1, weight=1)
            right.rowconfigure(i, weight=1)
            ttk.Label(frame, text=title, font=('Microsoft YaHei UI', 11, 'bold')).grid(row=0, column=0, sticky='w', pady=(0, 5))
            canvas = tk.Canvas(frame, height=170, bg='#11171d', highlightthickness=1, highlightbackground='#525b66')
            canvas.grid(row=1, column=0, sticky='nsew')
            canvas.bind('<Configure>', lambda event, index=i: self.draw(index))
            self.canvases.append(canvas)
            if i == 0:
                self.part_button = ttk.Button(frame, text='切换单元', command=self.next_part, state='disabled')
                self.part_button.grid(row=0, column=1, padx=(6, 0))
                canvas.grid(columnspan=2)
            if i == 2:
                self.step_button = ttk.Button(frame, text='替换 STEP…', command=self.choose_step, state='disabled')
                self.step_button.grid(row=0, column=1, padx=(6, 0))
                self.rotate_button = ttk.Button(frame, text='旋转 45°', command=self.rotate, state='disabled')
                self.rotate_button.grid(row=0, column=2, padx=(6, 0))
                canvas.grid(columnspan=3)
        bottom = ttk.Frame(self.root, padding=15)
        bottom.grid(row=2, column=0, columnspan=2, sticky='ew')
        bottom.columnconfigure(0, weight=1)
        self.status = tk.StringVar(value='先搜索，再选择结果查看预览。导入后可从 AD Components 面板放置。')
        ttk.Label(bottom, textvariable=self.status, wraplength=1100).grid(row=0, column=0, columnspan=4, sticky='w', pady=(0, 7))
        self.progress = ttk.Progressbar(bottom, maximum=100)
        self.progress.grid(row=1, column=0, columnspan=4, sticky='ew', pady=(0, 10))
        ttk.Label(bottom, text='目标：MyParts.SchLib + MyFootprints.PcbLib → MyLibrary.IntLib').grid(row=2, column=0, sticky='w')
        self.save_button = ttk.Button(bottom, text='导出模型…', command=self.save_model, state='disabled')
        self.save_button.grid(row=2, column=1, padx=(5, 8))
        self.import_button = ttk.Button(bottom, text='导入主库', style='Accent.TButton', command=self.import_part, state='disabled')
        self.import_button.grid(row=2, column=2, padx=(0, 8))
        self.close_button = ttk.Button(bottom, text='关闭', command=self.close)
        self.close_button.grid(row=2, column=3)
        self.entry.focus_set()
        self.root.after(100, self.poll)

    def set_busy(self, value, text=''):
        self.busy = value
        if value: self.started = time.monotonic()
        self.search_button.configure(state='disabled' if value else 'normal')
        self.import_button.configure(state='disabled')
        self.save_button.configure(state='disabled' if value or self.current is None else 'normal')
        if text: self.status.set(text)

    def worker(self, operation, callback):
        def task():
            try: self.messages.put((operation, callback()))
            except Exception as error: self.messages.put(('error', str(error)))
        threading.Thread(target=task, daemon=True).start()

    def do_search(self):
        if self.busy: return
        value = self.query.get().strip()
        self.set_busy(True, '正在搜索…')
        self.progress.configure(value=5)
        self.worker('search', lambda: search(value, self.config['cache'], self.config['backend_root']))

    def select(self, event=None):
        if self.busy: return
        selected = self.tree.selection()
        if not selected: return
        code = selected[0]
        self.current = code
        if code in self.prepared: self.display(code, self.prepared[code]); return
        self.images = [None] * 3
        self.set_busy(True, '正在取得符号、封装、STEP 和 3D 网格…')
        for i in range(3): self.draw(i)
        self.progress.configure(value=10)
        self.worker('preview', lambda: (code, prepare_preview(code, self.config['cache'], lambda stage, percent: self.messages.put(('progress', (stage, percent))))))

    def display(self, code, prepared):
        self.prepared[code] = prepared
        self.current = code
        report = prepared['report']
        self.images = prepared['images']
        self.angle = 32
        self.symbol_part = 1
        unit_count = report.get('symbol_units', {}).get('count', 1)
        self.part_button.configure(text=f'单元 1/{unit_count} · 切换', state='normal' if unit_count > 1 else 'disabled')
        self.set_busy(False)
        self.rotate_button.configure(state='normal' if prepared['mesh'] else 'disabled')
        self.step_button.configure(state='normal' if report['embedded_models'] else 'disabled')
        self.import_button.configure(state='normal' if report['embedded_models'] and report['bodies'] else 'disabled')
        values = self.results[code]
        self.tree.item(code, values=(code, '✓', '✓' if report['embedded_models'] else '—', report['symbol'], ' '.join(prepared['description'].split())[:180] or values['manufacturer']))
        self.detail.configure(state='normal')
        self.detail.delete('1.0', 'end')
        source_model = (report.get('model_placement') or {}).get('source_model', {}).get('title', '')
        self.detail.insert('end', f"{report['symbol']}  ·  {code}\n封装：{report['footprint']}\n引脚 / 焊盘：{report['unique_pin_count']} / {report['unique_pad_count']}  ·  单元：{unit_count}  ·  STEP：{'文件及绑定已校验' if report['embedded_models'] else '缺失，不能导入'}\n来源模型：{source_model or '未提供模型名称'}\n{prepared['description']}")
        self.detail.configure(state='disabled')
        self.progress.configure(value=100)
        self.status.set('预览已就绪。确认元件后点击“导入主库”；AD 将继续完成原生编译。')
        for i in range(3): self.draw(i)

    def draw(self, index):
        canvas = self.canvases[index]
        canvas.delete('all')
        width, height = max(10, canvas.winfo_width()), max(10, canvas.winfo_height())
        if self.images[index] is None:
            text = '选择搜索结果后显示预览' if self.current is None else '正在加载预览…' if self.busy else '没有可用的三维网格' if index == 2 else '预览不可用'
            canvas.create_text(width / 2, height / 2, text=text, fill='#8e9aa6', font=('Microsoft YaHei UI', 10))
            return
        image = self.images[index].copy()
        image.thumbnail((max(1, width - 8), max(1, height - 8)))
        self.photos[index] = ImageTk.PhotoImage(image)
        canvas.create_image(width / 2, height / 2, image=self.photos[index])

    def rotate(self):
        if self.current is None or self.busy: return
        mesh = self.prepared[self.current]['mesh']
        if not mesh: return
        self.angle = (self.angle + 45) % 360
        self.images[2] = preview_render.model(mesh, angle=self.angle)
        self.draw(2)

    def next_part(self):
        if self.current is None or self.busy: return
        report = self.prepared[self.current]['report']
        count = report.get('symbol_units', {}).get('count', 1)
        self.symbol_part = self.symbol_part % count + 1
        self.images[0] = preview_render.schematic(report, part=self.symbol_part)
        self.part_button.configure(text=f'单元 {self.symbol_part}/{count} · 切换')
        self.draw(0)

    def choose_step(self):
        if self.current is None or self.busy: return
        path = filedialog.askopenfilename(parent=self.root, title='选择正确的 STEP 模型（沿用来源封装的尺寸与旋转，请核对预览）', filetypes=[('STEP 模型', '*.step *.stp')])
        if not path: return
        code = self.current; report = self.prepared[code]['report']
        self.set_busy(True, '正在校验并对齐替换模型，请在导入前核对方向和引脚…')
        self.worker('preview', lambda: (code, replace_step(report, path, self.config['cache'])))

    def import_part(self):
        if self.current is None or self.busy: return
        report = self.prepared[self.current]['report']
        if self.tree.selection() != (self.current,): return
        if not report['embedded_models'] or not report['bodies']: return
        self.set_busy(True, '正在合并两份主源库并建立全部引脚映射…')
        self.progress.configure(value=70)
        self.close_button.configure(state='disabled')
        self.worker('import', lambda: main_library.import_master(report, self.config['my_library']))

    def save_model(self):
        if self.current is None or self.busy: return
        directory = filedialog.askdirectory(parent=self.root, title='选择模型导出文件夹')
        if not directory: return
        report = self.prepared[self.current]['report']
        target = pathlib.Path(directory) / (report['lcsc'] + '_' + report['symbol'])
        if target.exists(): messagebox.showerror('导出停止', '同名导出目录已存在，请选择其他位置。', parent=self.root); return
        target.mkdir()
        shutil.copy2(report['schlib'], target / 'Part.SchLib')
        shutil.copy2(report['pcblib'], target / 'Footprint.PcbLib')
        shutil.copy2(report['project'], target / pathlib.Path(report['project']).name)
        for source in pathlib.Path(report['source_pcblib']).parent.iterdir():
            if source.suffix.lower() in ('.obj', '.mtl'): shutil.copy2(source, target / source.name)
        placement = report.get('model_placement')
        if placement: shutil.copy2(placement['normalized_step'], target / 'Aligned.step')
        else:
            for source in pathlib.Path(report['source_pcblib']).parent.glob('*.step'): shutil.copy2(source, target / source.name)
        self.status.set('模型已导出：' + str(target))

    def poll(self):
        while not self.messages.empty():
            kind, data = self.messages.get_nowait()
            if kind == 'progress': self.progress.configure(value=data[1]); self.status.set(data[0])
            elif kind == 'search':
                self.set_busy(False)
                self.results = {row['code']: row for row in data}
                self.current, self.images = None, [None] * 3
                self.tree.delete(*self.tree.get_children())
                for row in data: self.tree.insert('', 'end', iid=row['code'], values=(row['code'], '待查', '✓' if row['has_3d'] else '待查', row['name'], row['manufacturer']))
                self.status.set(f'找到 {len(data)} 个结果，选择一项查看预览。' if data else '没有找到结果，请检查编号或更换关键字。')
                self.progress.configure(value=100)
                for i in range(3): self.draw(i)
                if data: self.tree.selection_set(data[0]['code'])
            elif kind == 'preview':
                code, prepared = data
                self.prepared[code] = prepared
                self.set_busy(False)
                if self.tree.selection() == (code,): self.display(code, prepared)
                else: self.select()
            elif kind == 'import':
                bridge.response(self.reply, data, job_id=self.request['job_id'])
                self.imported = True
                self.progress.configure(value=85)
                self.status.set('源库合并和绑定完成。窗口关闭后，AD 将继续保存、编译和安装 IntLib。')
                self.root.after(600, self.root.destroy)
            elif kind == 'error':
                self.set_busy(False)
                self.close_button.configure(state='normal')
                self.status.set('操作停止：' + data)
                self.progress.configure(value=0)
                messagebox.showerror('操作停止', data, parent=self.root)
        if self.busy:
            elapsed = int(time.monotonic() - self.started)
            text = self.status.get().split('  ·  已等待')[0]
            self.status.set(text + f'  ·  已等待 {elapsed} 秒')
        if not self.closed: self.root.after(100, self.poll)

    def close(self):
        if self.busy and str(self.close_button['state']) == 'disabled': return
        self.closed = True
        if not self.imported: bridge.response(self.reply, error='cancelled', job_id=self.request['job_id'])
        self.root.destroy()

def run_browser(config, request, reply):
    browser = Browser(config, request, reply)
    browser.root.mainloop()
    return 0 if browser.imported else 1
