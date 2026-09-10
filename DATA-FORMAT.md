# Pinghe Launcher Lite · 数据格式规范(v1)

> 本文档描述 **Pinghe Launcher Lite**(下称 PLL)在用户机器上落盘的全部数据,
> 供 **PH-Launcher**(下称 PHL)按同一规格读写、实现两个应用之间的数据互通。
>
> 规范版本:`1`(2026-09-10)。任何破坏性字段变更都要升版本号并在本文档登记。

---

## 0. 设计原则

1. **数据全部在一个目录里**,便携安装时就在程序目录下的 `data/`(普通安装/源码运行
   是 `~/.hellopinghe/`,测试环境由 `portable.flag` 决定)。目录位置由
   `hellopinghe/paths.py::data_dir()` 单点解析,任何模块都不许自己拼 `Path.home()`。
2. **只分三类**:
   - 人可读可改的**配置**(YAML):`settings.yaml`
   - **两个应用可以共用**的数据:日程 `Schedule`、AI 会话 `agent/`
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
└── _migrated_backup/          由旧版布局迁移过来的历史文件(保留不删,可手动清理)
```

**共用约定**:`Schedule`、`agent/` 两个由 PLL 与 PHL 共用 —— 谁先写谁后写都必须
遵守本文档的字段与语义;`phll/` 只有 PLL 读写,PHL **不要碰**(里面是 PLL 的
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

agent:
  workspace: D:\some\dir        # Agent 读写根目录
  workspaces: [D:\some\dir]
  mode: confirm                 # readonly | confirm | workspace_write | full_access
  send_grades_to_llm: false
  ddl_notify_days: 3

ui:
  course_order: ['12345', '67890']   # 课程卡片顺序(class_id)
  task_order: ['数学|2026-09-12 23:59']  # 作业条目顺序(title|due_at)

secrets_extra: {}               # 认不出归属的凭据键落在这里(键→值)
```

### 2.1 字段说明

| 字段 | 类型 | 说明 |
|---|---|---|
| `version` | int | 文档版本,当前 `1` |
| `wizard_done` | bool | 是否完成首次向导 |
| `accounts.<平台>` | map | 见 2.2 |
| `lessons` | list | 选课项:`subject`(科目族/合并名)、`teacher`、`group`;`group` 为空表示全班必修或默认必选 |
| `ai.providers[]` | list | `id`/`name`/`protocol`/`base_url`/`api_key`/`models[]`/`notes` |
| `ai.active_provider_id` / `active_model` | str | 当前使用的供应商与模型 |
| `agent.workspace` / `workspaces[]` | str/list | Agent 工作区 |
| `agent.mode` | str | 权限档位(见上) |
| `agent.send_grades_to_llm` | bool | 是否允许把成绩发给 LLM(默认 false) |
| `agent.ddl_notify_days` | int | DDL 提前提醒天数 |
| `ui.course_order` / `ui.task_order` | list | 用户拖拽后的排序(顺序即优先级) |
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
| `edupage/week_<周一日期>_<班级id>.json` | 整周课表缓存(6 小时有效) | 可以 |
| `edupage/personal_<日期>_<班级id>_<选课哈希>.json` | 个人课表缓存(2 小时有效) | 可以 |
| `managebac/tasks.json` | 各课作业卡片缓存(6 小时有效) | 可以 |
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

## 6. `logs/` 与 `_backups/`

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

## 8. PHL 适配清单

- [ ] **日程互通(推荐先做)**:读写 `data/Schedule`。加载时按第 3 节校验字段;
      新增用 `max(id)+1`;写入用"读-改-写 + 原子替换";时间戳带本地时区。
- [ ] **AI 会话互通**:按第 4 节读写 `data/agent/*.json`(OpenAI 消息格式,
      可直接喂给模型);
- [ ] **配置**:如果要让用户在两处填一次账号,直接读同一份 `data/settings.yaml`
      的 `accounts` 段;**不要**复制到自己的配置里(会不同步);
      写回时只改自己拥有的字段,保留未知字段与凭据字段。
- [ ] **不要碰** `data/phll/`(PLL 私有缓存与平台 cookie)。
- [ ] 若 PHL 也想做心情记录:可直接复用 `phll/xinlv/entries.json` 的字段与
      同步规则(第 5.3 节),或自行调用心履官方 API —— 但**同一台机器上不要
      两个应用各自上传**,否则会互相覆盖(建议约定:以 PLL 为准,或先合并)。
- [ ] 两应用同时运行时,对 `Schedule` 的写入务必"读最新再改",避免丢对方的改动。

---

## 9. 变更记录

| 版本 | 日期 | 变更 |
|---|---|---|
| 1 | 2026-09-10 | 首次确定布局:`settings.yaml` + `Schedule` + `agent/` + `phll/` + `logs/`;凭据并入 settings.yaml;旧布局(SQLite/config.json/secrets.json/散落缓存)自动迁移 |
