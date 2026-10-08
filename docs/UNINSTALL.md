# 卸载

本版本采用目录式安装，没有 Windows“应用和功能”中的 MSI 卸载项。

1. 保存原理图、PCB 和库文件，关闭插件窗口。
2. 在 AD 的自定义菜单/工具栏界面移除“立创元件”（英文系统可能是 `LCSC Parts`）入口，然后退出 AD。只移除本插件入口，保留其他自定义命令。
3. 依据安装时选择的目录或 `安装结果.json`，删除本插件安装目录。不要把主库目录一并删除。
4. 主库中的 `MyParts.SchLib`、`MyFootprints.PcbLib`、`MyLibrary.LibPkg` 和 `Compiled/MyLibrary.IntLib` 可继续在 AD 独立使用。若也不需要它们，先从 Components 的 File-based Libraries Preferences 移除该 IntLib 注册，再备份或删除对应库目录。
5. 下载并解压的安装介质可以删除。安装备份默认保存在 `%LOCALAPPDATA%\LCSC_AD_Bridge\InstallBackups`，确认不需要恢复后再整理。

如果入口无法从 AD 界面删除，先退出 AD 并备份当前用户对应版本的 `DXP.RCS`。存在 `LCSC` 插件 BEGIN/END 标记时，可移除对应完整块；AD 原生保存可能已移除注释，因此不要凭关键词批量删除整个菜单文件。遇到此情况保留文件，通过 AD 自定义界面处理。

不要将安装前的整份用户配置直接覆盖到长期使用后的配置上，否则可能丢失后来添加的其他菜单。重新安装时使用 Release 中完整资料包，重新选择当前电脑的 AD 设置即可。
