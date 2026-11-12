# 三颗豆（MouseGlass / SKD）开源源码包

> 版本 **v1.4.29** · Windows 10/11 · Python 3.13 + PySide6-Essentials 6.9.3

一个常驻托盘的多品牌鼠标状态面板：通过 Windows 原生 HID 读取电量、型号、DPI、
回报率与连接方式；对可写设备直接改 DPI / 档位 / 回报率，对不支持的设备如实标注
「谁在管这件事」。详见同目录 `功能介绍.md`。

## 目录结构

```
skd-driver-opensource/
  README.md                 本文件
  功能介绍.md               完整功能说明（界面、支持矩阵、实现、边界）
  设备适配指南.md            ★ 新增自己的设备就看这份
  LICENSE.md                许可证占位说明（发布前必须补齐）
  requirements.txt          运行 / 打包依赖
  build.py                  PyInstaller 打包脚本（自动递增版本号）
  version_info_skd*.txt     exe 版本信息模板（build.py 会自动重写）
  src/                      全部源码
  tests/                    全量单元测试
  assets/                   app.ico / uninstall.ico
  docs/                     DESIGN-SYSTEM-v2.md（视觉设计规范，基线 v1.3.14，实现以源码为准）
```

## 快速开始

```bash
pip install -r requirements.txt
python src/main.py                       # 直接跑源码（需 Windows；系统通知图标等安装期资源以安装包为准）
python -m unittest discover -s tests    # 全量回归
python build.py                          # 产出 dist/skd-setup-<版本>.exe
```

`build.py` 会自行渲染 `assets/*.ico` 与 `version_info_*.txt`，再依次构建主程序、
独立卸载器和安装包；分发产物只有安装包一个 exe。

## 适配自己的设备

按 `设备适配指南.md` 的顺序填完四个维度：**设备连接方式 / 当前供电方式 / DPI / 回报率**。
指南里给出了 `MouseInfo` 与 `DeviceCaps` 的完整字段契约、四条适配路线、可直接复制的
Monitor 骨架、以及「不支持时必须复用的提示文案对照表」。核心原则：不伪造数据、
不支持就不给交互、认不出的设备不显示。

## 本包**不含**什么

安装包 / 主程序 exe、`build/` 与 `dist/` 构建产物、截图、日志、用户配置、开发期的
探针与截图脚本、IDE 配置、字节码缓存。这些都是本机开发过程的中间物，不是源码的
一部分；包内也不含任何用户数据或本机绝对路径。

## 使用限制

权利人声明（见 `src/setup_core.py` 的 `DESCRIPTION`）：

> 本软件适用不同品牌鼠标驱动的管理，例如DPI、刷新率、电量的管理。
> **本软件已开源，禁止私自商用**。

`LICENSE.md` 目前只是占位说明，**正式发布仓库前请由权利人选定并写入正式许可证文本**。
本项目实现代码为自写；型号识别参考了公开的 usb.ids、OpenRGB 设备登记单、HID++ 2.0
文档与各品牌官网参数页。
