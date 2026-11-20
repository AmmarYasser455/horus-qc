"""Prepare a new release in one command.

    python scripts/bump_version.py 1.1.0 "Added X; fixed Y"

Updates metadata.txt (version + changelog), adds a CHANGELOG.md entry and
refreshes plugins.xml. Then commit, tag and push:

    git commit -am "Release 1.1.0" && git tag v1.1.0 && git push && git push origin v1.1.0

The release workflow builds the ZIP, creates the GitHub release and publishes
the new version to plugins.qgis.org, so every user gets the update in QGIS.
"""

import datetime
import os
import re
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
META = os.path.join(ROOT, "horus_qc", "metadata.txt")
CHANGELOG = os.path.join(ROOT, "CHANGELOG.md")


def main():
    if len(sys.argv) < 2 or not re.fullmatch(r"\d+\.\d+\.\d+", sys.argv[1]):
        sys.exit('usage: bump_version.py X.Y.Z "what changed"')
    version = sys.argv[1]
    note = sys.argv[2] if len(sys.argv) > 2 else "Maintenance release."

    with open(META, encoding="utf-8") as fh:
        meta = fh.read()
    meta = re.sub(r"(?m)^version=.*$", "version=%s" % version, meta)
    meta = re.sub(r"(?m)^changelog=", "changelog=%s %s\n    " % (version, note.replace("\n", " ")), meta, count=1)
    with open(META, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(meta)

    with open(CHANGELOG, encoding="utf-8") as fh:
        log = fh.read()
    entry = "## %s (%s)\n\n- %s\n\n" % (version, datetime.date.today().isoformat(), note)
    log = log.replace("# Changelog\n\n", "# Changelog\n\n" + entry, 1)
    with open(CHANGELOG, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(log)

    subprocess.check_call([sys.executable, os.path.join(ROOT, "scripts", "build_zip.py")])
    print("Ready. Now run:\n  git commit -am \"Release %s\" && git tag v%s && git push && git push origin v%s"
          % (version, version, version))


if __name__ == "__main__":
    main()
