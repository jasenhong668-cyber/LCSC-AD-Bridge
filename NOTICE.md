# 第三方组件与许可

本仓库公开项目源码与使用资料，目前未对整个项目另行声明统一的开源许可。第三方代码及二进制继续适用各自的许可证；公开仓库不代表所有内容可任意商业使用。

## 元件转换后端

安装包包含 **JLC-Export-Workstation v0.8.6** 的 `lceda.exe`，作者 **LZJ-I**，来源：<https://github.com/LZJ-I/JLC-Export-Workstation>。其许可证为 **CC BY-NC 4.0**，保留于 `vendor/lceda/LICENSE`，安装后位于 `vendor/lceda/LICENSE`。请遵守署名及非商业使用条件，商业使用联系原作者取得许可。

## 几何与 Python 运行组件

- CadQuery OCP / `cadquery-ocp-novtk` 7.9.3.1.1：Apache 2.0，来源 <https://github.com/CadQuery/OCP>。
- Open CASCADE Technology 7.9.3：LGPL 2.1 及 OCCT 例外，来源 <https://github.com/Open-Cascade-SAS/OCCT/tree/V7_9_3>。
- Python、Tcl/Tk、Pillow、olefile、PyInstaller：安装包的 `runtime/licenses` 及 vendor 目录保留对应许可证。

几何组件许可正文及构建信息见 [vendor/geometry](vendor/geometry)。修改或再分发时保留相关许可证、署名及适用的源码获取信息。

Altium Designer、立创商城及嘉立创相关名称属于各自权利人。本项目为独立工具，不表示上述厂商的官方认证或支持。
