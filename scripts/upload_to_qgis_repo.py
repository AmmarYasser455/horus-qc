"""Upload the plugin ZIP to the official QGIS plugin repository (plugins.qgis.org).

    OSGEO_USERNAME=... OSGEO_PASSWORD=... python scripts/upload_to_qgis_repo.py dist/horus_qc-1.0.0.zip

Uses the repository's XML-RPC API (standard library only). The credentials are an
OSGeo ID; in GitHub Actions they come from the repository secrets of the same names.
The first upload of a new plugin is reviewed by the QGIS plugin team before it is
listed; later versions from the same owner are published automatically.
"""

import os
import sys
import xmlrpc.client
from urllib.parse import quote

ENDPOINT = "plugins.qgis.org:443/plugins/RPC2/"


def upload(zip_path, username, password):
    url = "https://%s:%s@%s" % (quote(username, safe=""), quote(password, safe=""), ENDPOINT)
    server = xmlrpc.client.ServerProxy(url, verbose=False)
    with open(zip_path, "rb") as fh:
        data = xmlrpc.client.Binary(fh.read())
    return server.plugin.upload(data)


def main():
    if len(sys.argv) != 2:
        sys.exit("usage: upload_to_qgis_repo.py <plugin.zip>")
    user = os.environ.get("OSGEO_USERNAME", "").strip()
    password = os.environ.get("OSGEO_PASSWORD", "")
    if not user or not password:
        sys.exit("Set OSGEO_USERNAME and OSGEO_PASSWORD (your OSGeo ID) to upload.")
    try:
        plugin_id, version_id = upload(sys.argv[1], user, password)
    except xmlrpc.client.ProtocolError as e:
        # never print e.url: it contains the credentials
        sys.exit("Upload refused: HTTP %s %s (check the OSGeo username/password)." % (e.errcode, e.errmsg))
    except xmlrpc.client.Fault as e:
        sys.exit("plugins.qgis.org rejected the ZIP:\n%s" % e.faultString)
    print("Uploaded to plugins.qgis.org: plugin id %s, version id %s" % (plugin_id, version_id))
    print("https://plugins.qgis.org/plugins/horus_qc/")


if __name__ == "__main__":
    main()
