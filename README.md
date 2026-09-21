# Pinghe Launcher Lite

本地运行的 ManageBac + Edupage 学习助手。无服务器、无云端:数据抓取、缓存、提醒、AI 全在本机。
基于 [PH-Launcher](https://github.com/XKRyan/PH-Launcher)(MIT)的设计理念的 Python 本地化重实现。

## 功能

- **我的课表**:Edupage 课表按学生选课生成, 支持教学组配色/连堂合并/高亮标记/当前时间指示线
- **我的日程**:周/月/年三视图, 点日期直接增删日程
- **班级课表**:按班级查询任一天的课表与教室安排
- **我的课程**:ManageBac 课程列表(含总评, 可排序)、未截止作业/考试(可排序/移除/恢复)、
  课程详情(作业/单元/文件/日历)、作业详情与一键提交、CAS 与 EE 概览
- **平和邮箱**:网易企业邮箱收发、通讯录收割与自动补全
- **Agent 助手**:AI 查课表/DDL/邮件/联系人, 起草 Word 作业, 代发邮件、代交作业;
  四档权限模式(只读/操作前确认/工作区写入/完全访问)
- **外观定制**:字体缩放、六套主题预设、四色角色调色(设置页)

## 平台

| 平台 | 状态 | 打包方式 |
|---|---|---|
| Windows 10/11 | ✅ 主力 | PyInstaller 出 app exe，再用 **WiX 3.14** 打成 MSI（`installer/PingheLauncherLite.wxs`），按用户安装、无需管理员 |
| macOS 11+ | ✅ 可构建 | PyInstaller(.app) + hdiutil(DMG)，见 `scripts/macos_build.py`（在 macOS 机器上跑，构建完拉回产物） |

> Windows **只发安装版**（MSI），不再提供便携版（绿色版）exe。

## 快速开始(开发)

```bash
pip install -e .
```

```bash
# 探测学校登录页(无需账号):验证表单与 CSRF
hellopinghe probe --url https://shph.managebac.cn

# 登录并保存会话(密码不落盘,只存 cookie)
hellopinghe login --url https://shph.managebac.cn

# 抓取课程 / DDL / 成总评
hellopinghe classes
hellopinghe ddl --days 14
hellopinghe grades

# Edupage 课表
hellopinghe timetable --subdomain 你的学校子域名 --days 7
```

源码启动:`python run_hellopinghe.py`

## 配置

数据目录:安装版在**安装目录的 data 文件夹**(便携式);源码运行在 `~/.hellopinghe/`(首次运行自动生成)。
Agent 部分支持预设: deepseek / kimi / glm / qwen / ollama(本地) / custom,协议 openai|anthropic 二选一,用户自填 API key 与 base_url。
密码/授权码:Windows 下 DPAPI 加密存数据目录,macOS 存钥匙串——均无明文。

## 安全边界

- 密码只在登录瞬间使用,加密存储在本机;登录态只存 cookie
- 所有数据只进本机 SQLite
- Agent 写操作默认需人工确认;工作区写入/完全访问模式需双重确认并自担风险

## 打包

### Windows

```bash
python -m PyInstaller --noconfirm --clean PingheLauncherLite.spec   # 应用 exe
```

再打成 MSI（**WiX 3.14**，工具在 `tools/wix314/`）：

```bash
cd installer
../tools/wix314/candle.exe PingheLauncherLite.wxs -nologo
../tools/wix314/light.exe  PingheLauncherLite.wixobj -out PingheLauncherLite.msi -nologo
```

> **不要给 candle/light 加 `-ext WixUIExtension`** —— 那会把数据库代码页压回 1252，
> 中文串直接报 `LGHT0311`。不加就正常。
>
> 安装包是**按用户安装**（`%LOCALAPPDATA%\Programs\PingheLauncherLite`），不需要管理员权限；
> 会在「设置 → 应用」里注册，可正常覆盖升级与卸载。`portable.flag` 让数据目录留在**安装目录**里，
> 所以卸载不会删除用户数据。
>
> 早先那版自研安装器（tkinter，`installer/installer.py` + `installer.spec`）**已不再出货**，
> 源码保留仅供参考。

### macOS(在 Mac 上执行)

推荐用仓库里的脚本：它会把源码同步到 Mac、建虚拟环境、打包 .app、生成 dmg，再把产物拉回来。

```bash
python -X utf8 scripts/macos_build.py --host <mac-ip> --user <mac-user>
```

手工做等价于：

```bash
python3 -m venv venv && source venv/bin/activate
pip install -e . pyinstaller pywebview pyobjc-core pyobjc-framework-Cocoa
python -m PyInstaller --noconfirm --clean PingheLauncherLite-mac.spec
hdiutil create -volname "Pinghe Launcher Lite" -srcfolder "dist/Pinghe Launcher Lite.app" \
  -ov -format UDZO PingheLauncherLite.dmg
```

> 产物**未签名**（没有 Developer ID 证书），首次打开要右键 →「打开」。
> 架构取决于构建机：Intel 机器出 x86_64（Apple Silicon 走 Rosetta），
> Apple Silicon 机器出 arm64。仓库里的 GitHub Actions 工作流
> （`.github/workflows/build-macos.yml`）在打 `v*` tag 时会自动为两种架构各出一份并挂到 Release。

## License

本项目以 **GPL-3.0-or-later** 发布(因依赖 [edupage-api](https://pypi.org/project/edupage-api/)(GPL-3.0));
设计基调延续自 [PH-Launcher](https://github.com/XKRyan/PH-Launcher)(MIT)。
完整第三方组件清单见 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。

## 致谢

- [PH-Launcher](https://github.com/XKRyan/PH-Launcher)(MIT)— 设计基调来源
- [edupage-api](https://github.com/nikolajjsj/edupage-api)(GPL-3.0)— Edupage 数据层
- [ManageBac-GPA-Scraper](https://github.com/Ayushpanditmoto/ManageBac-GPA-Scraper)(MIT)— ManageBac 选择器参考
- [pywebview](https://github.com/r0x0r/pywebview)(BSD)— 跨平台桌面窗口
