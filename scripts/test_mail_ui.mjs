/* 邮件界面的 DOM 冒烟自检(回复 / 转发 / 选附件 / 发送)
 *
 *     node scripts/test_mail_ui.mjs
 *
 * 用 jsdom 真的把 ui/index.html + ui/app.js 跑起来, 只把 window.pywebview.api
 * 换成假的(记下每次调用), 然后像用户一样点:
 *   邮件列表点开一封 → 阅读窗格顶部出现 回复/转发 → 点转发 → 撰写窗口
 *   预填好收件人/主题/引用块/原附件 → 加/删附件 → 点发送 → 核对发给 Python 的参数
 *
 * 依赖: jsdom(不在仓库里, 不引任何 CDN)。先装到任意目录再告诉本脚本:
 *     npm install jsdom
 *     set JSDOM_ROOT=C:\path\to\node_modules       (或 npm root -g)
 *
 * 预填数据(prefill.json)由 scripts/test_mail_compose.py 生成, 与 Python 端
 * 用的是同一份, 保证界面断言的是真形状。生成后用环境变量传进来:
 *     set MAIL_PREFILL_JSON=<json 文本>
 */
import fs from "node:fs";
import path from "node:path";
import { createRequire } from "node:module";
import { fileURLToPath } from "node:url";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const ROOT = path.resolve(HERE, "..");

/* ---------------------------------------------------------------- jsdom */
function loadJsdom() {
  const candidates = [];
  if (process.env.JSDOM_ROOT) candidates.push(process.env.JSDOM_ROOT);
  candidates.push(path.join(ROOT, "node_modules"));
  candidates.push(path.join(process.env.TEMP || process.env.TMP || "/tmp",
    "dsh-jsdom-probe", "node_modules"));
  for (const base of candidates) {
    try {
      const req = createRequire(path.join(base, "_", "noop.js"));
      return req("jsdom");
    } catch { /* 下一个 */ }
  }
  return null;
}

const jsdomMod = loadJsdom();
if (!jsdomMod) {
  console.log("跳过界面自检: 没找到 jsdom。");
  console.log("  装法: npm install jsdom  然后 set JSDOM_ROOT=<node_modules 目录>");
  process.exit(0);
}
const { JSDOM } = jsdomMod;

/* ---------------------------------------------------------------- 断言 */
let passed = 0;
const failures = [];
function check(cond, msg) {
  if (cond) { passed++; return; }
  failures.push(msg);
  console.log(`  [FAIL] ${msg}`);
}
function checkEq(got, want, msg) {
  check(got === want, `${msg} (期望 ${JSON.stringify(want)}, 实际 ${JSON.stringify(got)})`);
}
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

/* ------------------------------------------------------------ 假后端 */
const calls = [];
let prefillSrc = { reply: {}, forward: {} };
if (process.env.MAIL_PREFILL_JSON) {
  prefillSrc = JSON.parse(process.env.MAIL_PREFILL_JSON);
}

const SENT_FOLDER = {
  name: "实验报告.pdf", size: 21,
  data_base64: Buffer.from("%PDF-1.4 original\n").toString("base64"),
};

function ok(data) { return { ok: true, data }; }

function makeApi() {
  return {
    mail_list: async (unseen) => {
      calls.push(["mail_list", unseen]);
      return ok({
        shared: false,
        mails: [{ uid: "1", subject: "关于下周的实验课", from: "王老师", date: "09-08 09:30", seen: false }],
      });
    },
    mail_read: async (uid) => {
      calls.push(["mail_read", uid]);
      return ok({
        uid, subject: "关于下周的实验课", from: "王老师 <teacher@example.test>",
        date: "Mon, 08 Sep 2026 09:30:00 +0800", to: "me@example.test", cc: "",
        body: "第一行原文\n第二行原文", is_html: false,
        attachments: [{ index: 0, filename: "实验报告.pdf", size: 21 }],
      });
    },
    mail_download_attachment: async () => ok({ path: "C:/x/实验报告.pdf" }),
    mail_unread: async () => ok({ count: 1 }),
    mail_contacts: async () => ok({ contacts: [] }),
    mail_prefill: async (uid, mode) => {
      calls.push(["mail_prefill", uid, mode]);
      const p = prefillSrc[mode];
      if (!p) return { ok: false, error: `没有 ${mode} 预填` };
      return ok({ ...p, uid });
    },
    mail_send: async (...args) => {
      calls.push(["mail_send", ...args]);
      return ok({ sent: true, envelope: ["new@example.test"], skipped: [] });
    },
    mail_pick_attachments: async () => ok({ cancelled: true, files: [] }),
    home_data: async () => ok({}),
    phix_status: async () => ok({ logged_in: false }),
  };
}

/* ------------------------------------------------------------ 启动界面 */
const html = fs.readFileSync(path.join(ROOT, "ui", "index.html"), "utf8");
const dom = new JSDOM(html, {
  url: "http://localhost/",
  runScripts: "outside-only",
  /* 不发任何外部请求(字体/图标之类一律不拉), 自检必须离线可跑 */
  resources: undefined,
  pretendToBeVisual: false,
});
const win = dom.window;
win.pywebview = { api: makeApi() };
/* app.js 的顶层是 const/函数声明, 直接 win.eval 会和 jsdom 自己的全局(它也有
   `$`)撞名(TDZ/重复声明直接抛错)。套一层 IIFE 再把它那几处函数交出来即可 ——
   这就跟 pywebview 里 <script src> 跑的环境等价。 */
const appSrc = fs.readFileSync(path.join(ROOT, "ui", "app.js"), "utf8");
const exposed = ["loadMail", "fetchMail", "openCompose", "startMailFrom",
  "mlAddFiles", "mlAttLabel", "renderMail", "renderContacts", "esc"];
const wrapped = `(function(){\n${appSrc}\n;
window.__pll = { ${exposed.join(", ")} };
/* 自检要自己拉邮件列表, 得先把"当前视图"设成邮箱(否则 swr 的 isCurrent 会拦下重绘) */
window.__pll.gotoMailView = function () { currentView = "mail"; };
})();`;
try {
  win.eval(wrapped);
} catch (e) {
  console.log(`  [FAIL] ui/app.js 在 jsdom 里跑不起来: ${e && e.message}`);
  console.log("         (先确认 node --check ui/app.js 通过, 再看错误信息)");
  process.exit(1);
}
const doc = win.document;
const $ = (sel) => doc.querySelector(sel);
/* 交出来的那几个函数只能这样拿(它们不在 window 上) */
const P = win.__pll;

async function main() {
  /* 让启动流程跑完(启动页会预载各页数据, 用假后端不会联网) */
  await sleep(60);

  /* ---- 1. 点开一封邮件, 阅读窗格顶部要有 回复 / 转发 ---- */
  /* 主页加载完成后直接拉邮件列表(自检不依赖"用户点了侧栏导航") */
  P.gotoMailView();
  await P.loadMail();
  await sleep(40);
  const item = $("#ml-list .mail-item");
  check(!!item, "邮件列表要渲染出至少一封邮件");
  if (!item) return finish();
  item.dispatchEvent(new win.MouseEvent("click", { bubbles: true }));
  await sleep(60);

  const replyBtn = $("#ml-read .mail-actions [data-act='reply']");
  const fwdBtn = $("#ml-read .mail-actions [data-act='forward']");
  check(!!replyBtn, "阅读窗格顶部要有「回复」按钮");
  check(!!fwdBtn, "阅读窗格顶部要有「转发」按钮");
  check(!!$("#ml-read h3"), "阅读窗格要显示主题");
  check(($("#ml-read .mail-body") || {}).textContent?.includes("第一行原文"),
    "阅读窗格要显示正文");
  check(!!$("#ml-read .att-dl"), "原有附件下载按钮不能被改坏");
  check(calls.some((c) => c[0] === "mail_read"), "点开邮件要调 mail_read");

  /* ---- 2. 回复: 收件人/主题/引用块, 光标在引用块上方 ---- */
  replyBtn.dispatchEvent(new win.MouseEvent("click", { bubbles: true }));
  await sleep(80);
  checkEq($("#ml-modal").className.includes("hidden"), false, "点回复要打开撰写窗口");
  checkEq($("#ml-title").textContent, "↩ 回复邮件", "撰写窗口标题要显示「回复」");
  checkEq($("#ml-to").value, prefillSrc.reply.to, "回复的收件人要预填");
  checkEq($("#ml-subject").value, prefillSrc.reply.subject, "回复的主题要预填 Re: ");
  const rbody = $("#ml-body").value;
  check(rbody.startsWith("\n\n---------- 原始邮件 ----------"),
    `回复正文要以引用块开头, 实际 ${JSON.stringify(rbody.slice(0, 40))}`);
  check(rbody.includes("> 第一行原文"), "回复正文要逐行加 >");
  checkEq($("#ml-body").selectionStart, 0, "光标要停在引用块上方(位置 0)");
  checkEq($("#ml-att-info").textContent, "", "回复不该自动带附件");

  /* ---- 3. 转发: 收件人留空 + 原附件带上 ---- */
  $("#ml-cancel").dispatchEvent(new win.MouseEvent("click", { bubbles: true }));
  await sleep(20);
  fwdBtn.dispatchEvent(new win.MouseEvent("click", { bubbles: true }));
  await sleep(80);
  checkEq($("#ml-title").textContent, "➡ 转发邮件", "撰写窗口标题要显示「转发」");
  checkEq($("#ml-to").value, "", "转发不预填收件人");
  checkEq($("#ml-subject").value, prefillSrc.forward.subject, "转发的主题要预填 Fwd: ");
  check($("#ml-body").value.includes("> 第一行原文"), "转发正文要带引用块");
  const attRows = doc.querySelectorAll("#ml-att-list .att-one");
  check(attRows.length === prefillSrc.forward.attachments.length,
    `转发的原附件要列进附件列表(期望 ${prefillSrc.forward.attachments.length} 行, 实际 ${attRows.length})`);
  check($("#ml-att-info").textContent.includes("1 个附件"),
    `附件行要显示数量与大小, 实际 ${JSON.stringify($("#ml-att-info").textContent)}`);
  check(!!$("#ml-attach"), "撰写窗口要有「添加附件」按钮");
  const fileInput = $("#ml-file");
  check(!!fileInput && fileInput.type === "file", "要有 <input type=file>");
  check(fileInput.multiple, "文件选择框要能多选");

  /* ---- 4. 加一个附件(file.path 走路径上传) ---- */
  const fakeFile = { name: "笔记.txt", size: 2048, path: "C:\\tmp\\笔记.txt" };
  Object.defineProperty(fileInput, "files", { value: [fakeFile], configurable: true });
  fileInput.dispatchEvent(new win.Event("change", { bubbles: true }));
  await sleep(60);
  const rows2 = doc.querySelectorAll("#ml-att-list .att-one");
  checkEq(rows2.length, 2, "选中的文件要加进附件列表");
  check($("#ml-att-info").textContent.includes("2 个附件"),
    "附件信息要更新成 2 个");

  /* ---- 5. 移除一个附件: 点第二行(刚选的那个)的 ✕, 只该删掉它自己 ---- */
  const xBtn = doc.querySelectorAll("#ml-att-list .att-one .att-x")[1];
  const removedName = doc.querySelectorAll("#ml-att-list .att-one .grow")[1].textContent;
  xBtn.dispatchEvent(new win.MouseEvent("click", { bubbles: true }));
  await sleep(20);
  checkEq(doc.querySelectorAll("#ml-att-list .att-one").length, 1, "点 ✕ 要能移除附件");
  const leftName = doc.querySelector("#ml-att-list .att-one .grow").textContent;
  check(!leftName.includes("笔记"),
    `✕ 只能删它自己那一行(点的是 ${removedName}, 删完还剩 ${leftName})`);
  check(leftName.includes("实验报告"),
    `删掉后应保留转发的原附件, 实际剩 ${leftName}`);

  /* ---- 6. 发送: 核对交给 Python 的参数 ---- */
  $("#ml-to").value = "new@example.test";
  $("#ml-cc").value = "cc@example.test";
  $("#ml-bcc").value = "blind@example.test";
  $("#ml-msg").textContent = "";
  calls.length = 0;
  $("#ml-send").dispatchEvent(new win.MouseEvent("click", { bubbles: true }));
  await sleep(80);
  const send = calls.find((c) => c[0] === "mail_send");
  check(!!send, "点发送要调 mail_send");
  if (send) {
    checkEq(send.length, 7, "mail_send 要传 7 个参数(方法名 + 6 个)");
    checkEq(send[1], "new@example.test", "第 1 个参数是收件人");
    checkEq(send[2], prefillSrc.forward.subject, "第 2 个参数是主题");
    check(send[3].includes("> 第一行原文"), "第 3 个参数是正文(带引用块)");
    checkEq(send[4], "cc@example.test", "第 4 个参数是抄送");
    checkEq(send[5], "blind@example.test", "第 5 个参数是密送");
    let specs = null;
    try { specs = JSON.parse(send[6]); } catch { /* 下面报错 */ }
    check(Array.isArray(specs), `第 6 个参数要是附件的 JSON 数组, 实际 ${JSON.stringify(send[6]).slice(0, 80)}`);
    if (Array.isArray(specs)) {
      checkEq(specs.length, 1, "剩下的那个附件要被带上");
      if (specs.length === 1) {
        check(!!specs[0].data_base64 && !specs[0].path,
          `转发留下的原附件要以 base64 形式交给 Python(不是路径), 实际 ${JSON.stringify(Object.keys(specs[0]))}`);
        checkEq(Buffer.from(specs[0].data_base64 || "", "base64").toString(), "%PDF-1.4 original\n",
          "附件字节要原样传给 Python");
        checkEq(specs[0].name, "实验报告.pdf", "带上的是删剩的那个附件");
      }
    }
  }
  checkEq($("#ml-modal").className.includes("hidden"), true, "发送成功后撰写窗口要关上");
  checkEq($("#ml-to").value, "", "发送成功后收件人框要清空");

  /* ---- 7. 附件超限要给可读提示, 不许静默丢弃 ---- */
  $("#ml-compose").dispatchEvent(new win.MouseEvent("click", { bubbles: true }));
  await sleep(20);
  const tooBig = { name: "超大.iso", size: 30 * 1024 * 1024, path: "C:\\tmp\\超大.iso" };
  const inp2 = $("#ml-file");
  Object.defineProperty(inp2, "files", { value: [tooBig], configurable: true });
  inp2.dispatchEvent(new win.Event("change", { bubbles: true }));
  await sleep(60);
  checkEq(doc.querySelectorAll("#ml-att-list .att-one").length, 0,
    "超过单个上限的文件不许加进附件列表");
  check(($("#toast") || { textContent: "" }).textContent.includes("超过单个附件上限")
    || doc.body.textContent.includes("超过单个附件上限"),
    "超限要给出可读的中文提示");

  return finish();
}

function finish() {
  console.log(`mail UI self-check: ${passed} 项通过, ${failures.length} 项失败`);
  if (failures.length) {
    for (const f of failures) console.log(`  - ${f}`);
    process.exitCode = 1;
  }
  /* 界面里还挂着 setInterval/定时器(未读轮询、toast), jsdom 不会自己退 —— 收尾 */
  try { dom.window.close(); } catch { /* 忽略 */ }
  process.exit(process.exitCode || 0);
}

/* 卡死兜底: 自检超过 30 秒直接失败退出, 别把 CI/终端挂住 */
const watchdog = setTimeout(() => {
  console.log("  [FAIL] 界面自检超时(30 秒), 可能是启动流程卡住了");
  process.exitCode = 1;
  try { dom.window.close(); } catch { /* 忽略 */ }
  process.exit(1);
}, 30000);
watchdog.unref?.();

main().catch((e) => {
  console.log(`  [FAIL] 自检本身出错了: ${e && e.stack ? e.stack : e}`);
  process.exitCode = 1;
  try { dom.window.close(); } catch { /* 忽略 */ }
  process.exit(1);
});
