# Pinghe Launcher Lite

本地优先的 ManageBac + Edupage 学习助手：数据抓取、缓存、提醒、AI 都在本机跑；
选课、日程、账号等可按需通过 **phix 做端到端加密同步**（服务端只存密文，默认可以不开）。
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
- **账号与云同步(可选)**:phix 统一账号登录,把选课/日程/课表/学校数据/AI 配置按对象
  **端到端加密**同步;服务端只存密文,合并全部在客户端做
- **心履**:心情记录本地镜像与增量同步(可选)

## 平台

| 平台 | 状态 | 打包方式 |
|---|---|---|
| Windows 10/11 | ✅ 主力 | PyInstaller 出 app exe，再用**标准 NSIS**（MUI2 assisted，与 PH Launcher 同一套安装体验）打成安装包，按用户安装、无需管理员 |
| macOS 11+ | ✅ 可构建 | PyInstaller(.app) + hdiutil(DMG)，见 `scripts/macos_build.py`（在 macOS 机器上跑，构建完拉回产物） |

> Windows 下载页只提供**安装版**（NSIS `.exe`），不再提供便携绿色版或 MSI；
> 应用内自动更新用的也是同一个 NSIS 安装包（旧版裸 exe 渠道保留兼容，见 §更新）。
>
> 默认安装目录与 PH Launcher 2.0.13 **完全一致**：`%LOCALAPPDATA%\Programs\PH Launcher`。
> 两个程序共用该目录下的 `data/`；先装哪个都行，后装的那个会直接复用已有数据。

## 快速开始(开发)

```bash
pip install -e .
```

```bash
# 探测学校登录页(无需账号):验证表单与 CSRF
hellopinghe probe --url https://shph.managebac.cn

# 登录并保存会话(只存 cookie)
hellopinghe login --url https://shph.managebac.cn

# 抓取课程 / DDL / 成总评
hellopinghe classes
hellopinghe ddl --days 14
hellopinghe grades

# Edupage 课表
hellopinghe timetable --subdomain 你的学校子域名 --days 7
```

源码启动:`python run_hellopinghe.py`

## 测试

```powershell
Get-ChildItem scripts\test_*.py | ForEach-Object { python -X utf8 $_.FullName }
```

当前 **18 个**脚本,0 失败。它们各自打印自己的汇总(其中 5 个用 unittest,会打印
`Ran N tests ... OK`)—— 验收看的是**明确的成功标志 + 没有 traceback**,
不是某一条 "OK" 字符串。本机实际跑法见 `docs/交接文档.md` §4。

## 配置

数据目录:安装版在**安装目录的 data 文件夹**(便携式);源码运行在 `~/.hellopinghe/`(首次运行自动生成)。
`data/` 里 `settings.yaml` / `Schedule` / `Timetable` / `School` / `agent/` 与 PH Launcher
**共用**(所以两个程序不能同时运行),PLL 私有缓存与学校网站 Cookie 关在 `phll/` 里。
Agent 部分支持预设: deepseek / kimi / glm / qwen / ollama(本地) / custom,协议 openai|anthropic 二选一,用户自填 API key 与 base_url。
凭据(四平台密码/授权码、phix 令牌):按用户要求**明文**存 `data/settings.yaml` 的
`accounts` 段与 `secrets_extra`,写入时收紧为仅本人可读;程序不会上传、打印到日志或打进安装包。

## 更新

与 PH Launcher 2.0.13 同一套交互：启动时**后台检查**，发现真正的新版本就**静默预下载**并
显示在设置外的更新卡片上——下载未完成时卡片显示真实进度（服务器没给 Content-Length 时显示
不确定进度，绝不假造百分比）；下载完成且 SHA256 校验通过后，才由用户点「重启并安装」启动
NSIS 安装器。取消只关闭卡片（下载继续），「跳过本版本」会停止下载并记住该版本；手动检查
失败会明确说「检查失败」，不会谎报「已是最新」。

## 安全边界

- 凭据按用户要求明文存 `data/settings.yaml`(见"配置"),靠文件权限保护;不上传、不写日志
- 学校网站登录态只存 cookie(`phll/managebac/session_*.json`),**绝不上云**
- 本地数据是 `data/` 下的 YAML/JSON(不再用 SQLite,旧库首次启动自动迁移);云同步只上传
  白名单对象,且客户端先加密,服务端只有密文
- Agent 写操作默认需人工确认;工作区写入/完全访问模式需双重确认并自担风险

## 打包

### Windows

```bash
python -m PyInstaller --noconfirm --clean PingheLauncherLite.spec   # 应用 exe
python -X utf8 scripts/build_windows.py                            # NSIS 安装包
```

`build_windows.py` 会先校验 `installer/installer.py` 的 `VERSION` 与
`hellopinghe/updater.py` 的 `APP_VERSION` 一致，再用仓库自带的 NSIS 定义
（`installer/PingheLauncherLite.nsi`，MUI2 assisted、单用户、可改目录）编译成
`dist/PingheLauncherLite-<版本>-Setup.exe`。没有 makensis 时可用
`MAKENSIS_EXE` 指定，或安装 NSIS / 复用 electron-builder 的缓存。

> **默认安装位置与 PH Launcher 相同**（`%LOCALAPPDATA%\Programs\PH Launcher`），
> 安装器会创建 `data/` 与 `portable.flag`，让两个程序共用同一份数据。
> 安装/卸载都只碰 Lite 自己的文件，**不会递归删除 data 或 PH Launcher 的文件**。
>
> 历史产物：旧 WiX MSI（`installer/PingheLauncherLite.wxs`）与早期 tkinter
> 安装器（`installer/installer.py`）**已不再出货**，源码保留仅供参考；旧 MSI
> 安装的用户数据不会自动合并进新目录，原目录会保留，请自行确认后再迁移。

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
