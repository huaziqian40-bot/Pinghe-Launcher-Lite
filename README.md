# SchoolHub(占位名,可随时改)

本地运行的 ManageBac + Edupage 学习助手。无服务器、无云端:数据抓取、缓存、提醒、AI 全在本机。

## 功能规划

- **M1 核心只读(当前)**:ManageBac 纯 HTTP 登录 + classes/DDL/成绩抓取;Edupage 登录 + 课表;SQLite 缓存
- **M2 壳与提醒**:pywebview 界面 + 本地通知(APScheduler + toast)
- **M3 消息**:Edupage 收发(edupage-api)+ ManageBac 收件箱(端点待真实会话探索)
- **M4 Word + Agent**:作业工作区(.docx)+ harness 式最小 agent 循环 + ManageBac dropbox 提交

## 快速开始

```bash
pip install -e .
```

```bash
# 探测学校登录页(无需账号):验证表单与 CSRF
schoolhub probe --url https://shph.managebac.cn

# 登录并保存会话(密码不落盘,只存 cookie)
schoolhub login --url https://shph.managebac.cn

# 抓取课程 / DDL / 成总评
schoolhub classes
schoolhub ddl --days 14
schoolhub grades

# Edupage 课表
schoolhub timetable --subdomain 你的学校子域名 --days 7
```

## 配置

`~/.schoolhub/config.json`(首次运行自动生成),Agent 部分支持预设:
deepseek / kimi / glm / qwen / ollama(本地) / custom,协议 openai|anthropic 二选一,用户自填 API key 与 base_url。

## 安全边界

- 密码只在登录瞬间使用,不落盘;登录态只存 cookie
- 所有数据只进本机 SQLite
- Agent 提交作业前强制人工确认
