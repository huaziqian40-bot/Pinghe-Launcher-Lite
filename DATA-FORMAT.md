# Pinghe Launcher Lite · 数据格式规范(v1)

> 本文档描述 **Pinghe Launcher Lite**(下称 PLL)在用户机器上落盘的全部数据,
> 供 **PH-Launcher**(下称 PHL)按同一规格读写、实现两个应用之间的数据互通。
>
> 规范版本:`1`(2026-09-10)。任何破坏性字段变更都要升版本号并在本文档登记。

---

## 0. 设计原则

1. **数据全部在一个目录里**,便携安装时就在程序目录下的 `data/`(普通安装/源码运行
   是 `~/.hellopinghe/`,测试环境用 `PHLL_DATA_DIR` 指到临时目录)。目录位置由
   `hellopinghe/paths.py::data_dir()` 单点解析,任何模块都不许自己拼 `Path.home()`。
   解析顺序:环境变量 `PHLL_DATA_DIR`(测试/自动化,与 PH Launcher 的 `PHL_DATA_DIR` 对称)
   → 安装登记(`%LOCALAPPDATA%\PingheSeries\installations.ini`)里本程序的 `data_root`
   (仅安装版/`PHLL_RECORD_INSTALL=1`;安装器会把它设为 PH Launcher 实际使用的数据根,
   见 `install_registry.py`) → 程序目录下存在 `portable.flag` 则数据落
   `<安装目录>/data` → `~/.hellopinghe`。
   测试/演示用的便携包旁边还会放一个 `fresh.flag`:存在时**跳过旧数据迁移**
   (`paths.py::is_fresh`),否则便携模式首次启动会把 `~/.hellopinghe` 里的真实个人
   数据搬进测试目录,测试环境就不再是"完全隔离"的了。
   **两个程序装在同一个文件夹时,各自 exe 旁边的 `data/` 是同一个目录**,这就是"共用"的全部机制:
   Windows 安装包(`installer/PingheLauncherLite.nsi`,1.2.8 起)会写入 `portable.flag` 并建
   `data/`,默认装到 `%LOCALAPPDATA%\Programs\PH Launcher`(与 PH Launcher 2.0.13 相同),
   所以装完数据就落在程序目录,不会跑到用户目录里。
   **旧 MSI(≤1.2.7)的数据不会自动合并**:新安装器只提示、不复制不覆盖;
   Lite 启动时若目标根已有 PHL 数据,也不会把 `~/.hellopinghe` 的旧数据并进来
   (`paths.py::phl_shared_data_present`)。
2. **只分三类**:
   - 人可读可改的**配置**(YAML):`settings.yaml`
   - **两个应用可以共用**的数据:日程 `Schedule`、课表 `Timetable`、学校快照
     `School`、AI 会话 `agent/`,以及云同步状态 `.sync/`(见 6.1)
   - **PLL 独享**的数据:全部关在 `phll/` 文件夹里(缓存、偏好、镜像、会话 cookie)
3. **写入一律原子替换**(临时文件 + `os.replace`),崩溃/断电不会留下半个文件。
4. **读取必须容错**:文件缺失或损坏时使用默认值,绝不因此报错退出。
5. 未知字段**必须原样保留**再写回(向前兼容:老版本读到新版本写的文件不能删字段)。

---

## 1. 目录总览

```
data/                          ← 数据根目录(便携:<安装目录>\data)
├── settings.yaml              配置 + 四平台凭据 + 选课      [人可改]
├── Schedule                   日程(可与 PHL 共用)          [共用]
├── agent/                     AI 会话记录(可与 PHL 共用)    [共用]
│   └── <session-id>.json
├── Timetable                  共享课表(可与 PHL 共用, 见第 8 节)  [共用]
├── School                     学校信息快照(可与 PHL 共用, 见第 9 节) [共用]
├── Profile                    个人资料云同步对象(见 6.2 节)          [共用]
├── Mood                       心情云同步对象(见 6.2 节)              [共用]
├── phll/                      PLL 独享(缓存/偏好/镜像/会话)
│   ├── state.json
│   ├── edupage/               课表缓存(可随时删)
│   ├── managebac/
│   │   ├── tasks.json         作业缓存
│   │   ├── classes.json       课程名快照(一般不用)
│   │   ├── deadlines.json     DDL 快照(一般不用)
│   │   └── session_<host>.json  ManageBac 登录 cookie
│   ├── mail/
│   │   ├── contacts.json      从邮件头收割的联系人(24h 缓存)
│   │   └── contacts_custom.json 用户自建联系人 + 隐藏标记
│   └── xinlv/
│       ├── entries.json       心履心情记录本地镜像
│       └── state.json         心履增量同步游标 + 内容目录缓存
├── logs/                      诊断日志(非用户数据,可随时删)
│   ├── app.log
│   └── error.log
├── _backups/                  自动备份(启动时最多一天一份, 保留最近 14 份)
│   └── data-YYYYMMDD-HHMMSS.zip
├── _migrated_backup/          由旧版布局迁移过来的历史文件(保留不删,可手动清理)
├── .sync/                     云同步状态与明文快照(与 PHL 共用同一份, 见 6.1 节)
├── .pll-running               PLL 运行标记(心跳, 见第 10 节)
├── .phl-running               PHL 运行标记(心跳, 见第 10 节)
└── .updates/                  应用内更新的临时下载目录(可随时删)
```

**共用约定**:`Schedule`、`agent/`、`Timetable`(第 8 节)、`School`(第 9 节)、
`Profile`/`Mood`(6.2 节)由 PLL 与 PHL 共用 —— 谁先写谁后写都必须遵守本文档的字段与语义;
`.sync/`(6.1 节)也是两边共用同一份;`phll/` 只有 PLL 读写,PHL **不要碰**(里面是 PLL 的
私有缓存与登录凭据)。

编码:所有文本文件 **UTF-8(无 BOM)**,换行 `\n`。JSON 缩进 2 空格,非 ASCII
**不转义**(直接写中文)。YAML 用块状风格、不排序键。

---

## 2. `settings.yaml` — 配置与凭据

YAML 文档。PLL 的 `config.py`(非凭据字段)与 `secrets.py`(凭据)共同维护,
**保存时必须"读-改-写",不能整份覆盖**(否则会互相抹掉对方的字段)。

```yaml
version: 1
wizard_done: true

accounts:                       # 四平台凭据(见 2.2)
  edupage:
    username: someone@example.com
    subdomain: pingheschool
    password: "……"
  managebac:
    base_url: https://shph.managebac.cn
    email: someone@example.com
    password: "……"
  mail:
    email: someone@example.com
    imap_host: imap.qiye.163.com
    smtp_host: smtp.qiye.163.com
    password: "……"              # 网页登录密码(回退用)
    authcode: "……"              # 客户端授权码(IMAP/SMTP 首选)
  xinlv:
    username: someone
    token: "……"                 # 心履 Bearer 令牌(长期有效)

lessons:                        # 选课(教学组 = 科目族 + 组号 + 老师)
- subject: TOK
  teacher: Jiabin Xu
  group: F
- subject: 国家理科              # 默认必选(物理/化学/生物合并)
  teacher: 理科组
  group: ''

ai:                             # AI 供应商(PLL 的 Agent 用)
  providers:
  - id: p-glm-xxxxxx
    name: GLM (智谱)
    protocol: openai            # openai | anthropic
    base_url: https://open.bigmodel.cn/api/paas/v4
    api_key: "……"
    models: [glm-4-plus]
    notes: ''
  active_provider_id: p-glm-xxxxxx
  active_model: glm-4-plus
  updated_at: '2026-09-13T10:00:00+08:00'   # 同步元信息(三端合并时判断该听谁的)
  updated_by: pll                            # 真正改配置的那一端; 云端拉下来的(web/phl)原样保留

agent:
  workspace: D:\some\dir        # Agent 读写根目录
  workspaces: [D:\some\dir]
  mode: confirm                 # readonly | confirm | workspace_write | full_access
  send_grades_to_llm: false
  ddl_notify_days: 3

ui:
  course_order: ['12345', '67890']   # 课程卡片顺序(class_id)
  task_order: ['数学|2026-09-12 23:59']  # 作业条目顺序(title|due_at)

update:                         # 应用内更新(独立成段, 不混进与三端共享的同步对象)
  skipped_version: ''           # 用户点过「跳过本版本」的版本号; 该版本静默, 更高版本仍会提示

phix:                           # phix 云同步(见 6.1; 两个程序共用同一份设置)
  server: https://phix.ing
  username: someone
  user_id: 12
  device: MY-PC
  key_mode: password            # password(用登录密码包裹) | syncphrase(独立同步口令)
  auto_sync: true
  sync_interval_minutes: 10     # 自动同步间隔(界面下限 2 分钟)
  objects: [settings.accounts, settings.lessons, settings.ui,
            schedule, timetable, school, profile, mood]
  last_sync_at: '2026-09-13T10:00:00+08:00'
  e2e: true                     # 应用层加密传输(默认开; 关掉只在已经走 HTTPS 时考虑)

secrets_extra: {}               # 认不出归属的凭据键落在这里(键→值)
```

> `backup` 段(自动备份的 `enabled` / `interval_hours` / `keep`)在 §6 有示例,这里不重复。

### 2.1 字段说明

| 字段 | 类型 | 说明 |
|---|---|---|
| `version` | int | 文档版本,当前 `1` |
| `wizard_done` | bool | 是否完成首次向导 |
| `accounts.<平台>` | map | 见 2.2 |
| `lessons` | list | 选课项:`subject`(科目族/合并名)、`teacher`、`group`;`group` 为空表示全班必修或默认必选 |
| `ai.providers[]` | list | `id`/`name`/`protocol`/`base_url`/`api_key`/`models[]`/`notes` |
| `ai.active_provider_id` / `active_model` | str | 当前使用的供应商与模型 |
| `ai.updated_at` / `updated_by` | str | `ai` 段的同步元信息:三端(网页/PLL/PHL)合并时按"谁什么时候改的"决定听谁的;配置内容没变就不刷新时间戳 |
| `agent.workspace` / `workspaces[]` | str/list | Agent 工作区 |
| `agent.mode` | str | 权限档位(见上) |
| `agent.send_grades_to_llm` | bool | 是否允许把成绩发给 LLM(默认 false) |
| `agent.ddl_notify_days` | int | DDL 提前提醒天数 |
| `ui.course_order` / `ui.task_order` | list | 用户拖拽后的排序(顺序即优先级) |
| `update.skipped_version` | str | 用户点过「跳过本版本」的版本号;该版本不再提示,更高的版本仍会弹卡片 |
| `phix.*` | map | phix 云同步:`server`/`username`/`user_id`/`device`/`key_mode`/`auto_sync`/`sync_interval_minutes`/`objects`/`last_sync_at`/`e2e`,见 6.1 |
| `backup.*` | map | 自动备份:`enabled`/`interval_hours`(默认 20)/`keep`(默认 14),见第 6 节 |
| `secrets_extra` | map | 兜底凭据键值 |

### 2.2 四平台凭据

| 平台 | 字段 | 用途 |
|---|---|---|
| Edupage | `username` / `subdomain` / `password` | 课表(edupage-api 登录) |
| ManageBac | `base_url` / `email` / `password` | 作业、成绩、DDL、交作业 |
| 网易企业邮 | `email` / `imap_host` / `smtp_host` / `password` / `authcode` | IMAP/SMTP **必须用 `authcode`**(客户端授权码),`password` 仅作回退 |
| 心履 | `username` / `token` | `token` 为 `Authorization: Bearer` 令牌,长期有效 |

> ⚠️ **凭据是明文**(用户明确要求"四平台密码放 settings.yaml",便于两个应用共用与
> 用户自己查看)。因此:PLL 写入时会 `chmod 0600`;两个应用都**不得**把该文件
> 上传、打印到日志、或写进任何安装包/仓库。PHL 若要共用,请直接读同一份文件,
> 不要另存一份。

### 2.3 兼容规则

- 读:缺字段用默认值;`version` 大于自己支持的版本时,只读认识的字段,**写回时
  保留全部未知字段**(必须"读-改-写")。
- 写:`accounts` 下只更新自己拥有的字段,别把别人的段落整块替换。
- 旧的 `data/config.json` 与 `data/secrets.json` 会自动迁移进本文件,迁移后的旧
  文件在 `_migrated_backup/`。

---

## 3. `Schedule` — 日程(可与 PHL 共用)

一个 JSON 文档(无扩展名,内容为 JSON)。**按天存储,一天可多条**。

```json
{
  "version": 1,
  "kind": "pinghe-schedule",
  "app": "Pinghe Launcher Lite",
  "updated_at": "2026-09-10T21:30:00+08:00",
  "events": [
    {
      "id": 3,
      "day": "2026-09-12",
      "time": "15:00",
      "title": "打球",
      "note": "带球拍",
      "created": "2026-09-10T21:29:41+08:00"
    }
  ]
}
```

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `version` | int | ✓ | 当前 `1` |
| `kind` | str | ✓ | 固定 `pinghe-schedule`(便于对方识别文件类型) |
| `app` | str | | 最后写入者名称(便于排查) |
| `updated_at` | str | | 最后写入时间(ISO8601 带时区) |
| `events` | list | ✓ | 事件数组,可为空 |
| `events[].id` | int | ✓ | 唯一自增 id(全文件内唯一;删除后不复用) |
| `events[].day` | str | ✓ | `YYYY-MM-DD`,**本地日期**,不带时区 |
| `events[].time` | str | | `HH:MM`;空字符串 = 全天事项 |
| `events[].title` | str | ✓ | 事项标题 |
| `events[].note` | str | | 备注,可空 |
| `events[].created` | str | | 创建时间(ISO8601 带时区) |
| `lastId` | int | | **id 高水位**:分配过的最大 id。删掉最大 id 的事件后这个值不退回去,保证"删除后不复用";PH Launcher 也写这个字段,PLL 新增时取"现存最大 id 与 `lastId`"的较大者 +1 |

语义与约定:

- **新增** id = 现有最大 id + 1(不要用时间戳,避免跨应用冲突时无法排序)。
- **删除** = 从数组里移除;由于是共用文件,删除前建议重新读取一次(避免覆盖对方的改动)。
- **排序**由读方决定;建议 `(day, time, id)` 升序。
- 时间戳一律**带本地时区偏移**(如 `+08:00`),不要写 UTC 的 `Z`(跨应用显示会差 8 小时)。
- 写入必须是"读-改-写 + 原子替换";PLL 用进程内锁保护,PHL 侧建议同样加锁
  (或先 `stat` 比对 mtime 再合并)。

---

## 4. `agent/` — AI 会话(可与 PHL 共用)

**一个会话一个 JSON 文件**,文件名即会话 id(建议 `YYYYMMDD-HHMMSS`)。

```
agent/20260910-213045.json
```

```json
{
  "id": "20260910-213045",
  "title": "最近两周哪些作业还没交",
  "history": [
    { "role": "system", "content": "你是……" },
    { "role": "user", "content": "最近两周哪些作业还没交?" },
    { "role": "assistant", "content": "", "tool_calls": [
        { "id": "call_1", "name": "get_ddl", "arguments": "{\"days\": 14}" } ] },
    { "role": "tool", "tool_call_id": "call_1", "name": "get_ddl",
      "content": "{\"ok\": true, \"deadlines\": []}" },
    { "role": "assistant", "content": "这两周有 3 项……" }
  ]
}
```

| 字段 | 类型 | 说明 |
|---|---|---|
| `id` | str | 会话 id(与文件名一致) |
| `title` | str | 会话标题(通常取首条用户消息前 30 字) |
| `history` | list | OpenAI Chat Completions 风格的消息数组(见下) |
| `version` | int | 可选:PH Launcher 会写 |
| `kind` | str | 可选:PH Launcher 写 `phl-agent-session` |
| `app` | str | 可选:最后写入者(`PH Launcher` / `Pinghe Launcher Lite`),便于排查与"谁写的谁负责" |
| `updated_at` | str | 可选:最后写入时间(ISO8601 带时区) |

**写回时只改这四个已知字段,其它字段(对方写的)原样保留** —— 会话文件是两边的共用文件,
整份覆盖会把对方写的元数据抹掉。

消息角色与字段(与 OpenAI 协议一致,便于直接喂给 LLM):

| `role` | 附加字段 | 说明 |
|---|---|---|
| `system` | `content` | 系统提示词 |
| `user` | `content` | 用户输入 |
| `assistant` | `content`(可空)、`tool_calls[]`(`id`/`name`/`arguments`(JSON 字符串)) | 模型回复或工具调用 |
| `tool` | `tool_call_id`、`name`、`content` | 工具执行结果(JSON 字符串) |

约定:

- 只追加、不改写历史;列表页按文件 mtime 倒序即可。
- `arguments` / `content` 是**字符串**(即使内容是 JSON),不要塞对象。
- 会话里可能含个人隐私(成绩、邮件),共用时请勿自动上传。

---

## 5. `phll/` — PLL 独享数据

> PHL **不要读写**这里;它包含 PLL 的私有缓存与平台登录凭据。
> 全部可以随时删除(删了只是丢缓存/需要重新登录,不会丢用户数据)。

| 文件 | 内容 | 可否删除 |
|---|---|---|
| `state.json` | 偏好与小状态:`ddl_dismissed`(用户在界面上左滑移除的 DDL) | 谨慎(会丢"已移除"记录) |
| `edupage/week_<周一日期>_<班级id或all>.json` | 整周课表缓存(6 小时有效;拿不到自己的班级 id 时文件名里写 `all`) | 可以 |
| `edupage/personal_v8_<日期>_<班级id或all>_<选课哈希>.json` | 个人课表缓存(2 小时有效;写新缓存时会顺手清掉同一天其它选课哈希的 v8 缓存和上一版的 `personal_<日期>_…`) | 可以 |
| `managebac/tasks.json` | 各课作业卡片缓存(6 小时有效) | 可以 |
| `managebac/classes.json` | 课程号→课程名快照(一般不用) | 可以 |
| `managebac/deadlines.json` | DDL 快照(CLI `ddl --save` 会写;一般不用) | 可以 |
| `managebac/session_<host>.json` | ManageBac 登录 cookie | 可以(需重新登录) |
| `mail/contacts.json` | 从收发件人收割的联系人(24 小时缓存) | 可以 |
| `mail/contacts_custom.json` | 用户自建联系人 + 隐藏标记 | 谨慎(用户手工数据) |
| `xinlv/entries.json` | 心履心情记录本地镜像(含未同步的脏标记) | **不要删**(会丢未上传的记录) |
| `xinlv/state.json` | 心履增量同步游标 `server_time` + 内容目录缓存 | 谨慎 |

### 5.1 `phll/state.json`

```json
{
  "version": 1,
  "kind": "phll-state",
  "updated_at": "2026-09-10T21:30:00+08:00",
  "ddl_dismissed": {
    "shph.managebac.cn": [
      { "key": "物理 IA 初稿|2026-09-12 23:59", "created": "2026-09-10T20:00:00+08:00" }
    ]
  }
}
```

- `ddl_dismissed` 以 **ManageBac host** 为键(一个应用可能连不同学校)。
- `key` 规则固定为 `` `${title}|${due_at}` ``(`due_at` 为 `YYYY-MM-DD HH:MM`,
  可能为空字符串),与 PLL 首页/课程页的"左滑移除"共用一套键。
- "恢复"就是把对应项从数组里删掉。

### 5.2 `phll/managebac/tasks.json`

```json
{
  "version": 1,
  "kind": "phll-managebac-tasks",
  "hosts": {
    "shph.managebac.cn": {
      "updated": "2026-09-10T21:20:00+08:00",
      "items": [
        { "task_id": "88547", "class_id": "39792",
          "class_name": "Physics HL", "title": "IA 初稿",
          "due_at": "2026-09-12T23:59:00", "status": "Pending",
          "past_due": false, "can_submit": true }
      ]
    }
  }
}
```

| 字段 | 说明 |
|---|---|
| `hosts.<host>.updated` | 该 host 缓存写入时间(超过 6 小时应重新抓取) |
| `items[].task_id` | ManageBac 任务 id |
| `items[].class_id` / `class_name` | 所属课程 |
| `items[].due_at` | ISO8601(**不带时区**,按本地时间理解)或 `null` |
| `items[].status` | `Pending` / `Submitted` / `Late` … |
| `items[].past_due` | 是否已过期(界面用它分组"已过期") |
| `items[].can_submit` | 卡片上是否有"提交作业"入口 |

### 5.3 `phll/xinlv/entries.json`(心情记录镜像)

与心履官方同步协议字段一一对应(**不要改名**,否则无法增量同步):

```json
{
  "version": 1,
  "kind": "phll-xinlv-entries",
  "entries": [
    { "uuid": "550e8400-e29b-41d4-a716-446655440000",
      "date": "2026-09-10", "at": "09:30:00", "mood": "happy",
      "note": "今天天气很好", "intensity_level": 2, "intensity_percent": 60,
      "deleted": false, "created_at": "2026-09-10T09:30:01+08:00",
      "updated_at": "2026-09-10T09:30:01+08:00", "dirty": false }
  ]
}
```

| 字段 | 说明 |
|---|---|
| `uuid` | 客户端生成 UUID v4,**全局唯一键**(服务端按它 upsert) |
| `mood` | 10 个 key 之一:`happy/calm/excited/grateful/tired/anxious/sad/angry/lonely/numb` |
| `intensity_level` | 1–4(略微/有点/相当/十分) |
| `intensity_percent` | 0–100(界面滑杆) |
| `deleted` | **墓碑**(软删除):`true` 的条目要照常上传,本地不显示 |
| `updated_at` | 该条最后修改时间;**冲突时最新者赢(LWW)** |
| `dirty` | PLL 内部标记:还没推送到服务器的条目(`true` 需 push) |

同步规则(官方 v1):先 push(单批 ≤500)再 pull(`?since=` 必须 URL 编码,
用 `state.server_time` 增量);本地删除 = 置 `deleted=true` 后照常 push。

### 5.4 `phll/xinlv/state.json`

```json
{ "version": 1, "state": { "server_time": "2026-09-10T13:00:00+00:00",
                           "catalog_ts": "1757500000.0",
                           "catalog_json": "{...}" } }
```

`state` 是字符串键值对;`server_time` 是增量拉取的起点(服务端时钟,别用本机时间)。

---

## 6. 状态、同步落地与备份目录(`.sync/`、`Profile`、`Mood`、`logs/`、`_backups/`)

### 6.1 `data/.sync/` — phix 云同步状态

> 同步的对象清单与合并规则见 `D:\phix\server\docs\协议规范.md` §5;同步状态文件见
> 该文档 §5.2,固定服务器公钥见该文档 §10.1。这里只描述 PLL 在本机落下的目录。
> `.sync/` **不上云、不进备份包**,两个程序共用同一份(它们不能同时运行),
> 换程序接着同步时基版就是对方留下的那份。

```
data/.sync/
├── accounts/<账号>/state.json        同步状态(按账号分目录)
├── accounts/<账号>/last/<对象>.json  上次同步后的明文快照(三方合并的基版)
└── pinned/<host>.txt                 固定下来的服务器公钥(首次信任后 TOFU)
```

- **状态与快照按账号隔离**(`cloudsync.py::SyncEngine._account_dir`)。为什么必须:
  快照是"上次同步后的明文",三方合并靠它判断"对方删了"还是"我新加的";在同一个
  `data/` 上换了 phix 账号却沿用旧账号的快照,新账号云端没有的内容会被判成
  "远端删除了",于是**把本地数据删掉**。
- `state.json` 的形态:`{version, kind: "phix-sync-state", server, user_id, username,
  device, last_sync_at, objects: {<对象>: {revision, sha256, synced_at}}, conflicts: [...]}`。
  `objects[].sha256` 与快照内容对不上时**不认那份快照**(宁可这轮不传播删除,
  也绝不猜着删);`conflicts` 保留最近 50 条,由界面提示用户。
- 快照文件名里对象的 `:`、`/` 会换成 `__`(例:`agent__20260910-213045.json`)。
- 老布局 `data/.sync/state.json`(不分账号)会在登录后迁移进当前账号目录:只复制、
  不删旧文件,并把迁过来的快照 `sha256` 清空 → 第一轮退化成"只取并集",重建基线。

### 6.2 `data/Profile` 与 `data/Mood` — 云同步对象的本地落地文件

> 这两个不是"配置"也不是可删缓存,而是 phix 同步对象 `profile` / `mood` 在本地的那一份。
> 两个程序读写同一路径(`cloudsync.py` 的 `collect` / `apply`),所以算共用文件。

| 文件 | 内容 | 可否删除 |
|---|---|---|
| `Profile` | 个人资料:`{display_name, avatar, updated_at}`。`display_name` 最长 50 字;`avatar` 是前端可显示的头像串;`updated_at` 是 **UTC** ISO 时间(历史实现,别按 §8/§9 的"带本地时区"惯例解析) | 谨慎(丢了头像/昵称, 云端还有) |
| `Mood` | 心情同步对象:`{entries: [{id, ts, text, intensity}, …]}`。PLL 目前**没有界面写入方**,只把云端内容拉下来落地、再原样推回 | 谨慎(本地镜像会丢, 云端还有) |

- 合并语义:PHL 对 `profile` 取 `updated_at` 较新的一份、对 `mood` 按 `entries[].id`
  取并集(PHL `electron/cloudsync.cjs`);PLL 走通用的"整体替换型"合并
  (`cloudsync.py::_merge_scalar`),两边都改且不同时保留本地并记进 `conflicts[]`。
  因为 PLL 不主动写 `Mood`,这条差异实际很少碰到。
- 两者都**不进自动备份包**(见 6.3:只收 `settings.yaml`/`Schedule`/`agent/`/
  `phll/state.json`/`phll/xinlv/`/`phll/mail/`),云端才是权威副本。

### 6.3 `logs/` 与 `_backups/`

| 文件 | 内容 |
|---|---|
| `logs/app.log` | 运行日志(启动、同步、错误摘要),UTF-8 文本 |
| `logs/error.log` | 崩溃堆栈 |
| `_backups/data-<时间戳>.zip` | **自动备份**:启动时若距上次超过 `backup.interval_hours`(默认 20 小时)就打包一份,保留最近 `backup.keep`(默认 14)份 |

自动备份**只收不可再生的数据**:`settings.yaml`、`Schedule`、`agent/`、
`phll/state.json`、`phll/xinlv/`、`phll/mail/`;课表与作业缓存(可重新抓取、
动辄上百 MB)不进包。可在 `settings.yaml` 里调整:

```yaml
backup:
  enabled: true
  interval_hours: 20
  keep: 14
```

恢复方式:关掉应用 → 把 zip 里的文件按原路径解回 `data/` 覆盖 → 重新打开。

日志不属于用户数据,可随时删除;发 issue 时附带这两个文件最有用。

---

## 7. 并发、原子性与兼容

| 项目 | 约定 |
|---|---|
| 写文件 | 临时文件 + `os.replace` 原子替换(绝不能直接覆盖写) |
| 进程内并发 | 每个文件一把锁;`update_json/update_settings` 内部已含"读-改-写" |
| 跨进程并发 | 两个应用同时写 `Schedule`/`agent/` 时:**读最新 → 合并 → 写回**,并做一次 mtime 比对重试;PLL 侧不做跨进程锁(靠短写+原子替换) |
| 损坏容错 | 解析失败 = 当空文件处理,**不要自动删除**用户文件 |
| 版本升级 | 新增字段随意(老版本保留即可);删除/改名字段必须升 `version` 并写迁移 |
| 旧布局 | `data/` 根下的 `config.json`、`secrets.json`、`hellopinghe.db`、`edupage_*.json`、`session_*.json`、`mail_contacts.json`、`contacts_custom.json`、`agent_sessions/` 都是**旧版本遗留**,PLL 首次启动会迁移并把它们移进 `_migrated_backup/` |

---

## 8. `Timetable` — 共用课表(按天课卡)

```json
{
  "version": 1,
  "kind": "pinghe-timetable",
  "app": "PH Launcher",
  "updated_at": "2026-09-11T12:51:43+08:00",
  "days": {
    "2026-09-21": [
      { "subject": "Physics HL1", "teacher": "Jing Jiang", "room": "A201",
        "start": "08:00", "end": "08:40", "group": "A", "cancelled": false }
    ]
  }
}
```

- 键与 PLL `personal()` 的课卡一致,两个程序都不需要转换字段;`group` 为空 = 全班必修;
  `cancelled` 为 `true` 的课不提醒。课卡还可能带 `curriculum`(Edupage 的课程类别,
  字符串;不认识它的程序原样保留即可,不要删)。
- 两个程序都会写这份文件(谁同步成功谁写);**本地没有缓存时也读它**——
  与 `School` 的 `edupage` 段互为备份,只要有一份在就能用。
- 写入是"读-改-写":只覆盖当天的数组,未知字段原样保留。

---

## 9. `School` — 学校信息快照(课表 / 课程作业 / 邮箱摘要)

```json
{
  "version": 1,
  "kind": "pinghe-school",
  "app": "PH Launcher",
  "updated_at": "2026-09-11T12:51:44+08:00",
  "edupage": { "week_start": "2026-09-07", "fetched_at": "…+08:00", "class_name": "…",
               "lessons": [ { "date": "2026-09-07", "start": "08:00", "end": "08:40",
                              "subject": "…", "teacher": "…", "room": "…",
                              "group": "A", "cancelled": false } ],
               "selected_groups": ["…"] },
  "managebac": { "fetched_at": "…+08:00",
                 "courses": [ { "id": "11", "name": "…", "grade": "6" } ],
                 "tasks":   [ { "id": "…", "course_id": "11", "course": "…", "title": "…",
                                "due_at": "…", "due_text": "…", "status": "…", "score": "…" } ] },
  "mail": { "fetched_at": "…+08:00", "unread": 3,
            "recent": [ { "uid": "9001", "from": "…", "subject": "…", "date": "…",
                          "unread": true } ] }
}
```

- **只写自己负责的段**,其余段与未知字段原样保留;写入走"读-改-写 + 原子替换"。
- `managebac` 段两端都写 → **按条目自己的 id 取并集**(同 id 用新抓到的字段)。
  不合并的话:一边 20 门课/7 份作业、另一边 13 门/51 份,后写的会把对方的条目全抹掉。
- `edupage` 段同一周取并集、换了一周整段替换(不攒历史)。
- `mail` 段**只放摘要**:未读数 + 最近 30 条的发件人/主题/日期;**正文与附件绝不进共享文件**。
- 时间戳一律带本地时区偏移(`+08:00`),不写 `Z`。

---

## 10. 同系列互斥(`.phl-running` / `.pll-running`)

两个程序共用 `settings.yaml`/`Schedule`/`agent/`/`Timetable`/`School`/`Profile`/`Mood`,
所以**不能同时运行**。各自在数据根目录写一个自己的运行标记,启动时先看对方那个:

```json
{ "kind": "pll", "pid": 4532,
  "started_at": "2026-09-11T12:39:00+08:00", "updated_at": "2026-09-11T12:39:31+08:00",
  "startedAt": "2026-09-11T12:39:00+08:00", "updatedAt": "2026-09-11T12:39:31+08:00" }
```

- **时间戳两种拼法都写、都读**:标记里同时带 snake_case(`started_at`/`updated_at`)与
  camelCase(`startedAt`/`updatedAt`)(PLL 见 `shareddata.py::_write_marker`,PHL 见
  `electron/app-mutex.cjs::writeMarker`),读对方的标记时两种都认(两边各有一份
  `readMarker`)。必须两种都写:已经装在用户机器上的旧版本只认自己那一种(旧 PLL 只读
  snake_case、旧 PHL 只读 camelCase),只写一种会让对方读不到心跳。读不到时间戳时按
  "仍然有效"处理,冲突就只剩"PID 还活着吗"一条判据,而 PID 会被系统回收给别人 →
  误判成"对方正在运行"、程序打不开(2026-10-12 实测确认过两边互相读不到)。
- 对方的标记存在、PID 活着、且时间戳在 **90 秒**内 → 提示后退出,不启动。
- 每 **30 秒**刷新一次时间戳(心跳)。**只看 PID 不够**:进程被强杀尚未回收时
  `OpenProcess` 仍会成功,PID 也会被系统回收给别人 —— 两种都会误判成"对方正在运行"而打不开。
- 老版本写的、没有时间戳的标记按"仍然有效"处理(保守)。
- 退出时只删自己的标记(删前核对 PID)。

---

## 11. PHL 适配清单

> 状态(2026-09-11):下面这项清单 **PH Launcher 已经全部做完**,见 PH Launcher 仓库的
> `docs/data-format.md`。两侧一起跑的自检(数据目录请用副本):
> `node scripts/interop-check.cjs seed|check <dataDir>` +
> `python -X utf8 scripts/interop_check.py check|write`。

- [x] **日程互通**:读写 `data/Schedule`(第 3 节),新增 id 取"现存最大 id 与 `lastId` 高水位"的较大者 +1;
- [x] **AI 会话互通**:按第 4 节读写 `data/agent/*.json`(OpenAI 消息格式,可直接喂给模型);
- [x] **配置**:直接读同一份 `data/settings.yaml` 的 `accounts` 段;**不要**复制到自己的配置里(会不同步);
      写回时只改自己拥有的字段,保留未知字段与凭据字段。
- [x] **不要碰** `data/phll/`(PLL 私有缓存与平台 cookie)。
- [x] **课表与学校数据**:`data/Timetable`(第 8 节)与 `data/School`(第 9 节),两端都写、都读。
- [x] **同系列互斥**:`.phl-running` / `.pll-running`(第 10 节)。
- [x] 两应用同时运行时,对 `Schedule` 的写入"读最新再改"(PHL 侧还做一次 mtime 比对重试)。
- [x] **云同步对象 `Profile` / `Mood`**:两端都读写 `data/Profile` / `data/Mood`(6.2 节)。
- [ ] **心履的原生记录仍不共享**:PLL 的镜像在 `phll/xinlv/`,PHL 的心履数据在 `phl/`
      私有目录里。同一台机器上**不要**让两个应用各自上传,否则会互相覆盖。


---

## 12. 变更记录

| 版本 | 日期 | 变更 |
|---|---|---|
| 1 | 2026-09-10 | 首次确定布局:`settings.yaml` + `Schedule` + `agent/` + `phll/` + `logs/`;凭据并入 settings.yaml;旧布局(SQLite/config.json/secrets.json/散落缓存)自动迁移 |
| 1.1 | 2026-09-11 | 补记两个程序共用的 `Timetable`(第 8 节)、`School`(第 9 节)与同系列互斥标记(第 10 节);`Schedule` 增加 `lastId` 高水位;`agent/` 增加可选的 `version/kind/app/updated_at`;新增 `PHLL_DATA_DIR` 环境变量;MSI 安装包写入 `portable.flag` 并预建 `data/` |
| 1.1(核对) | 2026-11-02 | 文档与代码对齐(无破坏性字段变更,格式版本仍为 `1`):§1 目录总览补 `Timetable`/`School`/`Profile`/`Mood`/`.sync/`/`.pll-running`/`.phl-running`/`.updates/`;§0 修正数据目录解析顺序并补 `fresh.flag`(测试环境禁止旧数据迁移);§2 补 `ai.updated_at`/`updated_by`、`update.skipped_version`、`phix` 段与 `backup` 指引;§5 补 `managebac/classes.json`/`deadlines.json`、修正课表缓存文件名(`personal_v8_*`、班级 id 缺省写 `all`);新增第 6 节 6.1(`data/.sync/`)与 6.2(`data/Profile`、`data/Mood`);§8/§9 补 `curriculum` 字段;§10 补运行标记的 camelCase 拼写及"两种都读"的原因;§11 修正 `mood` 共享范围 |
| 1.3 | 2026-11-10 | 新增安装登记(`%LOCALAPPDATA%\PingheSeries\installations.ini`,两个程序共写):记录各自安装目录与数据根,安装器据此默认装到对方目录并采用对方数据根;数据目录解析增加"登记里的 data_root"(仅安装版);`Schedule` 与 PHL 的跨程序读写加真实测试(不需要翻译) |
| 1.2 | 2026-11-10 | 安装方式与更新载荷变化(数据文件格式不变,仍为 `1`):Windows 安装入口从 WiX MSI 改为标准 NSIS(默认 `%LOCALAPPDATA%\Programs\PH Launcher`,与 PH Launcher 2.0.13 相同),写入 `portable.flag` 并建 `data/`,两个程序默认共用同一数据根;旧 MSI 数据不自动合并(见 §0.1);应用内更新改为后台预下载 + 用户确认后运行 NSIS 安装包,Windows 更新清单平台名从 `win` 改为 `win-nsis`(旧 `win` 渠道保留供 1.2.7 升级) |
