# 源码与构建

开发环境为 Windows x64、Python 3.14。发布安装包已包含运行时，普通用户无需执行这里的步骤。

```powershell
py -3.14 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.\.venv\Scripts\python.exe -m pytest tests -q
.\.venv\Scripts\python.exe -m PyInstaller --noconfirm LCSC_AD_Bridge.spec
.\.venv\Scripts\python.exe -m PyInstaller --noconfirm LCSC_AD_Launcher.spec
.\.venv\Scripts\python.exe -m PyInstaller --noconfirm GeometryWorker.spec
.\.venv\Scripts\python.exe -m PyInstaller --noconfirm LCSC_AD_Setup.spec
```

构建产物在 `dist`。源码中的 spec 使用项目目录，不依赖原开发电脑盘符。底层 bridge 命令行的库默认路径为 `D:/AltiumDocuments/MyLibrary`；开发调用请显式传入 `--library`。正式安装器会按用户选择生成实际路径。

## 制作安装介质

从本仓库 Release 下载并解压空库安装介质，将新构建的 Setup 放到介质根目录，Bridge、Launcher、GeometryWorker 放到 `runtime`。保留第三方后端、所有许可证和空库种子。修改后需要重新计算 `package-manifest.json` 中每个已列出文件的 SHA256；校验值不一致会被安装器拒绝。不要把已经使用过的安装目录当作发布介质。

`vendor/lceda` 在源码仓库中只包含许可证，后端二进制随 Release 发行。原始项目为 JLC-Export-Workstation v0.8.6，使用和再分发需遵守其 CC BY-NC 4.0 条款。空库种子位于 `assets/empty-library`，不得用包含个人元件的主库替换。

## 模块

| 模块 | 职责 |
| --- | --- |
| `portable_setup.py` | 安装向导、包校验、空库安装、AD 菜单、配置回滚 |
| `ad_launcher.py` | AD 与后台进程的启动衔接 |
| `component_browser.py` / `preview_render.py` | 搜索窗口与三类预览 |
| `bridge.py` | 下载、转换、任务协议及脚本生成 |
| `library_import.py` / `main_library.py` | 主源库合并、绑定和校验 |
| `component_geometry.py` / `geometry_worker.py` | 来源变换解析与 STEP 几何处理 |
| `cfb_native.py` / `schematic_text.py` | 原生文件记录及符号文本处理 |
| `templates` | AD DelphiScript 模板 |

## 测试数据

公开仓库包含单元测试与空库种子。依赖开发机原生元件库、转换缓存和 AD 保存样本的测试会标记跳过，避免公开个人工程或元件缓存。跳过不等于通过；完整 AD 验收需自行准备合法样本，在 AD 中执行原生保存、编译、放置、PCB 更新和 3D 检查。源代码测试不能证明目标电脑已安装成功。
