# vendor —— 随程序一起分发的第三方库

这些文件**不是**本仓库的代码，是从上游直接下载的原样副本（vendored），
目的是让程序**离线也能用**（不依赖 CDN，校园网/断网时照常工作）。

| 文件 | 版本 | 上游 | 许可 |
|---|---|---|---|
| `marked.min.js` | **12.0.2** | <https://github.com/markedjs/marked> · `https://cdn.jsdelivr.net/npm/marked@12.0.2/marked.min.js` | MIT |
| `purify.min.js` | **3.1.6** | <https://github.com/cure53/DOMPurify> · `https://cdn.jsdelivr.net/npm/dompurify@3.1.6/dist/purify.min.js` | MIT（或 Apache-2.0，二选一） |

## 各自做什么

- **marked** —— 把 AI 回复的 Markdown 渲染成 HTML。自己写正则那套只能应付粗体与标题，
  表格、围栏代码块、有序列表、引用、链接都出不来（2026-09-22 用户要求按正经渲染器来）。
- **DOMPurify** —— 在 `innerHTML` 之前把结果净化掉。**这一步不能省**：
  模型回复是外部输入，而本程序的 `js_api` 桥能发邮件、交作业、写文件；
  若回复里被塞进 `<script>` 或 `<img onerror=...>`，就等于把整个桥交给它。

## 怎么升级

```powershell
curl.exe -sL -o ui\vendor\marked.min.js   https://cdn.jsdelivr.net/npm/marked@<版本>/marked.min.js
curl.exe -sL -o ui\vendor\purify.min.js   https://cdn.jsdelivr.net/npm/dompurify@<版本>/dist/purify.min.js
```

升完跑一次 `python -X utf8 _ui_test.py`（里面有用例盯着"渲染出来了 + 危险标签被清掉"），
并把上表的版本号改掉。
