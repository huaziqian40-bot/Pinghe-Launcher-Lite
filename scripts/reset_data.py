# -*- coding: utf-8 -*-
"""清空测试数据: 备份配置 → 删 keyring 密钥 → 删配置/数据库/会话文件."""
import json
import shutil
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")

SB = Path.home() / ".hellopinghe"
cfg_path = SB / "config.json"

# 1) 备份配置(AI key 等都在里面)
if cfg_path.exists():
    backup = SB / f"config.backup-{__import__('datetime').datetime.now():%Y%m%d-%H%M%S}.json"
    shutil.copy2(cfg_path, backup)
    print(f"✓ 配置已备份: {backup}")
    old = json.loads(cfg_path.read_text(encoding="utf-8"))
else:
    old = {}
    print("(无配置文件)")

# 2) 删除 keyring 里本应用存的密钥
try:
    import keyring

    keys = []
    if old.get("edupage_username") and old.get("edupage_subdomain"):
        keys.append(f"edupage:{old['edupage_subdomain']}:{old['edupage_username']}")
    if old.get("managebac_base_url"):
        keys.append(f"managebac:{old['managebac_base_url']}")
    if old.get("mail_email"):
        keys.append(f"mail:{old['mail_email']}")
    for k in keys:
        try:
            keyring.delete_password("hellopinghe", k)
            print(f"✓ 已删除凭据: {k}")
        except Exception:  # noqa: BLE001
            print(f"- 无凭据可删: {k}")
except Exception as exc:  # noqa: BLE001
    print(f"keyring 清理异常: {exc}")

# 3) 删除配置 / 数据库 / 会话文件
for p in [cfg_path, SB / "hellopinghe.db", *SB.glob("session_*.json")]:
    if p.exists():
        p.unlink()
        print(f"✓ 已删除: {p}")

# 4) 验证: 重新加载应为全新状态
sys.path.insert(0, r"G:\agent\hellopinghe")
from hellopinghe.config import Config

cfg = Config.load()
print(f"\n验证 → wizard_done={cfg.wizard_done} (False = 下次启动会重新弹四步向导)")
print(f"edupage_username={cfg.edupage_username!r}, managebac={cfg.managebac_base_url!r}, "
      f"selected={len(cfg.selected_lessons)}")
