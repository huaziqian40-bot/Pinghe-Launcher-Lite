# -*- coding: utf-8 -*-
"""邮件「回复 / 转发 / 带附件发信」自检(不需要网络、不需要真实邮箱账号)。

    python scripts/test_mail_compose.py

覆盖:
1. 兼容性: 老的 `send(to, subject, body)` 三参数调用照旧可用;
2. MIME 组信: 正文 text/plain+utf-8、From/To/Cc 头、Date/Message-ID;
3. **Bcc 只进信封、不进头部**;
4. 附件: Content-Disposition: attachment、base64、顺序在正文之后、
   mimetypes 猜类型失败退 application/octet-stream;
5. **中文附件名 RFC 2231**(as_string() 之后再解析回来还是中文);
6. 附件限额: 单个 / 合计 / 个数 超限给**中文可读报错**, 不静默丢弃;
7. 附件两种来源: 文件路径 与 前端传的 data_base64;
8. 回复预填: Reply-To 优先(没有才用 From)、`Re: ` 不重复叠、引用块结构、
   **不带**原附件;
9. 转发预填: 收件人留空、`Fwd: `、引用块、**带上原附件**;
10. 引用正文取 text/plain 部分(不是 HTML、不是附件);
11. 收件人解析: 逗号/分号/全角标点混用、非法地址报中文错;
12. SMTP 信封: 一次性 stub 掉 smtplib, 断言 sendmail 的信封含全部收件人
    (含 Bcc)而正文头部不含 Bcc —— 不连任何真实服务器。

**全部用假凭据**; 不碰 `data/` 目录, 不写任何用户数据。
"""
from __future__ import annotations

import base64
import email
import email.policy
import re
import smtplib
import sys
import tempfile
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from hellopinghe.app import services as S  # noqa: E402
from hellopinghe.config import Config  # noqa: E402
from hellopinghe.exceptions import PingheError  # noqa: E402

SENDER = "sender@example.test"
FAKE_CODE = "fake-authcode-not-a-real-credential"


def check(cond: bool, msg: str) -> None:
    if not cond:
        raise AssertionError(msg)


def svc() -> S.MailService:
    """一个假的账号配置(地址与授权码都是编的, 不会连任何服务器)."""
    cfg = Config()
    cfg.mail_email = SENDER
    cfg.mail_imap_host = "imap.example.test"
    cfg.mail_smtp_host = "smtp.example.test"
    m = S.MailService(cfg)
    m._smtp_password = lambda: FAKE_CODE  # type: ignore[method-assign]
    return m


def reparse(msg) -> email.message.Message:
    """as_string() 之后重新解析 —— 模拟收件端真正看到的东西。

    只看内存里那份 Message 是查不出"折行把中文附件名折断了"这类问题的,
    所以每条断言都走一遍序列化。
    """
    return email.message_from_string(msg.as_string())


def parts_of(msg) -> list:
    return [p for p in msg.walk() if p.get_content_maintype() != "multipart"]


def payload_of(part) -> bytes:
    """附件字节 —— 换行统一成 \\n 再比。

    `Message.as_string()` 在 Windows 上按 MIME 要求把行尾写成 CRLF, 而附件里
    原本可能是 LF; 这是序列化层的正常行为(标准文本邮件同理), 不该算内容损坏。
    """
    return (part.get_payload(decode=True) or b"").replace(b"\r\n", b"\n")


def find_att(msg, name: str):
    for p in msg.walk():
        if p.get_filename() == name:
            return p
    return None


# ---------------------------------------------------------------- 1. 兼容性
def test_backward_compatible_signature() -> None:
    m = svc()
    msg, envelope, skipped = m.build_message("a@example.test", "主题", "正文")
    check(envelope == ["a@example.test"], "只有 To 时信封就是那一个地址")
    check(skipped == [], "没有附件就没有 skipped")
    check(msg["Subject"] == "主题" or "=?utf-8?" in str(msg["Subject"]),
          "中文主题要么原样要么 RFC2047 编码")
    check(msgs := reparse(msg), "能重新解析")
    check(msgs["To"] == "a@example.test", "To 头写对了")
    check(msgs["From"] == SENDER, "From 头是发件邮箱")
    check(msgs.get_content_type() == "multipart/mixed", "组信壳是 multipart/mixed")
    # 老的三参数调用: 位置参数照旧
    import inspect

    sig = inspect.signature(S.MailService.send)
    positional = [p.name for p in sig.parameters.values()
                  if p.kind in (p.POSITIONAL_ONLY, p.POSITIONAL_OR_KEYWORD)]
    check(positional[:4] == ["self", "to", "subject", "body"],
          f"send 的前三个位置参数必须还是 to/subject/body, 实际 {positional}")
    for name in ("cc", "bcc", "attachments", "in_reply_to", "references"):
        check(sig.parameters[name].kind == inspect.Parameter.KEYWORD_ONLY,
              f"{name} 必须是关键字参数(老调用方不受影响)")
        check(sig.parameters[name].default is not inspect.Parameter.empty,
              f"{name} 必须有默认值")


# ---------------------------------------------------- 2~7. 组信 / 附件 / 限额
def test_recipients_and_headers() -> None:
    m = svc()
    msg, envelope, _ = m.build_message(
        "A <a@example.test>, b@example.test", "S", "B",
        cc="c@example.test; d@example.test", bcc="blind@example.test")
    check(envelope == ["a@example.test", "b@example.test", "c@example.test",
                       "d@example.test", "blind@example.test"],
          f"信封要含 To+Cc+Bcc, 实际 {envelope}")
    back = reparse(msg)
    check(back["To"] == "a@example.test, b@example.test", "To 头规范化成地址列表")
    check(back["Cc"] == "c@example.test, d@example.test", "Cc 头写对了")
    check(back["Bcc"] is None, "Bcc 绝不能出现在头部")
    check("blind@example.test" not in msg.as_string().split("\n\n", 1)[0],
          "头部原文里也不能有 Bcc 地址")
    check(back["Date"], "要有 Date 头")
    check(back["Message-ID"] and "@" in back["Message-ID"], "要有 Message-ID 头")


def test_body_is_utf8_plaintext() -> None:
    m = svc()
    body = "第一行\n第二行 · 中文标点，还有 emoji 🙂"
    msg, _, _ = m.build_message("a@example.test", "s", body)
    back = reparse(msg)
    part = [p for p in back.walk() if p.get_content_type() == "text/plain"]
    check(len(part) == 1, f"正文只能有一个 text/plain 部分, 实际 {len(part)}")
    check(part[0].get_content_charset() == "utf-8", "正文声明 utf-8")
    check(part[0].get_payload(decode=True).decode("utf-8") == body,
          "正文逐字节还原(中文/emoji 都不许坏)")


def test_attachments_mime_and_order() -> None:
    m = svc()
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / "note.txt").write_text("你好 attachment\n", encoding="utf-8")
        (root / "data.bin").write_bytes(bytes(range(256)) * 3)
        raw = (root / "data.bin").read_bytes()
        msg, envelope, skipped = m.build_message(
            "a@example.test", "带附件", "看附件",
            attachments=[str(root / "note.txt"), str(root / "data.bin")])
        check(skipped == [], "正常附件不该被跳过")
        check(envelope == ["a@example.test"], "附件不影响信封")
        back = reparse(msg)
        flats = parts_of(back)
        check(len(flats) == 3, f"1 正文 + 2 附件 = 3 个叶子部分, 实际 {len(flats)}")
        check(flats[0].get_content_type() == "text/plain", "正文排在最前面")

        txt = find_att(back, "note.txt")
        check(txt is not None, "note.txt 附件在")
        check(str(txt.get("Content-Disposition")).startswith("attachment"),
              "Content-Disposition 是 attachment")
        check("base64" in str(txt.get("Content-Transfer-Encoding")).lower(),
              "附件是 base64 编码")
        check(payload_of(txt).decode("utf-8") == "你好 attachment\n",
              f"附件内容逐字节还原, 实际 {payload_of(txt)!r}")
        check(txt.get_content_type() == "text/plain", "note.txt 猜成 text/plain")
        binary = find_att(back, "data.bin")
        check(binary is not None, "data.bin 附件在")
        ctype = binary.get_content_type()
        check(ctype in ("application/octet-stream", "application/bin"),
              f".bin 应退到 application/octet-stream, 实际 {ctype}")
        check(payload_of(binary) == raw,
              "二进制附件(含全部 256 个字节值)逐字节还原")

        # 序列化后的报文里就该是 base64 文本(收件端按 base64 解);
        # encode_base64 每 76 字符折一行, 比对时先去掉换行。
        flat = msg.as_string().replace("\r\n", "").replace("\n", "")
        check(base64.b64encode(raw).decode() in flat,
              "序列化后的报文里能直接看到该附件的 base64 段")


def test_chinese_attachment_filename() -> None:
    m = svc()
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        name = "期末成绩单 2026（最终版）.pdf"
        (root / name).write_bytes(b"%PDF-1.4 fake\n")
        msg, _, _ = m.build_message("a@example.test", "s", "b",
                                    attachments=[str(root / name)])
        back = reparse(msg)
        att = find_att(back, name)
        check(att is not None,
              f"中文附件名 round-trip 之后必须还能原样读出来, "
              f"实际拿到 {[p.get_filename() for p in back.walk() if p.get_filename()]}")
        disp = str(att.get("Content-Disposition"))
        check("filename*=" in disp or "=?utf-8?" in disp,
              f"非 ASCII 名字要走 RFC 2231 / RFC 2047 编码, 实际 {disp!r}")
        check(att.get_payload(decode=True) == b"%PDF-1.4 fake\n", "内容没坏")


def test_attachment_limits_and_bytes_source() -> None:
    m = svc()
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        # (a) data_base64 来源(前端把 File 读成 base64 时走这条)
        blob = b"hello from base64\n" * 4
        msg, _, skipped = m.build_message(
            "a@example.test", "s", "b",
            attachments=[{"name": "前端选的.txt",
                          "data_base64": base64.b64encode(blob).decode()}])
        check(skipped == [], "base64 来源不该被跳过")
        att = find_att(reparse(msg), "前端选的.txt")
        check(att is not None and payload_of(att) == blob,
              "base64 来源的附件内容要还原")

        # (b) 路径不存在 → 记进 skipped, 给中文原因(不静默丢)
        _, _, skipped = m.build_message(
            "a@example.test", "s", "b",
            attachments=[str(root / "不存在的文件.txt")])
        check(len(skipped) == 1 and "不存在" in skipped[0]["reason"],
              f"读不到的附件要如实上报原因, 实际 {skipped}")

        # (c) 单个超限: 把上限临时调小, 免得真造一个 20MB 文件
        (root / "big.bin").write_bytes(b"x" * 4096)
        saved_one, saved_total = S.MAIL_ATTACH_MAX_ONE, S.MAIL_ATTACH_MAX_TOTAL
        try:
            S.MAIL_ATTACH_MAX_ONE = 1024
            S.MAIL_ATTACH_MAX_TOTAL = 100 * 1024
            _, _, skipped = m.build_message(
                "a@example.test", "s", "b", attachments=[str(root / "big.bin")])
            check(len(skipped) == 1 and "单个" in skipped[0]["reason"],
                  f"单个超限要报「单个附件上限」, 实际 {skipped}")
            check("4.0KB" in skipped[0]["reason"] or "4KB" in skipped[0]["reason"]
                  or "KB" in skipped[0]["reason"],
                  f"错误里要有可读大小, 实际 {skipped[0]['reason']}")

            # (d) 合计超限: 3 个 800B, 合计上限 1000B
            S.MAIL_ATTACH_MAX_ONE = 100 * 1024
            S.MAIL_ATTACH_MAX_TOTAL = 1000
            for i in range(3):
                (root / f"p{i}.bin").write_bytes(b"y" * 800)
            kept, skipped2 = m._collect_attachments(
                [str(root / f"p{i}.bin") for i in range(3)])
            check(len(kept) == 1, f"只装得下 1 个, 实际 {len(kept)}")
            check(len(skipped2) == 2 and all("总上限" in s["reason"] for s in skipped2),
                  f"合计超限的名额要带中文原因, 实际 {skipped2}")
        finally:
            S.MAIL_ATTACH_MAX_ONE, S.MAIL_ATTACH_MAX_TOTAL = saved_one, saved_total

        # (e) 个数超限: 直接抛中文错误(不是悄悄少带几个)
        saved_cnt = S.MAIL_ATTACH_MAX_COUNT
        try:
            S.MAIL_ATTACH_MAX_COUNT = 2
            try:
                m._collect_attachments([{"name": f"f{i}", "data_base64": ""}
                                        for i in range(3)])
                raise AssertionError("超过个数上限必须抛错, 不许静默丢弃")
            except PingheError as exc:
                check("最多" in str(exc) and "个附件" in str(exc),
                      f"个数超限要是可读中文, 实际 {exc}")
        finally:
            S.MAIL_ATTACH_MAX_COUNT = saved_cnt


def test_recipient_parsing_errors() -> None:
    m = svc()
    check(m._parse_addresses("a@x.test, b@y.test; c@z.test") ==
          ["a@x.test", "b@y.test", "c@z.test"], "逗号/分号混用要拆对")
    check(m._parse_addresses("甲 <a@x.test>，乙 <b@y.test>") ==
          ["a@x.test", "b@y.test"], "全角逗号 + 显示名要拆对")
    check(m._parse_addresses("") == [], "空串是空列表")
    check(m._parse_addresses("不是邮箱") == [], "没有 @ 的不算地址")
    try:
        m.build_message("", "s", "b")
        raise AssertionError("一个收件人都没有时必须报错")
    except PingheError as exc:
        check("收件人" in str(exc), f"空收件人错误要可读, 实际 {exc}")


# ------------------------------------------------- 8~10. 回复 / 转发预填
def sample_original() -> email.message.Message:
    """一封"收到的"邮件: 中文主题/正文 + HTML 替身 + 一个中文名附件."""
    msg = MIMEMultipart()
    msg["From"] = "王老师 <teacher@example.test>"
    msg["Reply-To"] = "reply-here@example.test"
    msg["To"] = SENDER
    msg["Subject"] = "关于下周的实验课"
    msg["Date"] = "Mon, 08 Sep 2026 09:30:00 +0800"
    msg["Message-ID"] = "<orig-123@example.test>"
    msg["References"] = "<thread-root@example.test>"
    msg.attach(MIMEText("第一行原文\n第二行原文\n第三行原文\n", "plain", "utf-8"))
    msg.attach(MIMEText("<p>这是 HTML 替身, 不该被引用</p>", "html", "utf-8"))
    from email.mime.base import MIMEBase
    from email import encoders

    part = MIMEBase("application", "pdf")
    part.set_payload(b"%PDF-1.4 original\n")
    encoders.encode_base64(part)
    part.add_header("Content-Disposition", "attachment", filename="实验报告.pdf")
    msg.attach(part)
    return email.message_from_string(msg.as_string())


def test_quote_text_structure() -> None:
    q = S.mail_quote_text("2026-09-08 09:30", "王老师", "A\nB")
    check(q.startswith("\n\n---------- 原始邮件 ----------\n"),
          f"引用块开头照抄参考实现的结构, 实际 {q!r}")
    check("2026-09-08 09:30 时 王老师 写道：" in q, "日期 + 发件人 一行(中文文案)")
    check("> A\n> B" in q, "原文逐行加 > ")
    check(q.rstrip("\n").endswith("------------------"), "引用块结尾横线")
    check(q.endswith("\n"), "引用块末尾要有换行(光标才好停在它上方)")
    check(S.mail_quote_text("", "", "") .count(">") == 0, "空正文不产生空的 > 行")


def test_reply_prefill() -> None:
    m = svc()
    p = m.reply_prefill(sample_original())
    check(p["mode"] == "reply", "模式是 reply")
    check(p["to"] == "reply-here@example.test",
          f"有 Reply-To 时必须优先用它, 实际 {p['to']}")
    check(p["subject"] == "Re: 关于下周的实验课",
          f"主题加 Re: 前缀, 实际 {p['subject']}")
    check(p["body"].startswith("\n\n"), "正文开头是引用块前的空行(光标落其上方)")
    check("> 第一行原文\n> 第二行原文" in p["body"], "引用正文逐行加 >")
    check("HTML 替身" not in p["body"], "引用正文取 text/plain, 不许引用 HTML 替身")
    check("实验报告.pdf" not in p["body"], "正文里不该出现附件内容")
    check(p["attachments"] == [], "回复**不**自动带原附件")
    check(p["in_reply_to"] == "<orig-123@example.test>", "带上 In-Reply-To 便于串线")
    check(p["references"] == "<thread-root@example.test>", "带上原本的 References")

    # Reply-To 缺失 → 回退到 From 里的地址
    msg = sample_original()
    del msg["Reply-To"]
    p2 = m.reply_prefill(msg)
    check(p2["to"] == "teacher@example.test",
          f"没有 Reply-To 就回退 From, 实际 {p2['to']}")
    check("王老师" in p2["quote"], "引用块里保留 From 的显示名")

    # 主题已经有 Re: 就不叠第二层(忽略大小写)
    for subj in ("Re: 关于下周的实验课", "RE: 关于下周的实验课", "re:关于下周的实验课"):
        msg = sample_original()
        del msg["Subject"]
        msg["Subject"] = subj
        p3 = m.reply_prefill(msg)
        check(p3["subject"] == subj,
              f"已经是 {subj!r} 就不该再叠 Re:, 实际 {p3['subject']!r}")


def test_forward_prefill() -> None:
    m = svc()
    p = m.forward_prefill(sample_original())
    check(p["mode"] == "forward", "模式是 forward")
    check(p["to"] == "", "转发不预填收件人(焦点留给收件人框)")
    check(p["subject"] == "Fwd: 关于下周的实验课",
          f"主题加 Fwd: 前缀, 实际 {p['subject']}")
    check("> 第一行原文" in p["body"], "转发也带引用块")
    check(len(p["attachments"]) == 1,
          f"转发必须带上原附件(参考实现这里留了 TODO), 实际 {p['attachments']}")
    att = p["attachments"][0]
    check(att["name"] == "实验报告.pdf", f"附件名要保住中文, 实际 {att['name']}")
    check(att.get("size") == len(b"%PDF-1.4 original\n"),
          f"附件要带大小(界面靠它显示 KB), 实际 {att.get('size')}")
    check(base64.b64decode(att["data_base64"]) == b"%PDF-1.4 original\n",
          "转发附件内容逐字节还原")
    check(p["skipped"] == [], "正常附件不该被跳过")

    # 已经有 Fwd: 就不叠
    msg = sample_original()
    del msg["Subject"]
    msg["Subject"] = "Fwd: 关于下周的实验课"
    check(m.forward_prefill(msg)["subject"] == "Fwd: 关于下周的实验课",
          "已有 Fwd: 不许再叠一层")

    # 转发**可以**直接把预填的附件拿去发信 —— 端到端串一遍
    msg2, envelope, skipped = m.build_message(
        "a@example.test", p["subject"], p["body"], attachments=p["attachments"])
    check(skipped == [], "预填附件拿去发信不该被跳过")
    back = reparse(msg2)
    check(find_att(back, "实验报告.pdf") is not None,
          "转发出来的信里真的带着原附件")
    check("> 第一行原文" in
          [x for x in parts_of(back) if x.get_content_type() == "text/plain"][0]
          .get_payload(decode=True).decode("utf-8"),
          "转发出来的信里正文带着引用块")


def test_plain_body_fallbacks() -> None:
    # 只有 HTML 的邮件: 退化成去标签的文本
    msg = MIMEMultipart()
    msg.attach(MIMEText("<p>只<br>有<br>HTML</p>", "html", "utf-8"))
    got = S.mail_plain_body(email.message_from_string(msg.as_string()))
    check("只" in got and "有" in got and "<p>" not in got,
          f"只有 HTML 时要退化成纯文本, 实际 {got!r}")

    # 单部分纯文本邮件
    plain = MIMEText("单部分正文", "plain", "utf-8")
    check("单部分正文" in S.mail_plain_body(email.message_from_string(plain.as_string())),
          "单部分纯文本邮件要读得出正文")

    # 附件不是正文来源
    only_att = MIMEMultipart()
    from email.mime.base import MIMEBase
    from email import encoders

    p = MIMEBase("text", "plain")
    p.set_payload(b"ATTACHMENT-CONTENT-SHOULD-NOT-BE-QUOTED")
    encoders.encode_base64(p)
    p.add_header("Content-Disposition", "attachment", filename="a.txt")
    only_att.attach(p)
    got = S.mail_plain_body(email.message_from_string(only_att.as_string()))
    check("SHOULD-NOT-BE-QUOTED" not in got, "带 attachment 的 text/plain 不算正文")


# ---------------------------------------------------------------- 12. 信封
def test_smtp_envelope_gets_bcc_but_headers_do_not() -> None:
    """stub 掉 smtplib(不建连接), 只看交给服务器的那两样: 信封 + 报文."""
    m = svc()
    captured: dict = {}

    class FakeSMTP:
        def __init__(self, host, port, timeout=None):
            captured["host"] = host
            captured["port"] = port

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def login(self, user, password):
            captured["login"] = (user, password)

        def sendmail(self, from_addr, to_addrs, msg_text):
            captured["from"] = from_addr
            captured["to"] = list(to_addrs)
            captured["raw"] = msg_text

    real = smtplib.SMTP_SSL
    smtplib.SMTP_SSL = FakeSMTP
    try:
        with tempfile.TemporaryDirectory() as tmp:
            att = Path(tmp) / "报告.txt"
            att.write_bytes("附件正文\n".encode())
            out = m.send("a@example.test", "主题", "正文",
                         cc="c@example.test", bcc="blind@example.test",
                         attachments=[str(att)])
    finally:
        smtplib.SMTP_SSL = real

    check(out["sent"] is True, "发送成功要返回 sent")
    check(captured["host"] == "smtp.example.test" and captured["port"] == 994,
          f"用配置里的 SMTP 主机与隐式 SSL 端口, 实际 {captured}")
    check(captured["login"] == (SENDER, FAKE_CODE), "用发件邮箱 + 授权码登录")
    check(captured["from"] == SENDER, "信封发件人是发件邮箱")
    check(captured["to"] == ["a@example.test", "c@example.test",
                             "blind@example.test"],
          f"信封收件人必须含 Bcc, 实际 {captured['to']}")
    head = captured["raw"].split("\n\n", 1)[0]
    check("blind@example.test" not in head,
          "Bcc 地址绝不能出现在报文头部(Bcc 的语义)")
    check("报告.txt" in captured["raw"].replace("\n", "").replace(" ", "") or
          "=?utf-8?" in captured["raw"], "附件名进了报文")
    check(out["envelope"] == captured["to"], "返回值里的信封与实际发送一致")

    # 没配邮箱 → 中文可读报错(不是崩栈)
    empty = S.MailService(Config())
    for kwargs, needle in (({}, "设置页"),):
        try:
            empty.send("a@example.test", "s", "b", **kwargs)
            raise AssertionError("没配邮箱时必须报错")
        except PingheError as exc:
            check(needle in str(exc), f"错误里要指明去哪配, 实际 {exc}")


# ------------------------------------------------- 13. 桥接层端到端(假 IMAP)
class FakeIMAP:
    """假的 IMAP 连接: 只回 FETCH 请求的原始报文, 不连任何服务器.

    `STORE`/`LOGOUT`/`SELECT` 都照 imaplib 的形状返回, 好让 bridge 里
    "标记已读 + 收尾登出"的代码原样跑一遍。
    """

    def __init__(self, raw: bytes):
        self.raw = raw
        self.stored: list[tuple] = []
        self.logged_out = False

    def uid(self, command, *args):
        if command == "FETCH":
            return "OK", [(b'1 (UID 1 FLAGS ())', self.raw)]
        if command == "STORE":
            self.stored.append(args)
            return "OK", [b""]
        return "OK", [b""]

    def select(self, folder, readonly=False):  # noqa: ARG002
        return "OK", [b"1"]

    def logout(self):
        self.logged_out = True
        return "BYE", [b""]


class FakeSMTPCollect:
    """假的 SMTP_SSL: 记下信封与报文, 不连服务器."""

    def __init__(self):
        self.calls: list[dict] = []

    def __call__(self, host, port, timeout=None):  # noqa: ARG002
        outer = self

        class Conn:
            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

            def login(self, user, password):
                outer.calls.append({"login": (user, password)})

            def sendmail(self, from_addr, to_addrs, msg_text):
                outer.calls.append({"from": from_addr, "to": list(to_addrs),
                                    "raw": msg_text})

        return Conn()


def test_bridge_end_to_end_with_fake_imap() -> None:
    """bridge 的 mail_prefill / mail_send 串起来跑(假 IMAP + 假 SMTP).

    这一条覆盖"点转发 → Python 取原信 → 预填(含原附件 base64) → 前端发回
    → 真正发出去"的整条链路, 以及 bridge 里"标记已读 + 收尾 logout"。
    """
    from hellopinghe.app import bridge as B
    from hellopinghe.app.bridge import Api

    raw = sample_original().as_string().encode("utf-8")
    cfg = Config()
    cfg.mail_email = SENDER
    cfg.mail_smtp_host = "smtp.example.test"
    service = S.MailService(cfg)
    service._smtp_password = lambda: FAKE_CODE  # type: ignore[method-assign]

    conns: list[FakeIMAP] = []

    def fake_conn():
        c = FakeIMAP(raw)
        conns.append(c)
        return c

    service._conn = fake_conn  # type: ignore[method-assign]

    api = Api.__new__(Api)          # 不走 __init__: 不读配置、不建目录
    api.cfg = cfg
    svc_holder = type("S", (), {})()
    svc_holder.mail = service
    api.svc = svc_holder

    # --- 转发预填 ---------------------------------------------------------
    out = api.mail_prefill("1", "forward")
    check(out.get("ok") is True, f"bridge 转发预填要成功, 实际 {out}")
    pre = out["data"]
    check(pre["mode"] == "forward" and pre["to"] == "", "转发的收件人留空")
    check(pre["subject"] == "Fwd: 关于下周的实验课", f"转发主题, 实际 {pre['subject']}")
    check(conns and conns[0].stored, "取原信时应顺手把该邮件标成已读")
    check(conns[0].logged_out, "用完要 logout 收尾(不占着 IMAP 连接)")
    check(len(pre["attachments"]) == 1, "转发预填要带上原附件")

    # --- 把这个预填拿去发: 就是前端 mail_send 做的事 ----------------------
    import json as _json

    specs = _json.dumps([{"name": a["name"], "data_base64": a["data_base64"]}
                         for a in pre["attachments"]])
    collector = FakeSMTPCollect()
    real = smtplib.SMTP_SSL
    smtplib.SMTP_SSL = collector
    try:
        sent = api.mail_send("new@example.test", pre["subject"], pre["body"],
                             "", "", specs)
    finally:
        smtplib.SMTP_SSL = real
    check(sent.get("ok") is True, f"发送要成功, 实际 {sent}")
    check(sent["data"]["sent"] is True, "返回值里 sent=True")
    check(collector.calls, "确实调用了 SMTP")
    body = collector.calls[-1]
    check(body["to"] == ["new@example.test"], "信封里有收件人")
    flat = body["raw"].replace("\r\n", "\n")
    back = email.message_from_string(body["raw"])
    check("> 第一行原文" in
          [p for p in back.walk() if p.get_content_type() == "text/plain"
           and p.get_filename() is None][0].get_payload(decode=True).decode("utf-8"),
          "转发出去的信里正文带着引用块")
    check(find_att(back, "实验报告.pdf") is not None,
          f"转发出去的信里带着原附件, 报文={flat[:400]!r}")

    # --- 回复预填: 不带原附件 --------------------------------------------
    out2 = api.mail_prefill("1", "reply")
    check(out2.get("ok") is True, "bridge 回复预填要成功")
    pre2 = out2["data"]
    check(pre2["to"] == "reply-here@example.test", "回复收件人取 Reply-To")
    check(pre2["subject"] == "Re: 关于下周的实验课", "回复主题加 Re: ")
    check(pre2["attachments"] == [], "回复不带原附件")

    # --- 参数错误要规规矩矩报中文, 不许崩 --------------------------------
    bad = api.mail_prefill("1", "看不懂的模式")
    check(bad.get("ok") is False and "模式" in bad.get("error", ""),
          f"不认识的模式要报错, 实际 {bad}")
    bad2 = api.mail_send("a@example.test", "s", "b", "", "", "[不是 JSON")
    check(bad2.get("ok") is False and "附件参数" in bad2.get("error", ""),
          f"坏 JSON 要报错, 实际 {bad2}")

    # --- 附件参数的各种形态都要认(日志里真出现过"解析失败"的看不懂报错) ----
    for raw, want in ((None, []),
                      ("", []),
                      ("   ", []),
                      ('[{"name":"a","path":"x"}]', [{"name": "a", "path": "x"}]),
                      ('{"name":"a"}', [{"name": "a"}]),
                      ("[] []", []),                      # 两段空数组拼在一起
                      ('["a"] ["b"]', ["a", "b"])):       # 两段非空数组拼在一起
        got = B._parse_attachment_json(raw)
        check(got == want, f"附件参数 {raw!r} 应解析成 {want}, 实际 {got}")
    try:
        B._parse_attachment_json("不是 JSON")
        raise AssertionError("看不懂的附件参数必须报错, 不许静默当空数组")
    except PingheError as exc:
        check("附件参数" in str(exc), f"坏附件参数要报中文, 实际 {exc}")

    # --- 老的前端调用签名(只传 to/subject/body)依然能过 -------------------
    collector.calls.clear()
    smtplib.SMTP_SSL = collector
    try:
        old = api.mail_send("a@example.test", "s", "b")
    finally:
        smtplib.SMTP_SSL = real
    check(old.get("ok") is True and collector.calls, "老的三参数 mail_send 照旧可用")


def _scan_parens(js: str, open_idx: int) -> tuple[int, int, bool]:
    """从 `(` 的位置扫到配对的 `)`, 返回 (结束下标, 顶层逗号数, 是否完整).

    字符串/模板串/行注释里的括号与引号都跳过, 免得把模板串里的一句话当成实参。
    """
    depth, commas, j, quote = 0, 0, open_idx, ""
    while j < len(js):
        ch = js[j]
        if quote:
            if ch == "\\":
                j += 2
                continue
            if ch == quote:
                quote = ""
        elif ch in "\"'`":
            quote = ch
        elif ch == "/" and j + 1 < len(js) and js[j + 1] == "/":
            j = js.find("\n", j)
            if j < 0:
                return len(js) - 1, commas, False
            continue
        elif ch in "([{":
            depth += 1
        elif ch in ")]}":
            depth -= 1
            if depth == 0:
                return j, commas, True
        elif ch == "," and depth == 1:
            commas += 1
        j += 1
    return len(js) - 1, commas, False


def js_call_args(js: str) -> list[tuple[str, int]]:
    """扫出每个 `call("名字", 参数…)` 的实参个数。

    手写扫描(不用正则): 参数里有字符串、模板串、嵌套括号、换行。扫出来的名字
    再拿去 `getattr(Api, …)` 核对, 源码文案里那种假 `call("xxx")` 自然对不上。
    """
    out: list[tuple[str, int]] = []
    i = 0
    while True:
        hit = js.find('call("', i)
        if hit < 0:
            return out
        open_idx = hit + len("call")
        end, commas, _ok = _scan_parens(js, open_idx)
        inner = js[open_idx + 1:end]
        m = re.match(r'\s*"([a-z_0-9]+)"\s*(,?)([\s\S]*)$', inner)
        if m:
            # 实参个数 = 名字后面那些逗号数(`<名字>, a, b` → 名字后的逗号是 2 个,
            # 但实参只有 2 个: a、b)
            n_args = commas if m.group(3).strip() else 0
            out.append((m.group(1), n_args))
        i = end + 1


def test_ui_call_contract() -> None:
    """前端 call("名字", 参数…) 的元数必须和 bridge 的方法参数对得上。

    前端多传/少传参数在 WebView2 里表现是"点了没反应", 很难查 —— 这里静态
    对一遍 arity, 把这类错误挡在提交之前。`mail_list` 这类"已登录就叫, 没登录
    就跳过"的调用后面可能跟着别的实参形态, 静态看不全的通通用 in 名字白名单跳过。
    """
    import inspect

    from hellopinghe.app import bridge as B

    js = (ROOT / "ui" / "app.js").read_text(encoding="utf-8")
    checked = 0
    for name, n_given in js_call_args(js):
        fn = getattr(B.Api, name, None)
        if fn is None:
            continue        # 有些调用名是拼出来的, 静态看不全, 跳过
        params = list(inspect.signature(fn).parameters.values())[1:]
        required = [p for p in params
                    if p.default is inspect.Parameter.empty
                    and p.kind in (p.POSITIONAL_ONLY, p.POSITIONAL_OR_KEYWORD)]
        if n_given > len(params):
            raise AssertionError(
                f'call("{name}") 前端传了 {n_given} 个参数, '
                f"但 Api.{name} 只收 {len(params)} 个")
        if n_given < len(required):
            raise AssertionError(
                f'call("{name}") 前端只传 {n_given} 个参数, '
                f"但 Api.{name} 有 {len(required)} 个必填参数")
        checked += 1
    check(checked >= 30, f"至少要静态核对 30 个调用点, 实际只核对了 {checked}")

    # 新加的三个邮件方法: 名字 + 参数顺序要对得上前端
    mine = [n for n, _ in js_call_args(js) if n.startswith("mail_")]
    for name in ("mail_prefill", "mail_send", "mail_pick_attachments"):
        check(name in mine, f"前端要调用 {name}, 实际只调了 {sorted(set(mine))}")
    sig = inspect.signature(B.Api.mail_prefill)
    check(list(sig.parameters) == ["self", "uid", "mode"],
          f"Api.mail_prefill 参数应为 (self, uid, mode), 实际 {list(sig.parameters)}")
    check(list(inspect.signature(B.Api.mail_pick_attachments).parameters) == ["self"],
          "Api.mail_pick_attachments 只收 self")
    send_params = inspect.signature(B.Api.mail_send).parameters
    check(list(send_params)[:4] == ["self", "to", "subject", "body"],
          f"mail_send 的前三个参数必须还是 to/subject/body, 实际 {list(send_params)}")
    for p in ("cc", "bcc", "attachments_json"):
        check(p in send_params, f"mail_send 要有 {p} 参数")
        check(send_params[p].default is not inspect.Parameter.empty,
              f"mail_send 的 {p} 必须有默认值(老调用方不受影响)")


def dump_prefill() -> int:
    """把回复/转发的预填打成 JSON —— 给界面自检(scripts/test_mail_ui.mjs)当假后端用。

    这样界面断言的就是 Python 端**真的**会吐出来的形状, 而不是手抄的一份。
    """
    import json

    m = svc()
    msg = sample_original()
    print(json.dumps({"reply": m.reply_prefill(msg),
                      "forward": m.forward_prefill(msg)}, ensure_ascii=False))
    return 0


def main() -> int:  # noqa: C901
    if "--dump-prefill" in sys.argv:
        return dump_prefill()
    tests = [
        test_backward_compatible_signature,
        test_recipients_and_headers,
        test_body_is_utf8_plaintext,
        test_attachments_mime_and_order,
        test_chinese_attachment_filename,
        test_attachment_limits_and_bytes_source,
        test_recipient_parsing_errors,
        test_quote_text_structure,
        test_reply_prefill,
        test_forward_prefill,
        test_plain_body_fallbacks,
        test_smtp_envelope_gets_bcc_but_headers_do_not,
        test_bridge_end_to_end_with_fake_imap,
        test_ui_call_contract,
    ]
    failed = 0
    for fn in tests:
        try:
            fn()
            print(f"  [pass] {fn.__name__}")
        except Exception as exc:  # noqa: BLE001
            failed += 1
            print(f"  [FAIL] {fn.__name__}: {type(exc).__name__}: {exc}")
    print(f"mail compose self-check: {len(tests) - failed}/{len(tests)} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
