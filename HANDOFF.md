# Hello! Pinghe launcher — 项目交接文档

> 写给接手的新 agent。这里包含项目的全部背景、架构、踩过的坑和当前状态。
> 读完后你应该能独立继续开发和维护这个项目。

---

## 一、项目是什么

**Hello! Pinghe launcher**(原名 SchoolHub)是给上海平和学校学生用的本地学习助手:

- **数据源**:Edupage(课表/考勤)、ManageBac(IB 课程作业/成绩/DDL)、网易企业邮箱(邮件/通讯录)
- **核心卖点**:本地运行、数据不出机器;AI 助手可以查课表/DDL/邮件/联系人、起草 Word 作业、代发邮件、代交作业(全部要用户确认)
- **技术栈**:Python 3.14 + pywebview(EdgeChromium/WebView2) + requests + edupage-api + anthropic/openai SDK + PyInstaller + WiX 3.14.1
- **用户**:平和学校 IB 项目学生(当前测试账号:IB grade 11 class 9 / 九班)

## 二、目录与环境

### 开发目录

- **`D:\HPHL-dev\`** — 唯一的开发仓库(git repo,分支 master)
  - `hellopinghe/` — Python 包(核心代码)
  - `ui/` — 前端(app.js / index.html / styles.css / logo.png)
  - `installer/` — WiX 定义 + 产物 HelloPingheLauncher.msi
  - `tools/wix314/` — WiX 3.14.1 便携版(candle.exe / light.exe)
  - `HelloPingheLauncher.spec` — PyInstaller 打包配置(内嵌 `icon='logo.ico'` + `--add-data "ui;ui"`)
  - `run_hellopinghe.py` — 源码启动入口
  - `HelloPingheLauncher.exe` — 绿色版(仓库根,已跟踪进 git)
  - `logo.ico` / `logo.png` — 图标(源图在 `D:\HPHL\logo.png`)
  - `_ui_test.py` / `_ui_dbg_week.py` — UI 测试脚本(gitignored)

**⚠ 用户明确要求:所有修改只在 `D:\HPHL-dev` 做,不要动本机其他项目的数据。**

### 用户数据目录(不在仓库里,装在用户家目录)

- `~/.hellopinghe/` — config.json(账号/AI provider)、hellopinghe.db(SQLite:作业缓存/日程/ dismissed DDL)、agent_sessions/(Agent 会话)、edupage_week_v3_*.json(整周课表缓存 6h)、edupage_personal_v5_{day}_{selhash}.json(个人课表缓存 2h)、mail_contacts.json(通讯录 24h)、contacts_custom.json(用户自建联系人/隐藏墓碑)、session_{host}.json(ManageBac 会话 cookie)
- Windows 凭据管理器(keyring 服务名 `hellopinghe`):
  - `edupage:{subdomain}:{username}` — Edupage 密码
  - `mail:{email}` — 邮箱网页密码
  - `mail_authcode:{email}` — 邮箱客户端授权码
  - `managebac:{base_url}` — ManageBac 密码

**⚠ 首次导入**:`config.py::_migrate_legacy()` 会在模块导入时自动把旧目录 `~/.schoolhub` 的数据和旧 keyring 服务 `schoolhub` 的密钥迁到新位置(幂等,静默失败)。

### 构建命令

```bash
# exe(产出 dist/HelloPingheLauncher.exe,记得 cp 到仓库根)
cd D:\HPHL-dev
python -m PyInstaller --noconfirm --clean HelloPingheLauncher.spec
cp -f dist/HelloPingheLauncher.exe ./HelloPingheLauncher.exe

# MSI(⚠ 不要加 -ext WixUIExtension,会把数据库代码页压回 1252 导致中文 LGHT0311)
cd installer
..\tools\wix314\candle.exe HelloPingheLauncher.wxs -nologo
..\tools\wix314\light.exe HelloPingheLauncher.wixobj -out HelloPingheLauncher.msi -nologo
rm -f HelloPingheLauncher.wixobj HelloPingheLauncher.wixpdb   # 清理中间产物

# 打包前隐私扫描(必须零命中): REDACTED-ACCOUNT / REDACTED-PASSWORD / REDACTED-PASSWORD / REDACTED-AUTHCODE / REDACTED-KEY
grep -rilE "REDACTED-ACCOUNT|REDACTED-PASSWORD|REDACTED-AUTHCODE|REDACTED-KEY" --include="*.py" --include="*.js" --include="*.html" --include="*.css" --include="*.wxs" --exclude-dir=.git .
```

## 三、代码架构

```
hellopinghe/
├── config.py          # Config dataclass + JSON 读写 + _migrate_legacy()
├── exceptions.py      # PingheError / LoginRequiredError / LoginError
├── storage.py         # SQLite(作业缓存/日程/dismissed DDL/通讯录不在这)
├── managebac/
│   ├── client.py      # ManageBacClient: 纯 HTTP 登录/数据抓取
│   └── parse.py       # HTML 解析(作业卡/DDL/成绩/课程列表)
├── app/
│   ├── __main__.py    # 窗口创建 + --smoke 测试入口
│   ├── bridge.py      # js_api 桥接层(Api 类, 全部方法返回 {ok, data|error})
│   ├── services.py    # 业务服务层(Edupage/FreeRooms/Mail/Schedule/Courses)
│   ├── agent.py       # Agent 引擎(工具循环 + 提案确认机制)
│   └── ...
└── cli.py             # 命令行入口

ui/
├── index.html         # 单页 UI(8 个视图: home/timetable/schedule/gradett/courses/mail/agent/settings)
├── app.js             # 全部前端逻辑(~1770 行)
├── styles.css         # 样式(~377 行, 设计 token 沿用 PH-Launcher 墨绿/金/象牙白)
└── logo.png           # 左上角 logo(源图 D:\HPHL\logo.png, 黑底像素风)
```

### 关键数据流

```
ui/app.js (SWR缓存 localStorage "sh_*")
    ↕ window.pywebview.api.<方法>()
bridge.py Api 类 (_SNAP 进程内 TTL 快照 + _wrap 统一错误)
    ↕
services.py (EdupageService / ManageBacClient / MailService / ScheduleService / AgentEngine)
    ↕
Edupage / ManageBac / 网易IMAP·SMTP / SQLite / keyring / 文件系统
```

### 课表数据管线(最复杂的一块)

1. `EdupageService._ensure()` — 登录(带 `_patch` 超时 + `_speed_patch` 性能补丁,见下)
2. `week_plans(monday, days)` — 3 个 gcall 窗口(锚点周一/周四/周日)合并 → `_parse_week()` 解析成 `{day_iso: [Lesson...]}`;磁盘缓存 `edupage_week_v3_{monday}.json`(6h)
3. `personal(day)` — 从 master_plan 按选课过滤;磁盘缓存 `edupage_personal_v5_{day}_{selhash}.json`(2h)
4. `master_plan(day)` — 先查 week_plans,缺这天回退 `ed.get_my_timetable(day)`(慢,但已被 speed_patch 加速)

## 四、踩过的坑(重要!)

### Edupage 服务端行为

1. **gcall loadData 忽略 dateto** — 只返回以 `date` 为中心的 3 天窗口(前一天+当天+后一天)。实测:请求周一→返回周日~周二。所以必须分 3 个锚点(周一/周四/周日)各拉一次再合并,否则周三~周五丢失
2. **周日锚点窗口会泄漏下周周一** — 合并后必须裁剪到请求区间 `[monday, monday+days)`,否则科目标选项里会出现"下周的周一"造成时间重复
3. **address 头是 IMAP ENVELOPE 序列化格式** — `BODY[HEADER.FIELDS (FROM TO CC)]` 返回 `(("名" NIL "local" "domain"))` 而非 RFC5322,`email.utils.getaddresses` 解析不了,需要自写括号分词器(`_envelope_addresses`)
4. **文件夹名是 modified UTF-7** — `&XfJT0ZAB-` = 已发送,`&g0l6P3ux-` = 草稿箱。`email.header.decode_header` 解不了,需要 `_mutf7_decode`
5. **UID SEARCH 带 CHARSET 会返回 BAD** — 裸 imaplib 命令不要加 CHARSET 参数

### edupage-api 性能病理(如果不修,整周解析 60 秒+)

`get_teachers/get_classes/get_subjects/get_classrooms` 每次调用都重新解析整个 dbi 列表,而课表解析每张课卡都要查一次 → O(卡片数 × 全表解析)。实测 149 张卡 = 9.6 万次 get_teacher、1400 万次对象解析。
**修复**:`EdupageService._speed_patch(ed)` 把这 4 个"全量列表"方法按 Edupage 实例缓存(helper 对象每张卡新建一个,所以必须挂在 edupage 实例上而不是 helper 上)。修复后整周解析 63s→0.02s。

### ManageBac 提交

- dropbox 路由因学校而异,硬编码 `/dropbox` 会 404。`submit_task()` 先打开任务页,在里面动态找提交入口(带本任务 id 的 dropbox 链接或直接上传表单),找不到再按常见路由兜底
- 提交用的 authenticity_token 从任务页表单里取

### 构建陷阱

- **WiX light 千万不要加 `-ext WixUIExtension`** — 它内嵌的 en-US .wxl 会把数据库代码页强制回 1252,中文内容直接 LGHT0311 失败(即使 Product/@Codepage="936")
- WiX 中间产物 .wixobj/.wixpdb/.msi 用完要清理,不要提交进 git
- PyInstaller onefile 的 exe 是压缩的,**对 exe 做二进制字符串扫描找不到任何东西**(包括敏感信息),隐私扫描要在源码层做
- exe 构建后必须 `cp dist/HelloPingheLauncher.exe .` 同步到仓库根(用户会看根目录那个)

### 测试基础设施

- 冒烟:`python -X utf8 -m hellopinghe.app --smoke` → 期望 `SMOKE_JS: dom-ok|js-ok`
- UI 交互测试:`_ui_test.py`(gitignored)— 真实 pywebview 窗口 + MockApi(不连真实服务/不碰真实个人数据),evaluate_js 驱动点击/拖拽/指针滑动,断言写进 `window.__ui_result`
- **改前端交互后必须跑 UI 测试,不能只跑 smoke**(smoke 只验证 DOM+JS 语法,不验证交互逻辑)

### 缓存失效(双层)

- 后端 `_SNAP` 快照(TTL: home 60s / tt 120s / courses 180s / gt 300s / mail 45s / contacts 600s)
- 前端 localStorage SWR(Store,键前缀 `sh_`)
- **选课变更必须同时失效两层**:`wizard_save_selection` 已 drop 后端 `tt|*` + `home`;前端 `sm-save` 已 drop `tt|` + `home`。漏掉任何一层 = 用户改完选课看到旧数据
- 邮件发送 → drop `mail|` + `home`;课程同步(refresh_tasks)→ drop `courses` + `home`;DDL 左滑移除 → drop `home` + `courses`

### 沙箱/工作区

- DSH 文件沙箱工作区是 `D:\HPHL`,而开发目录是 `D:\HPHL-dev`——shell 写文件到 D:\HPHL-dev 可能被拦(策略变了),用 write/edit 工具写、或让用户把 D:\HPHL-dev 加进沙箱白名单
- taskkill 在 git-bash 里 `//IM` 参数会被转义错,用 PowerShell `Stop-Process` 代替
- msiexec 参数也会被 git-bash 转义,用 PowerShell `Start-Process msiexec -ArgumentList '/i',...` 驱动

## 五、Agent 工具清单(build_tools)

| 工具 | 功能 | 类型 |
|---|---|---|
| get_timetable | 未来 N 天个人课表 | 只读 |
| get_ddl | 未来 N 天 ManageBac DDL | 只读 |
| get_class_tasks | 单门/全部课作业卡(含已截止) | 只读 |
| get_grades | 各科总评(需用户允许) | 只读 |
| list_mail / read_mail | 邮件列表/正文 | 只读 |
| get_schedule | 本地日程(区间) | 只读 |
| search_contacts | 通讯录按名字/邮箱搜联系人 | 只读 |
| list_workspace / read_docx / read_text_file | workspace 文件操作 | 只读 |
| create_docx / append_to_docx | 提案:写 Word | 写 |
| add_schedule_event | 提案:新增日程 | 写 |
| send_email | 提案:发邮件(先 search_contacts 查邮箱) | 写 |
| submit_managebac_task | 提案:交作业 | 写 |

所有写操作走 `_propose` → 用户确认 → `agent_confirm(pid)` 才执行。

## 六、课程模型细节

- **作息**(ui/app.js `PERIODS`):P1 8:00-8:40 / P2 8:45-9:25 / P3 9:35-10:15 / P4 10:20-11:00 / P5 11:05-11:55 / Lunch 12:00-12:40 / P6 12:45-13:25 / P7 13:30-14:10 / P8 14:15-14:55 / P9 15:00-15:40 / P10 15:45-16:25 / 晚自习 18:00-20:30
- **周五 12:45 起是走班轮换课**:课名带轮次号(HL1→HL2)、老师会换。选课器按教学组(科目族+组号+老师)列选项,学生勾自己所在的组
- **personal(day) 通用规则**:无组课卡=全班必修一律显示;有组课卡必须命中选课的 (family, group, teacher) 之一(老师宽松匹配:选课老师 ∈ 课卡老师集合)
- ** dismissed DDL**:首页/课程页左滑移除 → `ddl_dismissed` 表按 `title|due_at` 记;设置页"已移除的作业"可恢复(`ddl_restore` 删标记)

## 七、已知遗留问题 / 未完成

1. ~~提交 ManageBac 404~~ — 已修复(动态解析提交入口)
2. ~~TOK 通配符导致别的组的课出现~~ — 已通过教学组选项解决,用户可精确勾选
3. 没有自动化 CI;打包后需手动跑 `_ui_test.py` + smoke
4. `subject_options()` 冷启动可能 60s+(Edupage 服务器慢),目前靠 splash 预载 + week 磁盘缓存缓解;如用户反馈慢可考虑后台线程预热
5. Agent 的 `submit_managebac_task` 依赖动态解析,如果学校改版 ManageBac 页面结构可能再次失效 — 届时重新跑一次探测看新路由

## 八、git 提交历史(最近)

```
32a205b timetable: revert to strict subject+teacher matching (九班课表修正)
86e6561 Selection rebuilt around teaching groups (family+group+teacher)
75ef86e Drop timetable/home snapshots when selection changes
88ccd2a Timetable UI rework + whole-class courses always shown (班会/连堂/组色/弹卡/右键)
45f326b Week-switch blank page fix (骨架屏满宽 + 保留旧画面 + 预取相邻周)
da19fe2 邮箱通讯录: IMAP 收割联系人 + 写邮件自动补全 + AI 按名字查邮箱
973e2cf 修复: 我的课程页 NameError — courses_data 缺少 storage 局部导入
a2f52a5 更新根目录 exe 至最新构建, 清理旧 SchoolHub MSI 解包残留
4f45c58 UI 改进: 删首页空闲教室、写邮件弹卡、workspace 文件夹选择、课程左滑删 DDL+拖拽排序
2b04793 改名: SchoolHub → Hello! Pinghe launcher
2d209bd 基线: 性能优化+缓存+日程三视图+课表节次对齐 (SchoolHub 原名)
```

⚠ 注意: 上面的提交顺序是**从新到旧**, 但 32a205b(严格匹配回退)实际在 86e6561 之前,后来 88ccd2a 用"全班必修+教学组选项"重新覆盖了严格匹配的场景 — 读 git log 时按时间线理解。

## 九、密码/凭据安全

- 用户凭据(Edupage/ManageBac/邮箱密码、授权码)存在 Windows 凭据管理器(keyring 服务 `hellopinghe`),**不在任何代码或配置文件里**
- `~/.hellopinghe/config.json` 只有账号名和 AI provider 的 api_key(用户自己的机器,正常)
- 打包前必须跑隐私扫描(见构建命令),确认零命中才能出包
