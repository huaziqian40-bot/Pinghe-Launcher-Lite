# -*- coding: utf-8 -*-
"""AI 服务商配置的**规范形态**与归一化(三端统一, 2026-09-13)。

背景
----
AI 服务商(名称/协议/Base URL/模型/API Key)要和平台凭据一样"在哪填都同步到
账号上, 三端都能用"。但三端各自的历史形态不一样::

    网页端(phix 官网, 规范来源)
        {"providers":[{"name","protocol","base_url","model","api_key"}], "default_index":0}

    PLL(本仓库, 本地 settings.yaml 的 ai 段)
        {"providers":[{"id","name","protocol","base_url","api_key","models":[...],"notes"}],
         "active_provider_id":..., "active_model":...}
        并且同步时**多包了一层**: 载荷是 {"ai": <ai 段>}

    PHL(Electron, 本地 settings.ai)
        扁平单服务商: {provider:'api'|'local'|'off', apiEndpoint, apiModel, apiKey,
                       localEndpoint, localModel, workspace, workspaces, ...}

**本模块只负责"同步对象 `settings.ai` 的载荷"**, 不改变任何一端的本地文档结构:

- :func:`normalize` / :func:`providers_of` —— **读**: 按 ①规范 → ②PLL 包装 →
  ③PHL 扁平 → ④更早的单服务商 的顺序探测, 一律归一成
  ``{"providers":[{name,protocol,base_url,model,api_key}], "default_index":N}``。
- :func:`merge_payload` —— 把本地与远端的规范化结果做三方合并(按服务商**名称**
  对齐, 同一名称下逐字段比新旧)。
- :func:`serialize` —— **写**: 输出规范形态, 并带 ``updated_at`` / ``updated_by``。

三条硬规矩
----------
1. 读的时候**兼容到底**, 写的时候**只有规范形态**(不然又是一堆形态)。
2. 合并只用规范形态比, 本地那份 ``models`` 列表 / ``id`` / ``notes`` **原样不动**。
3. 认不出来的东西一律**保留**(放 ``_extra`` 里带回去), 绝不静默丢字段。
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

#: 正规写入时 ``updated_by`` 的取值。
KNOWN_WRITERS = ("phl", "pll", "web")

_PROTOCOLS = ("openai", "anthropic")


# ---------------------------------------------------------------- 小工具
def _text(v) -> str:
    if v is None:
        return ""
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return str(v)
    if isinstance(v, str):
        return v.strip()
    return ""


def _first(seq) -> str:
    if isinstance(seq, (list, tuple)):
        for item in seq:
            text = _text(item)
            if text:
                return text
    return ""


def _protocol_of(raw) -> str:
    proto = _text(raw).lower()
    return proto if proto in _PROTOCOLS else "openai"


def _is_mapping(v) -> bool:
    return isinstance(v, dict)


def now_iso() -> str:
    """本地时区的时间戳(与 ``filestore.now_iso`` 同款, 这里独立实现免得循环 import)。"""
    return datetime.now(timezone.utc).astimezone().replace(microsecond=0).isoformat()


def _new_id() -> str:
    return "p-" + uuid.uuid4().hex[:6]


# ---------------------------------------------------------------- 归一化
def normalize_provider(raw: dict) -> dict:
    """任意形态的一条服务商 → 规范的一条(name/protocol/base_url/model/api_key)。

    ``model`` 取单值: 规范形态只有单值字段, PLL 的 ``models`` 列表**取第一个**;
    反过来(PLL 用规范形态回填本地)由 :func:`to_local_provider` 负责。
    """
    raw = raw if _is_mapping(raw) else {}
    model = _text(raw.get("model")) or _text(raw.get("model_name")) or _first(raw.get("models"))
    return {
        "name": _text(raw.get("name")) or _text(raw.get("label")) or _text(raw.get("title")),
        "protocol": _protocol_of(raw.get("protocol") or raw.get("api_type")),
        "base_url": _text(raw.get("base_url")) or _text(raw.get("baseUrl"))
                    or _text(raw.get("api_base")) or _text(raw.get("apiEndpoint"))
                    or _text(raw.get("endpoint")) or _text(raw.get("url")),
        "model": model,
        "api_key": _text(raw.get("api_key")) or _text(raw.get("apiKey"))
                   or _text(raw.get("key")) or _text(raw.get("token")),
    }


def _clean(doc) -> dict:
    """剥掉 PLL 的 ``{"ai": {...}}`` 包装(只剥一层, 兼容远端就是包装形态)。"""
    if _is_mapping(doc) and _is_mapping(doc.get("ai")):
        inner = doc.get("ai")
        # 只有"里面才是配置本体"时才剥: `{"ai": {...}, "providers": [...]}` 这种
        # 已经带 providers 的文档不剥, 否则会把真正的配置丢掉。
        if "providers" not in doc or "providers" not in inner:
            if inner.get("providers") or inner.get("provider") or inner.get("base_url") \
                    or inner.get("api_key") or inner.get("apiKey") or inner.get("apiModel"):
                return inner
    return doc if _is_mapping(doc) else {}


def _flat_candidates(doc: dict) -> list[dict]:
    """PHL 扁平形态 / 更早的单服务商形态 → 一条 provider(没有就是空列表)。"""
    api_key = _text(doc.get("api_key")) or _text(doc.get("apiKey"))
    base_url = (_text(doc.get("base_url")) or _text(doc.get("apiEndpoint"))
                or _text(doc.get("endpoint")))
    model = _text(doc.get("model")) or _text(doc.get("apiModel")) or _first(doc.get("models"))
    if not (base_url or api_key or model):
        return []
    name = _text(doc.get("name"))
    if not name:
        # PHL 扁平形态里 "来自客户端" 就是它能给的全部信息了(与网页端的做法一致)
        name = "来自客户端" if (doc.get("provider") or doc.get("apiModel") or doc.get("apiKey")) else ""
    return [normalize_provider({
        "name": name,
        "protocol": doc.get("protocol"),
        "base_url": base_url,
        "model": model,
        "api_key": api_key,
    })]


def providers_of(doc) -> list[dict]:
    """从任意历史形态里**尽量**取出服务商列表(顺序即①规范→②包装→③扁平→④单服务商)。"""
    cfg = _clean(doc)
    if not cfg:
        return []
    rows = cfg.get("providers")
    if isinstance(rows, list):
        return [normalize_provider(r) for r in rows if _is_mapping(r)]
    if _is_mapping(rows):
        # 有人用过 {"providers": {"deepseek": {...}}} 这种"以名字为键"的写法
        out = []
        for name, item in rows.items():
            if _is_mapping(item):
                entry = normalize_provider(item)
                entry["name"] = entry["name"] or _text(name)
                out.append(entry)
        return out
    return _flat_candidates(cfg)


def _index_of(value, count: int) -> int:
    try:
        idx = int(value)
    except (TypeError, ValueError):
        return 0
    if count <= 0:
        return 0
    return idx if 0 <= idx < count else 0


def normalize(doc) -> dict:
    """任意历史形态 → 规范形态(读的一侧, **不加** updated_at/updated_by)。

    返回::

        {"providers":[{name,protocol,base_url,model,api_key}], "default_index":N,
         "local_index":N|None, "_extra":{...认不出来的原字段...}}

    ``_extra`` / ``local_index`` 是给**本地**用的附带信息(写回本地文档时还会用到),
    序列化上云时由 :func:`serialize` 决定带哪些。
    """
    cfg = _clean(doc)
    providers = providers_of(doc)
    default_index = _index_of(cfg.get("default_index"), len(providers))
    local_index = cfg.get("local_index")
    local_index = _index_of(local_index, len(providers)) \
        if local_index is not None and str(local_index).strip() != "" else None
    extra = {k: v for k, v in cfg.items()
             if k not in ("providers", "default_index", "local_index",
                          "local_provider_id", "updated_at", "updated_by", "updatedBy")}
    return {
        "providers": providers,
        "default_index": default_index,
        "local_index": local_index,
        "updated_at": _text(cfg.get("updated_at")),
        "updated_by": _text(cfg.get("updated_by")) or _text(cfg.get("updatedBy")),
        "_extra": extra,
    }


def normalize_local(ai_doc) -> dict:
    """读**本地** ``settings.yaml`` 的 ai 段: 归一化, 并标记"这是本地文档读出来的"。

    两件本地专有的信息要额外带出来(规范形态里放不下):

    1. ``from_settings``: 序列化时它决定"沿用原样的 updated_at/updated_by",
       而不是盖一个"刚刚 PLL 改的"上去。
    2. ``_local_full``: 本地 ai 段的**原样副本**。PLL 的一条 provider 是一个列表
       (``models``) + 一个"当前用哪个模型"(``active_model``), 规范形态只有单值的
       ``model``。序列化时要按本机**真正在用的那个模型**来填 —— 否则用户手选的
       deepseek-reasoner 会被悄悄换成列表里的第一个, 三端读到的都是错的。
    """
    out = normalize(ai_doc)
    out["from_settings"] = True
    full = dict(ai_doc) if _is_mapping(ai_doc) else {}
    if not _text(full.get("updated_at")):
        # 本地文档里**没有**时间戳时, 记下"读到它的时候" —— 用来区分
        # "本地确实没记过时间"(当成很老) 与 "本地刚改过"(有条目时间可比)。
        # 注意存进 `_local_full` 的这份**只用于比较**, 不会写进本地文档。
        out["_local_full"] = {**full, "_read_at": now_iso()}
    else:
        out["_local_full"] = full
    return out


def _with_local_choices(doc: dict, local_full: dict) -> dict:
    """把"本机当前在用哪个模型"反映到规范形态的 ``model`` 上(只动默认那一条)。"""
    local_full = local_full if _is_mapping(local_full) else {}
    rows = doc.get("providers") or []
    if not rows:
        return doc
    locals_ = [p for p in (local_full.get("providers") or []) if _is_mapping(p)]
    if not locals_:
        return doc
    default_index = _index_of(doc.get("default_index"), len(rows))
    default_row = rows[default_index]
    names = [row.get("name") or "" for row in rows]
    local_name = ""
    local_index = local_full.get("local_index")
    if local_index is not None:
        try:
            local_index = int(local_index)
        except (TypeError, ValueError):
            local_index = None
    if local_index is None:
        # 本机的"当前服务商"= active_provider_id 指的那一条
        active_id = _text(local_full.get("active_provider_id"))
        active = next((p for p in locals_ if _text(p.get("id")) == active_id), None)
        local_name = _text((active or locals_[0]).get("name"))
    elif 0 <= local_index < len(locals_):
        local_name = _text(locals_[local_index].get("name"))
    if local_name and local_name != _text(default_row.get("name")):
        # 本机在用别的服务商, 默认那条的模型不是"我这台在用的" → 不要改它
        return doc
    active = next((p for p in locals_ if _text(p.get("name")) == local_name), None)
    active_model = _text(local_full.get("active_model"))
    if active is not None and active_model and active_model in [ _text(m) for m in (active.get("models") or []) ]:
        rows[default_index] = {**default_row, "model": active_model}
    return doc


def is_empty(doc) -> bool:
    return not providers_of(doc)


# ---------------------------------------------------------------- 合并
def _ts(payload) -> float:
    """尽力解析 ``updated_at``; 解析不出来给 0(等于"没有时间信息")。"""
    text = _text((payload or {}).get("updated_at")) if _is_mapping(payload) else ""
    return _ts_text(text)


def _ts_text(text: str) -> float:
    if not text:
        return 0.0
    try:
        return datetime.fromisoformat(str(text).replace("Z", "+00:00")).timestamp()
    except ValueError:
        return 0.0


def _real_local_ts(local) -> float:
    """本地那一侧的**真实**改动时间。

    ``collect()`` 会把序列化结果直接交给合并, 那份 ``updated_at`` 可能是"刚兜底盖的
    当前时间"(本地文档里从来没记过)。拿它当"本地刚改过"会把远端的更新永远压住
    (实测: 网页端刚改的协议拉不下来)。所以这里优先看 ``_local_full`` 里**真正存在**
    的时间戳, 没有就返回 0 = "没有时间信息", 由调用方保守处理。
    """
    if not _is_mapping(local):
        return 0.0
    full = local.get("_local_full") if _is_mapping(local.get("_local_full")) else {}
    declared = _ts_text(_text(full.get("updated_at")))
    if declared:
        return declared
    if local.get("from_settings"):
        if not _text(full.get("updated_at")) and _text(full.get("_read_at")):
            # 本地文档从没记过时间 → 只按"读到它的时刻"当上限, 不该压住比它更新
            # 的远端(网页端/PHL 刚写完的配置, 时间戳一定比我们读文档的时刻新)。
            return _ts_text(_text(full.get("_read_at")))
        return 0.0
    return _ts(local)


def _same_row(a: dict, b: dict) -> bool:
    keys = ("name", "protocol", "base_url", "model", "api_key")
    return all(_text(a.get(k)) == _text(b.get(k)) for k in keys)


def _pick_by_time(local: dict, remote: dict, local_at: float, remote_at: float):
    """两边都改了 → 取 updated_at 较新的一份; 分不出就留本地。"""
    if remote_at and local_at:
        return remote if remote_at > local_at else local
    if remote_at and not local_at:
        # 本地那份根本没记时间(老客户端/网页端写的) → 远端更可信
        return remote
    return local


def _conflict(path: str, note: str, local=None, remote=None, base=None) -> dict:
    return {"path": path, "local": local, "remote": remote, "base": base, "note": note}


def merge_payload(base, local, remote, *, path: str = "ai", conflicts: list | None = None) -> dict:
    """规范化后的三方合并(按**服务商名称**对齐)。

    没有基版(首次同步)时: 两边都有值的服务商 → 本地优先(与 PLL 既有
    ``_merge_scalar`` 的保守选择一致: 宁可晚一轮, 也不猜着覆盖)。
    """
    conflicts = conflicts if conflicts is not None else []
    # 本地那份是不是"从本地文档读出来的"(决定序列化时能不能改 updated_by)
    from_settings = bool(_is_mapping(local) and local.get("from_settings"))
    local_full = (local or {}).get("_local_full") if _is_mapping(local) else None
    b = normalize(base) if base is not None and not is_empty(base) else None
    l = normalize(local) if local is not None else normalize({})   # noqa: E741
    r = normalize(remote) if remote is not None and not is_empty(remote) else None

    def keep(doc: dict) -> dict:
        out = dict(doc)
        out.pop("updated_at", None)
        out.pop("updated_by", None)
        if from_settings:
            out["from_settings"] = True
            out["_local_full"] = local_full or {}
        return out

    if r is None:
        return keep(l)
    if not l["providers"]:
        out = dict(r)
        out.pop("from_settings", None)
        return out
    if not r["providers"]:
        return keep(l)

    bidx = {}
    if b:
        bidx = {_text(p.get("name")): p for p in b["providers"]}

    rows: list[dict] = []
    names = [p.get("name") or "" for p in l["providers"]]
    names += [p.get("name") or "" for p in r["providers"] if (p.get("name") or "") not in names]
    for name in names:
        lp = next((p for p in l["providers"] if (p.get("name") or "") == name), None)
        rp = next((p for p in r["providers"] if (p.get("name") or "") == name), None)
        bp = bidx.get(name)
        row_path = f"{path}.providers[{name or '?'}]"
        if lp is None:
            rows.append(rp)
            continue
        if rp is None:
            if bp is None or not _same_row(lp, bp):
                rows.append(lp)          # 本地新增的 / 本地改过的不许被远端删掉
                if bp is not None:
                    conflicts.append(_conflict(
                        row_path, "远端删了这个服务商、但本地改过 → 保留本地", local=lp))
            # 否则: 远端删了、本地没动 → 跟着删
            continue
        if _same_row(lp, rp):
            rows.append(lp)
            continue
        if bp is not None and _same_row(lp, bp):
            rows.append(rp)              # 只有远端改了
            continue
        if bp is not None and _same_row(rp, bp):
            rows.append(lp)              # 只有本地改了
            continue
        if bp is not None:
            pick = _pick_by_time(lp, rp, _real_local_ts(local), _ts(r))
        else:
            # **没有基版**(第一次同步这个对象 / 换了账号): 两边都有这条却不一样,
            # 分不清是谁后改的。但两件事可以定:
            #   - 本地那条的 updated_at 是"上一次真改配置"的时间(同步本身不盖时间戳),
            #     它比远端老 → 远端确实更晚改的, 收下远端那份更合理;
            #   - 否则按既有约定**留本地**(宁可晚一轮, 也不猜着覆盖)。
            left = _real_local_ts(local)
            right = _ts(r)
            pick = rp if (right and (not left or right > left)) else lp
        rows.append(pick)
        if pick is lp:
            conflicts.append(_conflict(
                row_path, "同一个服务商两边都改了 → 取较新的一份(本地)，远端那份见冲突记录",
                local=lp, remote=rp, base=bp))

    # 默认项: 跟着"被采纳的那一侧"的 default_index 走, 再按名称映射到新列表
    default_name = ""
    local_ts = _real_local_ts(local)
    remote_ts = _ts(r)
    remote_is_newer = bool(remote_ts and (not local_ts or remote_ts > local_ts))
    default_side = r if remote_is_newer and r.get("providers") else l
    side_rows = default_side["providers"]
    if side_rows:
        default_name = _text(side_rows[_index_of(default_side.get("default_index"),
                                                len(side_rows))].get("name"))
    default_index = 0
    for i, row in enumerate(rows):
        if default_name and _text(row.get("name")) == default_name:
            default_index = i
            break

    # 本地自用指针(哪个服务商是本机当前在用的)默认跟着 default_index
    local_index = l.get("local_index")
    if local_index is None:
        local_index = default_index
    else:
        local_name = ""
        if l["providers"]:
            local_name = _text(l["providers"][_index_of(local_index, len(l["providers"]))].get("name"))
        local_index = next((i for i, row in enumerate(rows)
                            if local_name and _text(row.get("name")) == local_name), default_index)

    # 本地这一侧**改没改**? 判据是"与上次同步后的基版(b)一致吗" —— 拿 rows 跟本地比
    # 是不行的: 纯拉远端时 rows 也会变(变成远端那份), 那就把"网页端改的"记成"PLL 改的"。
    if b is None:
        local_unchanged = False          # 没有基版 → 认不出"本地改没改", 按改过处理
    else:
        local_unchanged = (l["providers"] == b["providers"]
                           and l.get("default_index") == b.get("default_index"))
    out = {
        "providers": rows,
        "default_index": default_index,
        "local_index": local_index,
    }
    if local_unchanged:
        # 只有远端改了 → 元信息原样沿用**本地那份**(它本来就等于远端那份的元信息),
        # 绝不重新盖"现在": 否则别的设备每轮都以为配置又变了。
        out["updated_at"] = _text(l.get("updated_at")) or _text(r.get("updated_at"))
        out["updated_by"] = _text(l.get("updated_by")) or _text(r.get("updated_by"))
        if from_settings:
            out["from_settings"] = True
            out["_local_full"] = local_full or {}
    elif _text(r.get("updated_at")):
        # 本地确实改过(或首次同步) → 署名交给 serialize 按调用方(pll)重新盖,
        # 但**时间戳沿用远端那份**: 结果里本来就含着远端的改动, 盖一个"现在"会让
        # 别的设备以为配置又变了。
        out["updated_at"] = _text(r.get("updated_at"))
    extra = {}
    for src in (r, l):
        for k, v in (src.get("_extra") or {}).items():
            extra.setdefault(k, v)
    out["_extra"] = extra
    return out


# ---------------------------------------------------------------- 序列化 / 回填
def serialize(payload, writer: str = "pll") -> dict:
    """规范化结果 → **上云用的规范形态**(这一步唯一决定"写出去长什么样")。

    ``updated_at`` **只写一次**: 本地配置里已经有这个字段时原样沿用, 没有才打当前
    时间。每次调用都刷新会导致本地文档哈希每轮都变 → 每轮同步都在推一份没变的配置
    (实测就是"永远收敛不了")。真正改动配置的写入方(网页端、PHL、PLL 的设置界面)
    负责在改完之后自己盖新时间戳; 这里只兜底。

    ``updated_by`` 同理: 载荷里已经标明是谁写的就用它(那是"上一次真正改配置的人"),
    只有没标时才记成调用方 —— 否则 PLL 同步一轮就会把"网页端改的"说成自己改的。
    """
    payload = payload or {}
    from_settings = bool(payload.get("from_settings"))
    rows = [normalize_provider(p) for p in (payload.get("providers") or [])]
    default_index = _index_of(payload.get("default_index"), len(rows))
    doc = {"providers": rows, "default_index": default_index}
    if from_settings:
        # 本地读出来的: 按"本机当前在用的模型"修正默认那条的 model
        doc = _with_local_choices(doc, payload.get("_local_full") or {})
        rows = doc["providers"]
    local_index = payload.get("local_index")
    if local_index is not None and _index_of(local_index, len(rows)) != default_index:
        # 本机当前用的不是默认那家(例如本地走 Ollama): 记下来, 别的设备不被这点差异影响
        doc["local_index"] = _index_of(local_index, len(rows))
    doc["updated_at"] = _text(payload.get("updated_at")) or now_iso()
    writer = writer if writer in KNOWN_WRITERS else "pll"
    if from_settings:
        # 本地文档读出来的: 原样带回去(哪怕没有), 绝不改成"我写的"
        declared = _text(payload.get("updated_by"))
        if declared:
            doc["updated_by"] = declared
    else:
        doc["updated_by"] = _text(payload.get("updated_by")) or writer
    return doc


def to_local_provider(row: dict, *, index: int = 0, previous: dict | None = None) -> dict:
    """规范的一条 → PLL **本地** ai.providers[] 的一条。

    本地结构里有而规范形态没有的东西(``id`` / ``models`` 列表 / ``notes``)
    **必须留着**: ``previous`` 是本机原来那条时, 沿用它的 ``id``/``notes``,
    ``models`` 只在"新模型名不在列表里"时前插, 绝不整体替换(用户手改过的模型列表
    不该因为一次同步消失)。
    """
    row = normalize_provider(row)
    previous = previous if _is_mapping(previous) else {}
    models = [m for m in (previous.get("models") or []) if _text(m)]
    model = _text(row.get("model"))
    if model and model not in models:
        models = [model] + models
    if not models and model:
        models = [model]
    # 之前那条的**能力位**也得留住: 有些老配置只认 models 列表里的名字
    out = {
        "name": row["name"] or _text(previous.get("name")) or f"服务商{index + 1}",
        "protocol": row["protocol"] or _text(previous.get("protocol")) or "openai",
        "base_url": row["base_url"] or _text(previous.get("base_url")),
        "api_key": row["api_key"] or _text(previous.get("api_key")),
        "models": models,
    }
    # id 是 PLL 本地专有的稳定标识(前端按 id 选当前服务商), 老配置没有就补一个。
    # 补在**前面**: 新服务商从云端拉下来时也要有 id, 否则前端选不中它。
    pid = _text(previous.get("id")) or _text(row.get("id")) or _new_id()
    out = {"id": pid, **out}
    for key, value in previous.items():
        if key not in out and key not in ("model", "models"):
            # 本地特有的字段(notes / 以后新增的)一律保留
            out[key] = value
    return out


def apply_to_local_doc(doc: dict, payload: dict, *, index: int | None = None,
                       writer: str = "pll") -> dict:
    """把规范化结果写回**本地** ``settings.yaml`` 的 ai 段。

    **只动** ``providers`` / ``active_provider_id`` / ``active_model``, 其余字段
    (以及每个 provider 自己的 ``id``/``models``/``notes``)原样保留 —— 同步载荷与
    本地文档结构**解耦**, 本地文档永远是唯一真相源。
    """
    doc = dict(doc or {})
    ai = doc.get("ai")
    ai = dict(ai) if _is_mapping(ai) else {}
    payload = payload or {}
    rows = [normalize_provider(p) for p in (payload.get("providers") or [])]
    locals_old = [p for p in (ai.get("providers") or []) if _is_mapping(p)]
    by_name = {_text(p.get("name")): p for p in locals_old}
    # by_name 只认"同名"的那条: 同步是**按名称对齐**的(见 merge_payload)

    active_before = _text(ai.get("active_provider_id"))
    active_name = ""
    if active_before:
        for p in locals_old:
            if _text(p.get("id")) == active_before:
                active_name = _text(p.get("name"))
                break

    new_rows = []
    for i, row in enumerate(rows):
        prev = by_name.get(_text(row.get("name")))
        new_rows.append(to_local_provider(row, index=i, previous=prev))
    ai["providers"] = new_rows

    # 哪条是"本机当前在用"? ① 调用方指定 ② 原来的 active 服务商(按名字对上)
    # ③ 载荷的 default_index。都对不上(被远端删了/改名了)才退回默认那条。
    active_id = ""
    if index is not None and 0 <= index < len(new_rows):
        active_id = _text(new_rows[index].get("id"))
    if not active_id and active_name:
        for p in new_rows:
            if _text(p.get("name")) == active_name:
                active_id = _text(p.get("id"))
                break
    if not active_id and new_rows:
        idx = _index_of(payload.get("default_index"), len(new_rows))
        active_id = _text(new_rows[idx].get("id"))
    if active_id:
        ai["active_provider_id"] = active_id
        chosen = next((p for p in new_rows if _text(p.get("id")) == active_id), None)
        if chosen:
            names = [m for m in (chosen.get("models") or []) if _text(m)]
            ai["active_model"] = names[0] if names else ""
    else:
        ai["active_provider_id"] = ""
        ai["active_model"] = ""
    doc["ai"] = ai
    # 上云时间是"别人的", 不写回本地文档(本地文档不承担同步元信息)
    return doc
