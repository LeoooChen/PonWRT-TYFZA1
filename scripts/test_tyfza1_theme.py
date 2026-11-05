#!/usr/bin/env python3
"""Exercise the one-time theme migration and feed preparation without a device."""
from pathlib import Path
import json
import os
import shutil
import subprocess
import tempfile

root = Path(__file__).resolve().parents[1]
package = root / "port/packages/luci-app-tyfza1-pon"
defaults = package / "root/etc/uci-defaults/95-tyfza1-argon"
assert defaults.stat().st_mode & 0o111
assert json.loads((root / "port/sources.lock.json").read_text())["luci_theme_argon"]["commit"] == "539e3e1db56b2348c3655615540e45638b797c73"
assert "src-git argon " not in (root / "configs/feeds.conf").read_text()
assert "+luci-i18n-argon-config-zh-cn" in (package / "Makefile").read_text()
assert "CONFIG_LUCI_LANG_zh_Hans=y" in (root / "port/configs/tyfza1-boot.config").read_text()

with tempfile.TemporaryDirectory() as directory:
    work = Path(directory)
    state = work / "uci.json"
    theme = work / "cascade.css"
    theme.touch()
    mock = work / "uci"
    mock.write_text("""#!/usr/bin/env python3
import json, os, sys
from pathlib import Path
p = Path(os.environ['UCI_FIXTURE'])
d = json.loads(p.read_text())
args = [a for a in sys.argv[1:] if a != '-q']
if args[0] == 'get':
    if args[1] not in d: sys.exit(1)
    print(d[args[1]])
elif args[0] == 'batch':
    for line in sys.stdin:
        if line.startswith('set '):
            k, v = line[4:].strip().split('=', 1)
            d[k] = v.strip("'")
    p.write_text(json.dumps(d))
else: sys.exit(2)
""")
    mock.chmod(0o755)
    script = work / "migrate.sh"
    script.write_text(defaults.read_text().replace("/www/luci-static/argon/css/cascade.css", str(theme)))
    env = {**os.environ, "PATH": str(work) + os.pathsep + os.environ["PATH"], "UCI_FIXTURE": str(state)}
    state.write_text(json.dumps({"luci.main.mediaurlbase": "/luci-static/bootstrap"}))
    subprocess.run(["sh", str(script)], env=env, check=True)
    applied = json.loads(state.read_text())
    assert applied["luci.main.mediaurlbase"] == "/luci-static/argon"
    assert applied["luci.main.tyfza1_argon_migrated"] == "1"
    applied["luci.main.mediaurlbase"] = "/luci-static/bootstrap"
    state.write_text(json.dumps(applied))
    subprocess.run(["sh", str(script)], env=env, check=True)
    assert json.loads(state.read_text()) == applied
    theme.unlink()
    state.write_text("{}")
    assert subprocess.run(["sh", str(script)], env=env).returncode == 1
    assert json.loads(state.read_text()) == {}

    project = work / "project"
    (project / "scripts").mkdir(parents=True)
    (project / "configs").mkdir()
    shutil.copy2(root / "scripts/prepare_luci_feeds.sh", project / "scripts/prepare_luci_feeds.sh")
    shutil.copy2(root / "configs/feeds.conf", project / "configs/feeds.conf")
    upstream = work / "theme-upstream"
    upstream.mkdir()
    (upstream / "Makefile").write_text("# fixture package\n")
    subprocess.run(["git", "init", "-q", str(upstream)], check=True)
    subprocess.run(["git", "-C", str(upstream), "add", "Makefile"], check=True)
    subprocess.run(["git", "-C", str(upstream), "-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid", "commit", "-qm", "fixture"], check=True)
    revision = subprocess.check_output(["git", "-C", str(upstream), "rev-parse", "HEAD"], text=True).strip()
    (project / "port").mkdir()
    (project / "port/sources.lock.json").write_text(json.dumps({"luci_theme_argon": {"url": str(upstream), "commit": revision}}))
    (project / "port/scripts").mkdir()
    shutil.copy2(root / "port/scripts/apply-luci-patches.sh", project / "port/scripts/apply-luci-patches.sh")
    shutil.copytree(root / "port/luci-patches", project / "port/luci-patches")
    tree = work / "openwrt"
    (tree / "scripts").mkdir(parents=True)
    luci = tree / "feeds/luci"
    luci.mkdir(parents=True)
    for patch in (root / "port/luci-patches").glob("*.patch"):
        for line in patch.read_text().splitlines():
            if line.startswith("--- a/"):
                name = line[6:]
                original = subprocess.check_output(["git", "-C", str(root / "sources/openwrt/feeds/luci"), "show", "HEAD:" + name])
                destination = luci / name
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_bytes(original)
    feed_tool = tree / "scripts/feeds"
    feed_tool.write_text('#!/bin/sh\nprintf "%s\\n" "$*" >> feed-calls\n')
    feed_tool.chmod(0o755)
    (tree / ".config").write_text("CONFIG_PACKAGE_luci=y\nCONFIG_PACKAGE_keep_me=y\n# CONFIG_PACKAGE_luci-theme-argon is not set\n# CONFIG_LUCI_LANG_zh_Hans is not set\n")
    for _ in range(2):
        subprocess.run(["sh", str(project / "scripts/prepare_luci_feeds.sh"), str(tree)], check=True)
    status_source = (luci / "modules/luci-mod-status/htdocs/luci-static/resources/view/status/include/10_system.js").read_text()
    assert status_source.count("var callHardwareStatus = rpc.declare") == 1
    assert "Realtek/RTL9617C（" in status_source
    header = (luci / "modules/luci-base/ucode/template/header.ut").read_text()
    assert header.count("resource_version:") == 1
    assert "-tyfza1-r2'" in header
    assert "base_url       : resource" in header
    assert "-' + pkgs_update_time + '-tyfza1" in header
    assert "当前功率上限" in (luci / "modules/luci-mod-network/htdocs/luci-static/resources/view/network/wireless.js").read_text()
    config = (tree / ".config").read_text()
    assert config.count("CONFIG_PACKAGE_luci-theme-argon=y") == 1
    assert config.count("CONFIG_PACKAGE_luci-i18n-argon-config-zh-cn=y") == 1
    assert config.count("CONFIG_LUCI_LANG_zh_Hans=y") == 1
    assert "# CONFIG_LUCI_LANG_zh_Hans is not set" not in config
    assert "CONFIG_PACKAGE_keep_me=y" in config
    assert "update packages luci\n" in (tree / "feed-calls").read_text()
    assert (tree / "package/luci-theme-argon").is_symlink()
    assert (tree / "package/luci-theme-argon/Makefile").read_text() == "# fixture package\n"
    assert subprocess.check_output(["git", "-C", str(tree / "feeds/luci-theme-argon"), "rev-parse", "HEAD"], text=True).strip() == revision
    (tree / "package/luci-theme-argon/Makefile").write_text("# user edit\n")
    assert subprocess.run(["sh", str(project / "scripts/prepare_luci_feeds.sh"), str(tree)], capture_output=True).returncode == 1
    assert (tree / "package/luci-theme-argon/Makefile").read_text() == "# user edit\n"

print("Argon: pinned standalone package, safe checkout, existing build selection, first-upgrade migration, user preference retention and missing-theme retry passed")
