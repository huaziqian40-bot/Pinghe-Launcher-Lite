"""Hello Pinghe! Launcher CLI —— M1 核心验证入口.

用法示例:
    python -m hellopinghe.cli probe --url https://shph.managebac.cn
    python -m hellopinghe.cli login --url https://shph.managebac.cn
    python -m hellopinghe.cli classes
    python -m hellopinghe.cli ddl --days 14 --save
    python -m hellopinghe.cli timetable --days 7
    python -m hellopinghe.cli preset deepseek
"""
from __future__ import annotations

import argparse
import getpass
import json
import sys
from datetime import date, timedelta
from pathlib import Path

from .config import CONFIG_PATH, PROVIDER_PRESETS, Config
from .exceptions import LoginError, PingheError
from .managebac.client import ManageBacClient
from .storage import connect, save_classes, save_deadlines, upcoming_deadlines

from . import paths as _paths
SESSION_DIR = _paths.data_dir()


def _session_path(client: ManageBacClient) -> Path:
    host = client.base_url.split("//")[-1].replace(":", "_")
    return SESSION_DIR / f"session_{host}.json"


def save_session(client: ManageBacClient) -> None:
    SESSION_DIR.mkdir(parents=True, exist_ok=True)
    _session_path(client).write_text(
        json.dumps({"cookies": client.session.cookies.get_dict()}, indent=2),
        encoding="utf-8",
    )


def load_client(cfg: Config) -> ManageBacClient:
    if not cfg.managebac_base_url:
        raise PingheError("还没配置 ManageBac 地址: hellopinghe login --url https://你的学校.managebac.cn")
    client = ManageBacClient(cfg.managebac_base_url)
    path = _session_path(client)
    if path.exists():
        for name, value in json.loads(path.read_text(encoding="utf-8"))["cookies"].items():
            client.session.cookies.set(name, value)
    return client


# ------------------------------------------------------------------ 子命令
def cmd_probe(args: argparse.Namespace) -> int:
    client = ManageBacClient(args.url)
    p = client.probe_login()
    print(f"状态码    : {p.status}")
    print(f"登录页    : {p.url}")
    print(f"表单action: {p.form_action}")
    print(f"账号字段  : {'✓' if p.has_login_field else '✗'}   密码字段: {'✓' if p.has_password_field else '✗'}")
    print(f"CSRF token: {'✓ 纯HTTP登录可行' if p.has_csrf else '✗ 需要回退网页登录'}")
    print(f" cookies  : {', '.join(p.cookies)}")
    return 0 if p.has_csrf else 2


def cmd_login(args: argparse.Namespace) -> int:
    cfg = Config.load()
    client = ManageBacClient(args.url)
    email = args.email or input("ManageBac 邮箱: ")
    password = getpass.getpass("ManageBac 密码(不落盘): ")
    try:
        client.login(email, password)
    except LoginError as exc:
        print(f"✗ 登录失败: {exc}")
        return 1
    save_session(client)
    cfg.managebac_base_url = client.base_url
    cfg.managebac_email = email
    cfg.save()
    print(f"✓ 登录成功,会话已保存到 {_session_path(client)}(密码未保存)")
    return 0


def cmd_classes(_: argparse.Namespace) -> int:
    client = load_client(Config.load())
    classes = client.get_classes()
    for cid, name in classes.items():
        print(f"{cid:>10}  {name}")
    print(f"共 {len(classes)} 门课")
    return 0


def cmd_ddl(args: argparse.Namespace) -> int:
    cfg = Config.load()
    client = load_client(cfg)
    items = client.get_deadlines(days_ahead=args.days)
    for it in items:
        due = it.due_at.strftime("%m-%d %H:%M") if it.due_at else "??"
        print(f"[{it.category:^8}] {due}  {(it.course or '-'):<20} {it.title}  ({it.status or '?'})")
    print(f"共 {len(items)} 条(未来 {args.days} 天)")
    if args.save:
        conn = connect()
        n = save_deadlines(conn, client.base_url, items)
        print(f"已写入本地数据库 {n} 条 → {SESSION_DIR / 'hellopinghe.db'}")
    return 0


def cmd_grades(_: argparse.Namespace) -> int:
    client = load_client(Config.load())
    grades = client.get_overall_grades()
    for name, grade in grades.items():
        print(f"{name:<30} {grade or '(未出分)'}")
    return 0


def cmd_timetable(args: argparse.Namespace) -> int:
    from . import edupage as ep

    cfg = Config.load()
    if not (cfg.edupage_username or args.username):
        raise PingheError("先在 ~/.hellopinghe/config.json 填 edupage_username,或用 --username 传入")
    username = args.username or cfg.edupage_username
    password = getpass.getpass("Edupage 密码(不落盘): ")
    account = ep.login(username, password, cfg.edupage_subdomain)

    for offset in range(args.days):
        day = date.today() + timedelta(days=offset)
        lessons = ep.fetch_timetable(account, day)
        print(f"\n== {day} ({'周' + '一二三四五六日'[day.weekday()]}) ==")
        if not lessons:
            print("  (无课)")
        for l in lessons:
            mark = " [已取消]" if l.cancelled else ""
            print(f"  {l.start}-{l.end}  {l.subject:<16} {l.teacher:<10} {l.classroom}{mark}")
    return 0


def cmd_preset(args: argparse.Namespace) -> int:
    cfg = Config.load()
    if args.name:
        if args.name not in PROVIDER_PRESETS:
            print(f"未知预设,可选: {', '.join(PROVIDER_PRESETS)}")
            return 1
        tpl = PROVIDER_PRESETS[args.name]
        pid = f"p-{args.name}"
        cfg.ai_providers = [
            p for p in cfg.ai_providers if p.get("id") != pid
        ] + [{
            "id": pid, "name": tpl.name, "protocol": "openai",
            "base_url": tpl.base_url, "api_key": tpl.api_key,
            "models": [tpl.model] if tpl.model else [], "notes": tpl.notes,
        }]
        cfg.agent_provider_id = pid
        cfg.agent_model = tpl.model
        cfg.save()
    a = cfg.active_provider()
    print(f"当前 AI: {a.get('name', '?')}\n  protocol: {a.get('protocol')}\n  base_url: {a.get('base_url')}\n  model   : {cfg.agent_model or '(未设)'}\n  api_key : {'已填' if a.get('api_key') else '(待填)'}")
    print(f"配置文件: {CONFIG_PATH}")
    return 0


def main(argv: list[str] | None = None) -> int:
    # Windows 管道默认 GBK, 强制 UTF-8 避免中文/符号乱码
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")

    parser = argparse.ArgumentParser(prog="hellopinghe", description="本地 ManageBac + Edupage 助手")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("probe", help="探测 ManageBac 登录页(无需账号)")
    p.add_argument("--url", required=True)
    p.set_defaults(fn=cmd_probe)

    p = sub.add_parser("login", help="账密登录并保存会话")
    p.add_argument("--url", required=True)
    p.add_argument("--email", default="")
    p.set_defaults(fn=cmd_login)

    sub.add_parser("classes", help="列出课程").set_defaults(fn=cmd_classes)

    p = sub.add_parser("ddl", help="统计未来 N 天 DDL")
    p.add_argument("--days", type=int, default=14)
    p.add_argument("--save", action="store_true", help="写入本地 SQLite")
    p.set_defaults(fn=cmd_ddl)

    sub.add_parser("grades", help="各科总评").set_defaults(fn=cmd_grades)

    p = sub.add_parser("timetable", help="Edupage 课表")
    p.add_argument("--days", type=int, default=7)
    p.add_argument("--username", default="")
    p.set_defaults(fn=cmd_timetable)

    p = sub.add_parser("preset", help="查看/切换 Agent 供应商预设")
    p.add_argument("name", nargs="?", default="")
    p.set_defaults(fn=cmd_preset)

    args = parser.parse_args(argv)
    try:
        return args.fn(args)
    except PingheError as exc:
        print(f"✗ {exc}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
