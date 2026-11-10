# 给 PHL 开发 AI 的提示词：安装登记与数据格式统一（配合 PHL Lite 1.2.9）

> 请先完整阅读 `docs/安装位置登记与数据统一.md`（规范），再按本提示词改 PHL 仓库。
> **只改 PHL 仓库**；PHL Lite 仓库只读参考，不要修改、不要提交它的文件。
> 不要直接部署/上传服务器，产物交给统一发布流程。

## 背景

PHL Lite 1.2.9 已经实现了固定目录的安装登记与「默认装到另一个程序所在目录」的联动；
PHL 侧还没有。现在两边存在两个问题：

1. 用户把 PHL 装到非默认目录后，再装 Lite（或反过来）不会自动装到同一目录，
   `data/` 没有共用。
2. PHL 的 `Timetable` / `agent/` 会整文件重建，Lite 写入的未知字段会丢；
   `Schedule` 虽然已经是同一份共享格式，但需要确认「读 Lite 写的条目、补默认值」
   的规则稳定，不要引入额外翻译层。

## 目标（验收口径）

1. **安装位置联动**：安装 PHL 时，如果 Lite 已安装，目录页默认值 = Lite 的安装目录；
   Lite 安装时同理（Lite 侧已完成，PHL 侧需保证登记可被读到）。用户仍可改目录。
2. **数据根联动**：登记里记录双方实际 `data_root`；安装器默认采用对方的数据根，
   避免「同目录但数据根不同」。
3. **数据格式**：`settings.yaml` / `Schedule` / `Timetable` / `School` / `agent/`
   两个程序直接读写同一份格式；PHL 不得整文件重建而丢掉 Lite 的未知字段。
   `Schedule` 保持 `{version, kind, app, updated_at, events[], lastId}`，
   事件字段 `{id, day, time, title, note, created}`；PHL 私有字段不进共享文件，
   读取时按自己的默认补 `end = start + 60 分钟`、无重复、无提醒。
4. 现有 PHL 测试全绿，并新增上述行为的测试。

## 具体改动

### A. 安装登记文件（新增 Node 模块）

位置（每用户，无需管理员）：

- Windows: `%LOCALAPPDATA%\PingheSeries\installations.ini`
- macOS: `~/Library/Application Support/PingheSeries/installations.ini`

格式（INI；NSIS 原生可读）：

```ini
[phl]
installed=1
path=C:\Users\me\Programs\PH Launcher
data_root=C:\Users\me\Programs\PH Launcher\data
version=2.0.13
updated_at=2026-11-10T18:20:00+08:00

[phl-lite]
installed=1
path=C:\Users\me\Programs\PH Launcher
data_root=C:\Users\me\Programs\PH Launcher\data
version=1.2.9
updated_at=...
```

建议新增 `electron/pinghe-series-registry.cjs`：

- `read()`：缺失/损坏返回空（绝不抛错）；只接受绝对路径。
- `record(name, { path, dataRoot, version })`：读-改-写、保留另一个程序的节、
  同目录临时文件 + `rename` 原子替换、UTF-8 无 BOM。
- `remove(name)`：卸载时删除自己的节（路径匹配才删）。
- `installation(name)`：`installed` 为 `0/false/no` 或路径为空 → `null`。
- 启动时在 `main.cjs` 写 `[phl]`：`path = path.dirname(app.getPath('exe'))`
  （macOS 从 `Contents/MacOS` 向上找到 `.app` 目录）、`data_root = dataRoot().root`、
  `version = app.getVersion()`。失败只记日志，不能挡启动。

### B. 安装器（`build/installer.nsh`）

electron-builder 的 NSIS 模板在 `installer.nsi:79` 调用 `customInit` 宏
（目录页之前），在 `installSection.nsh:81` 调用 `customInstall`，
在 `uninstaller.nsh:156` 调用 `customUnInstall`。请：

1. `!macro customInit`：
   - 读 `$LOCALAPPDATA\PingheSeries\installations.ini` 的 `[phl-lite] path`
     与 `data_root`。
   - 若 `IfFileExists "$path\PingheLauncherLite.exe"` 成立：
     `StrCpy $INSTDIR "$path"`，并把 `data_root` 存到全局变量
     （如 `Var /GLOBAL DetectedLiteDataRoot`）。
   - fallback 1：遍历 `HKCU\Software\Microsoft\Windows\CurrentVersion\Uninstall`，
     `DisplayName` 为 `Pinghe Launcher Lite` 且 `InstallLocation` 下存在
     `PingheLauncherLite.exe` → 用该目录（旧版 Lite 没写登记）。
   - fallback 2：`$LOCALAPPDATA\Programs\PH Launcher\PingheLauncherLite.exe`
     存在 → 用该目录（Lite 的标准默认目录）。
   - 都没有 → 保持 electron-builder 的默认 `$INSTDIR`。
   - **必须实测**：确认 `customInit` 修改 `$INSTDIR` 后目录页默认值确实变了；
     若 electron-builder 在 `customInit` 之后又覆盖 `$INSTDIR`，改用
     `MUI_DIRECTORYPAGE_VARIABLE`/自定义目录页或调整 hook，并在 PR 说明。
2. `!macro customInstall`（保留原有 `CreateDirectory "$INSTDIR\data"`）：
   - 数据根：`DetectedLiteDataRoot` 非空 → 用它；否则 `$INSTDIR\data`。
   - 写 `[phl]`：`installed=1`、`path=$INSTDIR`、`data_root=<采用的数据根>`、
     `version=${VERSION}`（`WriteINIStr`；先 `CreateDirectory "$LOCALAPPDATA\PingheSeries"`）。
   - **不得**覆盖/删除 `[phl-lite]` 节。
3. `!macro customUnInstall`：
   - 读 `[phl] path`；等于 `$INSTDIR` 时 `DeleteINISec` 删除 `[phl]`；
     保留 `[phl-lite]`；数据目录保持原有「不删」规则。
4. 目录页文案加一句：若检测到 Lite，会默认装到它的目录并共用数据根。

### C. 数据根缺陷修复

- 核对 `electron/data-layout.cjs` 的 `resolveDataRoot` / `writeRootPointer`：
  文档记录「数据目录指针不生效（写入路径与读取路径不一致）」。
  修成 **写入和读取使用同一个 `app.getPath('userData')`**，并加测试：
  写指针后重新 `resolveDataRoot` 能读到；`PHL_DATA_DIR` 仍最优先。
- 登记里的 `data_root` 只用于「互相发现/安装时采用」，**不要**用它偷偷改 PHL 的
  数据根；PHL 的数据根仍由 `PHL_DATA_DIR` / `data-root.txt` / exe 同级 `data` 决定，
  三者与登记保持一致即可。
- 旧 profile 迁移源路径错（`migrateProfile` 拿到改向后的 profile）：一并修正，
  保证只复制、不删除。

### D. 数据格式统一（重点）

1. **`Timetable` / `agent/` 整文件重建**：
   - 找到写入点（`main.cjs` / `shared-sessions.cjs` / 相关模块），改成
     「读-改-写 + 保留未知字段」：只更新自己认识的字段，Lite 写入的额外字段原样保留。
   - 参考 `settings-yaml.cjs` 的块级编辑思路；JSON 可做浅合并（顶层键与条目内未知键）。
   - 加测试：文件里预置未知字段 → PHL 写入后未知字段仍在。
2. **`Schedule`**：
   - 保持 `shared-schedule.cjs` 的规则不变：只写 `day/time/title/note`（+`id/created`），
     不写 `repeatWeekdays`/`end`/提醒/颜色/附件；读取 Lite 写的条目时按
     `toLauncherEvents` 补 `end = start + 60`（最晚 23:59）。
   - 确认 `upsertEvents` / `removeEvents` 不会删掉未知字段；`lastId` 高水位继续维护。
3. **`settings.yaml`**：确认 `cloudsync.writeSettingsSection` / `replaceBlock`
   保留未知字段与注释（已有则加回归测试）。

### E. 测试与验收

最少包含：

- Node 单测：登记 INI 读写（保留对方节、原子、损坏容错、`installed=0`）、
  `data-root.txt` 指针读写、`Timetable`/`agent/` 未知字段保留。
- 用 Lite 仓库的 `scripts/test_schedule_compat.py` 生成的 `Schedule` 样例（或
  等价的 Lite 格式样本）验证 PHL `readSchedule`/`toLauncherEvents` 正常。
- Windows 实机/VM 安装测试：预置 `[phl-lite]` 登记 → 安装 PHL 不传 `/D`，
  确认默认目录 = Lite 目录、数据根采用、安装后 `[phl]` 写入且 `[phl-lite]` 保留；
  卸载后 `[phl]` 删除、`[phl-lite]` 与数据保留。
- 现有 `npm test` 全绿。

### F. 交付

- 分支 + PR（或按仓库约定提交），提交信息中文写「为什么」。
- PR 说明：改了哪些文件、安装器 hook 是否实测、测试命令与输出、未验证项。
- 不要修改 PHL Lite 仓库；不要直接上传服务器/发版。

## 约束

- 绝不删除用户数据；绝不 `RMDir /r` 安装目录；只删自己拥有的文件/注册键/登记节。
- 读取容错，写入原子；保留未知字段、注释、对方程序的节。
- Windows 路径含空格/中文；NSIS Unicode；macOS 只做应用启动写登记
  （没有安装器钩子，不做安装时联动）。
- 版本从 `package.json` 读取，不硬编码。

## 参考（PHL Lite 侧，只读）

- 规范：`D:\phl-lite-dev\docs\安装位置登记与数据统一.md`
- Lite 登记实现：`hellopinghe\install_registry.py`
- Lite 安装器：`installer\PingheLauncherLite.nsi`（`.onInit` / 写 `[phl-lite]` / 卸载删节）
- Lite 数据根解析：`hellopinghe\paths.py`
- Lite 测试：`scripts\test_install_registry.py`、`scripts\test_paths_data_root.py`、
  `scripts\test_schedule_compat.py`、`scripts\test_installer_nsis.py`
