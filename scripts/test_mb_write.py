"""ManageBac 写操作「纯函数」的离线单测（不联网、不需要账号）。

覆盖三件最容易出错、又最难靠人工点出来的事：

1. 上传表单的解析（必须原样照抄隐藏域，含 `_method=patch`）
2. 提交结果的判定（**"302 到登录页"绝不能被当成"已提交"**）
3. 云同步的方向选择（`prefer=local/remote` 只在两边都有内容时生效）

跑法：`python scripts/test_mb_write.py`（退出码 0 = 全过）
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from hellopinghe import cloudsync as cs            # noqa: E402
from hellopinghe.app import services as sv         # noqa: E402

FAILED: list[str] = []


def check(label: str, got, want) -> None:
    if got != want:
        FAILED.append(f"{label}\n    got : {got!r}\n    want: {want!r}")


def ok(label: str, cond: bool) -> None:
    if not cond:
        FAILED.append(f"{label}（期望为真）")


# ---------------------------------------------------------------- 夹具
# 与上游 PH-Launcher `tests/school-write.test.cjs` 同形的权威表单
DROPBOX_HTML = """
<html><head><meta name="csrf-token" content="META-TOK"></head><body>
<form action="/student/classes/21/core_tasks/31/dropbox/upload" method="post">
  <input type="hidden" name="_method" value="patch"/>
  <input type="hidden" name="authenticity_token" value="FORM-TOK"/>
  <input type="hidden" name="dropbox[assets_attributes][0][file_cache]" value=""/>
  <input type="file" name="dropbox[assets_attributes][0][file]"/>
  <input type="submit" name="commit" value="Upload Files"/>
</form>
<form action="/student/classes/21/core_tasks/31/dropbox" method="get">
  <input type="file" name="nope"/>
</form>
<a href="/student/classes/21/core_tasks/31/dropbox/upload">upload</a>
</body></html>
"""

CAS_HTML = """
<html><body>
<form action="/student/ib/activity/cas/experiences" method="post">
  <input type="hidden" name="authenticity_token" value="TOK">
  <input type="hidden" name="_method" value="patch">
  <label for="exp_title">经历标题</label>
  <input type="text" id="exp_title" name="experience[title]" required>
  <label for="exp_desc">描述</label>
  <textarea id="exp_desc" name="experience[description]"></textarea>
  <label for="exp_kind">类型</label>
  <select id="exp_kind" name="experience[kind]">
    <option value="a">创造</option><option value="b" selected>服务</option>
  </select>
  <input type="file" name="experience[evidence]">
  <input type="submit" name="commit" value="保存">
</form>
<form action="/student/ib/activity/cas/search" method="get"><input name="q"></form>
</body></html>
"""

# ---------------------------------------------------------------- 1 上传表单解析
entry = sv.parse_upload_form(DROPBOX_HTML, "dropbox")
ok("parse_upload_form 应找到表单", entry is not None)
if entry:
    check("文件字段名", entry["field"], "dropbox[assets_attributes][0][file]")
    check("action", entry["action"], "/student/classes/21/core_tasks/31/dropbox/upload")
    check("_method 必须原样照抄", entry["hidden"].get("_method"), "patch")
    check("file_cache 必须原样照抄",
          entry["hidden"].get("dropbox[assets_attributes][0][file_cache]"), "")
    check("表单自带 token 优先", entry["hidden"].get("authenticity_token"), "FORM-TOK")
    check("提交按钮并入表单数据", entry["hidden"].get("commit"), "Upload Files")

# GET 表单不能被当成上传入口
check("只认 method=post 的表单", sv.parse_upload_form(
    '<form action="/x/dropbox/upload" method="get"><input type="file" name="f"></form>',
    "dropbox"), None)
# 没有 file 字段的表单也不行
check("没有 file 字段的表单不算上传入口", sv.parse_upload_form(
    '<form action="/x/dropbox/upload" method="post"><input name="a"></form>',
    "dropbox"), None)
# 没有表单隐藏域 token 时，回落 meta
meta_only = sv.parse_upload_form(
    '<html><head><meta name="csrf-token" content="META-TOK"></head><body>'
    '<form action="/x/dropbox/upload" method="post">'
    '<input type="file" name="f"></form></body></html>', "dropbox")
check("无隐藏域 token 时回落 meta", (meta_only or {}).get("hidden", {}).get(
    "authenticity_token"), "META-TOK")
# 从链接里找候选页（不猜路由）
check("候选链接只取页面里已有的",
      sv.parse_form_links(DROPBOX_HTML, "dropbox"),
      ["/student/classes/21/core_tasks/31/dropbox/upload"])

# ---------------------------------------------------------------- 2 提交结果判定
check("302 到登录页 = 会话过期",
      sv.judge_submit_response(302, "/users/sign_in")[0], False)
check("302 到登录页的错误码",
      sv.judge_submit_response(302, "/login")[1], "login_required")
check("401 = 会话过期", sv.judge_submit_response(401, "")[0], False)
check("403 = 会话过期", sv.judge_submit_response(403, "")[1], "login_required")
check("302 回任务页 = 成功",
      sv.judge_submit_response(302, "/student/classes/21/core_tasks/31")[0], True)
check("200 任务页 = 成功", sv.judge_submit_response(200, "", "<h1>Task</h1>")[0], True)
check("422 = 失败", sv.judge_submit_response(422, "")[0], False)
check("500 = 失败", sv.judge_submit_response(500, "")[1], "failed")
check("200 但正文是登录页 = 会话过期", sv.judge_submit_response(
    200, "", '<html><input type="password" name="p"><p>Please sign in</p>'
              '<label>Remember me</label></html>')[1], "login_required")
# 真实任务页正文里出现 "sign in"（导航栏）不该被误判
check("正常任务页不会被误判成登录页", sv.judge_submit_response(
    200, "", '<html><h1>Coursework</h1><div class="dropbox">Uploaded</div>'
              '<a href="/login">Sign in</a></html>')[0], True)

# ---------------------------------------------------------------- 3 CAS 表单发现
forms = sv.parse_submittable_forms(CAS_HTML, "cas")
check("只挑出 POST 的 cas 表单", len(forms), 1)
if forms:
    f = forms[0]
    check("CAS 表单 action", f["action"], "/student/ib/activity/cas/experiences")
    check("CAS 表单有文件字段", f["has_file"], True)
    check("CAS 提交按钮", (f["submit_name"], f["submit_value"]), ("commit", "保存"))
    names = [x["name"] for x in f["fields"]]
    check("可填字段（隐藏域不出现）", names,
          ["experience[title]", "experience[description]", "experience[kind]",
           "experience[evidence]"])
    byname = {x["name"]: x for x in f["fields"]}
    check("label 取自 label[for]", byname["experience[title]"]["label"], "经历标题")
    check("required 被识别", byname["experience[title]"]["required"], True)
    check("select 默认选中项", byname["experience[kind]"]["value"], "b")
    check("select 选项数", len(byname["experience[kind]"]["options"]), 2)
check("没有可填字段的表单不算数",
      sv.parse_submittable_forms('<form method="post" action="/cas/x">'
                                 '<input type="hidden" name="t" value="1">'
                                 '</form>', "cas"), [])

# ---------------------------------------------------------------- 4 写路径白名单
ok("同子树放行", sv.core_action_allowed(
    "/student/ib/activity/cas/experiences", "/student/ib/activity/cas"))
ok("相对子路径放行", sv.core_action_allowed(
    "reflections", "/student/ib/activity/cas"))
ok("空 action 放行（提交回当前页）",
   sv.core_action_allowed("", "/student/ib/activity/cas"))
ok("EE 子树放行", sv.core_action_allowed("/student/ib/pbl/778/doc", "/student/ib/pbl/778"))
ok("拒绝外站", not sv.core_action_allowed(
    "https://evil.example.com/steal", "/student/ib/activity/cas"))
ok("拒绝同站其它路径", not sv.core_action_allowed(
    "/student/classes/21/dropbox/upload", "/student/ib/activity/cas"))

# ---------------------------------------------------------------- 5 同步方向
check("元信息不算内容", cs._is_empty_doc(
    {"updated_at": "2026-01-01", "events": []}), True)
check("有内容就是有内容", cs._is_empty_doc({"events": [{"id": 1}]}), False)
check("列表条数摘要", cs._brief_doc({"events": [1, 2, 3]}), "3 条")

merged, conf = cs._apply_preference(
    "schedule", "merge", {"a": 1}, {"b": 2}, {"a": 1, "b": 2}, [])
check("merge 不动结果", merged, {"a": 1, "b": 2})
merged, conf = cs._apply_preference(
    "schedule", "local", {"events": [1]}, {"events": [2]}, {"events": [1, 2]}, [])
check("local 用本地", merged, {"events": [1]})
ok("local 记一条冲突说明", conf and "本地数据覆盖" in conf[0]["note"])
merged, conf = cs._apply_preference(
    "schedule", "remote", {"events": [1]}, {"events": [2]}, {"events": [1, 2]}, [])
check("remote 用云端", merged, {"events": [2]})
# 空的一边没有"覆盖"的资格
merged, conf = cs._apply_preference(
    "schedule", "local", {}, {"events": [2]}, {"events": [2]}, [])
check("本地为空时不覆盖（退回合并）", merged, {"events": [2]})
merged, conf = cs._apply_preference(
    "schedule", "remote", {"events": [1]}, None, {"events": [1]}, [])
check("云端为空时不覆盖（退回合并）", merged, {"events": [1]})
merged, conf = cs._apply_preference(
    "schedule", "local", None, {"events": [2]}, {"events": [2]}, [])
check("本地整份缺失时不覆盖", merged, {"events": [2]})

# ---------------------------------------------------------------- 结果
if FAILED:
    print(f"✗ {len(FAILED)} 项失败：\n")
    for item in FAILED:
        print("  · " + item)
    sys.exit(1)
print("✓ 全部通过（上传表单解析 / 提交判定 / CAS 表单发现 / 白名单 / 同步方向）")
