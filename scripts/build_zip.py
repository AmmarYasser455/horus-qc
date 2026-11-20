"""Build the installable plugin ZIP and refresh plugins.xml.

    python scripts/build_zip.py            ->  dist/horus_qc-<version>.zip

The ZIP installs with QGIS > Plugins > Manage and Install Plugins > Install from ZIP.
plugins.xml lets a team add this repo as a plugin repository and get updates.
"""

import configparser
import datetime
import html
import os
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PKG = "horus_qc"
REPO = "https://github.com/AmmarYasser455/horus-qc"


def metadata():
    cp = configparser.ConfigParser(interpolation=None)
    cp.read(os.path.join(ROOT, PKG, "metadata.txt"), encoding="utf-8")
    return cp["general"]


def build_zip(version):
    os.makedirs(os.path.join(ROOT, "dist"), exist_ok=True)
    out = os.path.join(ROOT, "dist", "%s-%s.zip" % (PKG, version))
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zf:
        for folder, dirs, files in os.walk(os.path.join(ROOT, PKG)):
            dirs[:] = [d for d in dirs if d != "__pycache__"]
            for name in sorted(files):
                if name.endswith((".pyc", ".pyo")):
                    continue
                full = os.path.join(folder, name)
                zf.write(full, os.path.relpath(full, ROOT))
        zf.write(os.path.join(ROOT, "LICENSE"), "%s/LICENSE" % PKG)
    return out


def write_plugins_xml(md, version):
    today = datetime.date.today().isoformat()
    esc = lambda s: html.escape(s.strip(), quote=True)  # noqa: E731
    xml = """<?xml version="1.0" encoding="UTF-8"?>
<plugins>
  <pyqgis_plugin name="{name}" version="{v}" plugin_id="{pkg}">
    <description>{desc}</description>
    <about>{about}</about>
    <version>{v}</version>
    <qgis_minimum_version>{qmin}</qgis_minimum_version>
    <qgis_maximum_version>{qmax}</qgis_maximum_version>
    <supports_qt6>True</supports_qt6>
    <homepage>{repo}</homepage>
    <file_name>{pkg}-{v}.zip</file_name>
    <icon>{repo}/raw/main/{pkg}/icons/horus.png</icon>
    <author_name>{author}</author_name>
    <download_url>{repo}/releases/download/v{v}/{pkg}-{v}.zip</download_url>
    <uploaded_by>AmmarYasser455</uploaded_by>
    <create_date>{today}</create_date>
    <update_date>{today}</update_date>
    <experimental>False</experimental>
    <deprecated>False</deprecated>
    <tracker>{repo}/issues</tracker>
    <repository>{repo}</repository>
    <tags>{tags}</tags>
    <server>False</server>
  </pyqgis_plugin>
</plugins>
""".format(name=esc(md["name"]), v=version, pkg=PKG, desc=esc(md["description"]), about=esc(md["about"]),
           qmin=md["qgisMinimumVersion"] + ".0", qmax=md["qgisMaximumVersion"] + ".0", repo=REPO,
           author=esc(md["author"]), today=today, tags=esc(md["tags"]))
    with open(os.path.join(ROOT, "plugins.xml"), "w", encoding="utf-8", newline="\n") as fh:
        fh.write(xml)


if __name__ == "__main__":
    md = metadata()
    version = md["version"]
    path = build_zip(version)
    write_plugins_xml(md, version)
    print(path)
