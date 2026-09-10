#!/usr/bin/env python3
"""一键 macOS 构建: 在局域网 Mac 上同步源码 → venv → PyInstaller(.app) → DMG → 拉回 Windows.

用法(在 Windows 开发机上):
    python scripts/macos_build.py --host 192.168.5.13 --user huazixian
        # 默认用 ~/.ssh/macos_build 免密登录(该机已授权); 无密钥时用 --pass / MAC_PASS
    python scripts/macos_build.py --host <Mac 的 IP> --user <用户名> --pass <密码>

凭据绝不含在仓库里(私钥在 ~/.ssh/macos_build, 不要提交)。
Mac 构建机常被手动关机, 先开机再跑; 它的 IP 由 DHCP 分配会变, 用前先 ping 或看 ARP 表。
依赖: 本机 pip install paramiko; Mac 需开机、联网、装好 Python 3.12。
输出: deliver/PingheLauncherLite-mac-<日期>-<架构>.dmg(此机为 Intel → x86_64; Apple Silicon → arm64)
"""
import argparse
import io
import os
import sys
import tarfile
import time
from pathlib import Path

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # 仓库根


def sh(ssh, cmd, timeout=1800, show=True):
    """在 Mac 上执行命令, 实时输出, 返回 (code, output)."""
    _, out, err = ssh.exec_command(cmd, timeout=timeout)
    chan = out.channel
    buf = []
    while True:
        while chan.recv_ready():
            data = chan.recv(4096).decode("utf-8", "replace")
            buf.append(data)
            if show:
                print(data, end="")
        if chan.exit_status_ready() and not chan.recv_ready():
            break
        time.sleep(0.1)
    code = chan.recv_exit_status()
    rest = err.read().decode("utf-8", "replace")
    if rest and show:
        print(rest, end="")
    return code, "".join(buf) + rest


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default=os.environ.get("MAC_HOST", "192.168.5.3"),
                    help="Mac 的地址(IP 会变, 用之前先确认; 例: 192.168.5.13)")
    ap.add_argument("--user", default=os.environ.get("MAC_USER", "huazixian"))
    ap.add_argument("--pass", dest="pwd", default=os.environ.get("MAC_PASS", ""),
                    help="SSH 密码(不填则优先用密钥, 仍无则交互输入)")
    ap.add_argument("--key", default=os.environ.get("MAC_KEY",
                    str(Path.home() / ".ssh" / "macos_build")),
                    help="SSH 私钥路径(默认 ~/.ssh/macos_build, 该机已授权可免密)")
    args = ap.parse_args()

    import paramiko

    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())

    # 优先密钥认证(免密), 失败再退回密码
    connected = False
    if args.key and Path(args.key).expanduser().exists():
        print(f"[1/8] 连接 {args.user}@{args.host} (密钥) …")
        try:
            ssh.connect(args.host, username=args.user, key_filename=str(Path(args.key).expanduser()),
                        timeout=8, look_for_keys=False, allow_agent=False)
            connected = True
        except Exception as exc:  # noqa: BLE001
            print(f"      密钥认证失败({type(exc).__name__}), 改用密码")
    if not connected:
        if not args.pwd:
            args.pwd = input(f"{args.user}@{args.host} SSH 密码: ")
        print(f"[1/8] 连接 {args.user}@{args.host} (密码) …")
        ssh.connect(args.host, username=args.user, password=args.pwd,
                    timeout=8, look_for_keys=False, allow_agent=False)
    print("OK")

    # 获取 Mac 的 HOME 和构建路径(必须在 Windows 侧解析, 传给 Mac 时用绝对路径)
    _, home_out, _ = ssh.exec_command("echo $HOME")
    mac_home = home_out.read().decode().strip()
    REMOTE_DIR = f"{mac_home}/hellopinghe-build"
    VENV = f"{mac_home}/hellopinghe-venv"
    _, arch_out, _ = ssh.exec_command("uname -m")
    arch = arch_out.read().decode().strip() or "x86_64"
    _, ver_out, _ = ssh.exec_command("sw_vers -productVersion")
    mac_ver = ver_out.read().decode().strip()
    print(f"  Mac home: {mac_home}  |  架构: {arch}  |  macOS {mac_ver}")

    print("[2/8] 打包源码 …")
    skip = ("/.git", "/build", "/dist", "/__pycache__", "/deliver",
            "/installer", "/tools", "logo.ico", "PingheLauncherLite.exe",
            "/.tokenicode", "/_probe_", "/_ui_", "/.claude")
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for name in sorted(os.listdir(HERE)):
            if any(s in f"/{name}" for s in skip) or name.startswith("_probe"):
                continue
            full = os.path.join(HERE, name)
            if os.path.isdir(full) and name == "scripts":
                continue  # 构建脚本自身不需要上传
            tar.add(full, arcname=name)
    data = buf.getvalue()
    print(f"  源码包 {len(data) // 1024} KB")

    # 创建远端目录并获取绝对路径(SFTP 不认识 ~)
    sh(ssh, f"mkdir -p {REMOTE_DIR}")
    _, out, _ = ssh.exec_command("echo $HOME/hellopinghe-build")
    remote_abs = out.read().decode().strip()
    print(f"  远端绝对路径: {remote_abs}")

    sftp = ssh.open_sftp()
    print("[3/8] 上传 …")
    sftp.putfo(io.BytesIO(data), f"{remote_abs}/src.tar.gz")
    sh(ssh, f"cd {remote_abs} && rm -rf src && mkdir src && "
            f"tar xzf src.tar.gz -C src && ls src | head -20", show=False)

    print("[4/8] venv + 依赖(需要 Mac 有网络, 首次数分钟) …")
    code, _ = sh(ssh, f"cd {remote_abs}/src && "
                      f"test -d {VENV} || /Library/Frameworks/Python.framework/Versions/3.12/bin/python3 -m venv {VENV}; "
                      f"{VENV}/bin/python -m pip install -U pip -q && "
                      f"{VENV}/bin/python -m pip install -e '.[ui,agent]' pyinstaller -q", show=True)
    if code != 0:
        sys.exit(f"依赖安装失败(检查 Mac 网络), exit {code}")

    print("[5/8] 生成 .icns 图标 …")
    sh(ssh, f"cd {remote_abs}/src && mkdir -p logo.iconset && "
            f"for s in 16 32 64 128 256 512; do "
            f"  sips -z $s $s ui/logo.png --out logo.iconset/icon_${{s}}x${{s}}.png >/dev/null; "
            f"  d=$((s*2)); sips -z $d $d ui/logo.png --out logo.iconset/icon_${{s}}x${{s}}@2x.png >/dev/null; "
            f"done; iconutil -c icns logo.iconset -o logo.icns && ls -la logo.icns")

    print("[6/8] PyInstaller(.app) …")
    code, _ = sh(ssh, f"cd {remote_abs}/src && "
                      f"{VENV}/bin/python -m PyInstaller --noconfirm --clean "
                      f"PingheLauncherLite-mac.spec")
    if code != 0:
        sys.exit(f"PyInstaller 失败, exit {code}")

    print("[7/8] 打 DMG …")
    stamp = time.strftime("%Y%m%d")
    # 文件名带架构(与 Releases 里的命名一致: ...-arm64.dmg / ...-x86_64.dmg)
    dmg = f"PingheLauncherLite-mac-{stamp}-{arch}.dmg"
    sh(ssh, f"cd {remote_abs}/src/dist && rm -f {dmg} && "
            f"hdiutil create -volname 'Pinghe Launcher Lite' -srcfolder "
            f"'Pinghe Launcher Lite.app' -ov -format UDZO {dmg} | tail -2")

    print("[8/8] 拉回 DMG …")
    deliver = os.path.join(HERE, "deliver")
    os.makedirs(deliver, exist_ok=True)
    local = os.path.join(deliver, dmg)
    sftp.get(f"{remote_abs}/src/dist/{dmg}", local)
    print(f"✅ 完成: {local} ({os.path.getsize(local) // 1048576} MB)")
    sftp.close()
    ssh.close()


if __name__ == "__main__":
    main()
