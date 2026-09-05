# 第三方组件与致谢(Third-Party Notices)

本项目(Hello Pinghe! Launcher)以 **GPL-3.0-or-later** 发布。
本文件列出项目引用/使用的开源组件及其许可证, 感谢这些项目的作者。

## 直接依赖(运行时)

| 组件 | 许可证 | 用途 | 主页 |
|---|---|---|---|
| [edupage-api](https://pypi.org/project/edupage-api/) | **GPL-3.0-or-later** | Edupage 登录/课表/数据抓取 | github.com/nikolajjsj/edupage-api |
| [pywebview](https://pypi.org/project/pywebview/) | BSD-3-Clause | 桌面 WebView 窗口(Windows: WebView2 / macOS: WKWebView) | github.com/r0x0r/pywebview |
| [keyring](https://pypi.org/project/keyring/) | MIT | 凭据安全存储(Windows 凭据管理器 / macOS 钥匙串) | github.com/jaraco/keyring |
| [python-docx](https://pypi.org/project/python-docx/) | MIT | 作业 Word 文档读写 | github.com/python-openxml/python-docx |
| [requests](https://pypi.org/project/requests/) | Apache-2.0 | HTTP 请求 | github.com/psf/requests |
| [beautifulsoup4](https://pypi.org/project/beautifulsoup4/) | MIT | HTML 解析 | www.crummy.com/software/BeautifulSoup/ |
| [PyInstaller](https://pyinstaller.org/) | GPL-2.0-or-later(带 bootloader 特例, 允许分发其打包的成品) | 打包为独立可执行程序 | pyinstaller.org |

## 设计与代码参考

| 项目 | 许可证 | 引用内容 |
|---|---|---|
| **[PH-Launcher](https://github.com/XKRyan/PH-Launcher)** (XKRyan) | **MIT** | 本项目的设计基调(墨绿/金/象牙白配色与视觉语言)延续自 PH-Launcher;本项目是其设计理念的 Python 本地化重实现 |
| [ManageBac-GPA-Scraper](https://github.com/Ayushpanditmoto/ManageBac-GPA-Scraper) | MIT | ManageBac 课程列表/总评页的 HTML 选择器来源(managebac/parse.py 注释已标注) |

## 仅作技术调研(未复制代码, 特此说明)

- [managebac-mcp](https://github.com/marcolan03/managebac-mcp) — DDL 文本行解析思路参考(仓库无 LICENSE 文件, 未复制代码)
- [pymanagebac](https://github.com/timothycdc/pymanagebac) — 验证了账密登录可行性(GPL-3.0, 未复制代码)
- [ManageBacAPI / AutoMB](https://github.com/AutoMB) — 学生端端点与选择器核对(无许可证, 未复制代码)

## 说明

- 由于依赖 edupage-api(GPL-3.0-or-later), 本项目整体以 **GPL-3.0-or-later** 发布;
  PH-Launcher(MIT)与 ManageBac-GPA-Scraper(MIT)的引用在 GPL 项目中兼容, 原始版权声明保留于本文件。
- 各组件的完整许可证文本以其官方仓库/发行包为准。
