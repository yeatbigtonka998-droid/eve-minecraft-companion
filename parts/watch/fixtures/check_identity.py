"""O40 step 8 (pin identity): check foundation's identity.txt (deployed eve_mind vs archive) against git itself.
    py -3.12 parts/watch/fixtures/check_identity.py <identity.txt> <commit>
"""
import hashlib
import subprocess
import sys

ident, rev = sys.argv[1], sys.argv[2]
sec, dep, arc = None, {}, {}
for line in open(ident, encoding="utf-8-sig").read().splitlines():
    if line.startswith("## "):
        sec = line[3:].strip()
        continue
    parts = line.split()
    if sec in ("deployed", "archive") and len(parts) == 2 and len(parts[1]) == 64:
        (dep if sec == "deployed" else arc)[parts[0].lstrip("\\/").replace("\\", "/")] = parts[1].lower()
tree = [t for t in subprocess.run(["git", "ls-tree", "-r", "--name-only", rev, "parts/mind/src/eve_mind"],
                                  capture_output=True, text=True).stdout.split() if t.endswith(".py")]
ok, bad = 0, []
for t in tree:
    blob = subprocess.run(["git", "show", f"{rev}:{t}"], capture_output=True).stdout
    crlf = blob.replace(b"\r\n", b"\n").replace(b"\n", b"\r\n")   # a Windows git archive (core.autocrlf true) writes CRLF
    name = t.split("eve_mind/")[-1]
    if dep.get(name) == hashlib.sha256(crlf).hexdigest():
        ok += 1
    else:
        bad.append(name)
names = {t.split("eve_mind/")[-1] for t in tree}
print(f"deployed={len(dep)} archive={len(arc)} lists equal={dep == arc}; deployed == git {rev} (CRLF): {ok}/{len(tree)}; "
      f"bad={bad}; extra={sorted(set(dep) - names)}; missing={sorted(names - set(dep))}")
