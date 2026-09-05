# Hello Pinghe! Launcher

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
| Windows 10/11 | ✅ 主力 | PyInstaller + 自研安装器 HPHLSetup.exe |
| macOS 14+ (Apple Silicon) | ✅ 可构建 | PyInstaller(.app)+ hdiutil(DMG), 见 `scripts/macos/` |

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
python -m PyInstaller --noconfirm --clean HelloPingheLauncher.spec   # 应用 exe
python -m PyInstaller --noconfirm --clean installer/installer.spec   # 安装程序 HPHLSetup.exe
```

> WiX/MSI 已弃用,改用自研安装器(自定义安装目录/桌面与任务栏快捷方式/注册卸载/数据目录随安装)。
> 需要先把 WiX 换掉前的旧版 MSI 卸载干净再装新版。

### macOS(在 Mac 上执行)

```bash
python3 -m venv venv && source venv/bin/activate
pip install -e . pyinstaller
python -m PyInstaller --noconfirm --clean HelloPingheLauncher-mac.spec
hdiutil create -volname "Hello Pinghe! Launcher" -srcfolder dist -ov -format UDZO HelloPingheLauncher.dmg
```

## License

本项目以 **GPL-3.0-or-later** 发布(因依赖 [edupage-api](https://pypi.org/project/edupage-api/)(GPL-3.0));
设计基调延续自 [PH-Launcher](https://github.com/XKRyan/PH-Launcher)(MIT)。
完整第三方组件清单见 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。

## 致谢

- [PH-Launcher](https://github.com/XKRyan/PH-Launcher)(MIT)— 设计基调来源
- [edupage-api](https://github.com/nikolajjsj/edupage-api)(GPL-3.0)— Edupage 数据层
- [ManageBac-GPA-Scraper](https://github.com/Ayushpanditmoto/ManageBac-GPA-Scraper)(MIT)— ManageBac 选择器参考
- [pywebview](https://github.com/r0x0r/pywebview)(BSD)— 跨平台桌面窗口
