"""
QGIS_Map loader
---------------
Loads one .tif and one .csv (P,E,N,Z) from C:\\QGIS_Map.
CSV coordinates are GDA2020 / MGA Zone 53 (EPSG:7853).
Draws a red 300 m buffer and a blue 500 m buffer around every point.

Run: QGIS > Plugins > Python Console > Show Editor > Open this file > Run.
"""

import os
import csv
import glob

from qgis.core import (
    QgsProject,
    QgsRasterLayer,
    QgsVectorLayer,
    QgsFeature,
    QgsGeometry,
    QgsPointXY,
    QgsCoordinateReferenceSystem,
    QgsFillSymbol,
    QgsMarkerSymbol,
    QgsDataSourceUri,
    QgsSettings,
    QgsSingleSymbolRenderer,
)
from qgis.utils import iface


FOLDER = r"D:\QGIS_Map"
CRS = "EPSG:7853"            # GDA2020 / MGA Zone 53
BUFFERS = [                  # (radius_m, layer name, RGB colour)
    (500, "Buffer 500 m", "255,0,0"),
    (250, "Buffer 250 m", "255,255,0"),
]
SEGMENTS = 64                # circle smoothness
OUTLINE_MM = 0.6             # buffer line width
CLEAR_PROJECT = True         # start from an empty project each run
SAVE_PROJECT = True          # save C:\QGIS_Map\QGIS_Map.qgz
XYZ_NAME = "Google Satellite"
XYZ_URL = "https://mt1.google.com/vt/lyrs=s&x={x}&y={y}&z={z}"
XYZ_ZMAX = 20


def find_file(patterns):
    """Return the newest file matching any pattern in FOLDER."""
    files = []
    for p in patterns:
        files += glob.glob(os.path.join(FOLDER, p))
    files = sorted(set(files), key=os.path.getmtime, reverse=True)
    if not files:
        raise FileNotFoundError(f"No {patterns} file found in {FOLDER}")
    if len(files) > 1:
        print(f"More than one {patterns} file found. Using newest: {files[0]}")
    return files[0]


def load_points(csv_path):
    """Read P,N,E,Z rows into a memory point layer. Skips header/bad rows."""
    layer = QgsVectorLayer(
        f"Point?crs={CRS}&field=P:string&field=N:double&field=E:double&field=Z:double",
        "Points",
        "memory",
    )
    feats, skipped = [], 0
    with open(csv_path, newline="", encoding="utf-8-sig") as f:
        for row in csv.reader(f):
            if len(row) < 3:
                skipped += 1
                continue
            try:
                p = row[0].strip()
                n = float(row[1])
                e = float(row[2])
                z = float(row[3]) if len(row) > 3 and row[3].strip() else None
            except ValueError:
                skipped += 1          # header row or bad data
                continue
            feat = QgsFeature(layer.fields())
            feat.setGeometry(QgsGeometry.fromPointXY(QgsPointXY(e, n)))
            feat.setAttributes([p, n, e, z])
            feats.append(feat)

    if not feats:
        raise ValueError(f"No valid P,N,E,Z rows in {csv_path}")

    layer.dataProvider().addFeatures(feats)
    layer.updateExtents()
    print(f"Loaded {len(feats)} points. Skipped {skipped} rows.")
    return layer


def make_buffer(points, radius, name, rgb):
    """Buffer every point by radius (metres), dissolve into one feature."""
    layer = QgsVectorLayer(
        f"MultiPolygon?crs={CRS}&field=radius_m:integer",
        name,
        "memory",
    )
    circles = [pt.geometry().buffer(radius, SEGMENTS) for pt in points.getFeatures()]
    dissolved = QgsGeometry.unaryUnion(circles)
    dissolved.convertToMultiType()
 
    feat = QgsFeature(layer.fields())
    feat.setGeometry(dissolved)
    feat.setAttributes([radius])
    layer.dataProvider().addFeatures([feat])
    layer.updateExtents()
 
    symbol = QgsFillSymbol.createSimple({
        "color": "0,0,0,0",           # no fill
        "outline_color": rgb,
        "outline_width": str(OUTLINE_MM),
    })
    layer.renderer().setSymbol(symbol)
    return layer


# XYZ connection storage paths.
# QGIS 3.30+ and 4.x use the first; older QGIS 3.x uses the second.
XYZ_ROOTS = ["connections/xyz/items", "qgis/connections-xyz"]


def find_xyz_connection():
    """Return (name, url, zmax) of a saved Google Satellite connection, or None."""
    s = QgsSettings()
    found = []
    for root in XYZ_ROOTS:
        s.beginGroup(root)
        for n in s.childGroups():
            url = s.value(f"{n}/url", "") or ""
            zmax = s.value(f"{n}/zmax", XYZ_ZMAX)
            found.append((n, url, zmax))
        s.endGroup()

    # 1. Exact name match
    for n, url, zmax in found:
        if n == XYZ_NAME and url:
            return n, url, zmax
    # 2. Any connection already pointing at Google satellite tiles
    for n, url, zmax in found:
        if "google" in url.lower() and "lyrs=s" in url:
            return n, url, zmax
    return None


def save_xyz_connection():
    """Save Google Satellite to the Browser XYZ Tiles list (both formats)."""
    s = QgsSettings()
    for root in XYZ_ROOTS:
        base = f"{root}/{XYZ_NAME}"
        s.setValue(f"{base}/url", XYZ_URL)
        s.setValue(f"{base}/zmin", 0)
        s.setValue(f"{base}/zmax", XYZ_ZMAX)
    s.sync()
    try:
        iface.reloadConnections()   # refresh the Browser panel
    except Exception:
        pass


def google_satellite_layer():
    """Use an existing Google Satellite XYZ connection, or create one."""
    match = find_xyz_connection()
    if match:
        name, url, zmax = match
        print(f"Using existing XYZ connection: {name}")
    else:
        save_xyz_connection()
        name, url, zmax = XYZ_NAME, XYZ_URL, XYZ_ZMAX
        print(f"Created XYZ connection: {name}")

    uri = QgsDataSourceUri()
    uri.setParam("type", "xyz")
    uri.setParam("url", url)
    uri.setParam("zmin", "0")
    uri.setParam("zmax", str(zmax))
    uri_str = bytes(uri.encodedUri()).decode()

    layer = QgsRasterLayer(uri_str, name, "wms")
    if not layer.isValid():
        raise RuntimeError("Could not load Google Satellite XYZ layer. Check internet access.")
    return layer


def load_blast_guards():
    """Load Blast_Guards.gpkg from FOLDER. Skip if missing or invalid."""
    path = os.path.join(FOLDER, "Blast_Guards.gpkg")
    if not os.path.isfile(path):
        print("Blast_Guards.gpkg not found. Skipping.")
        return None

    layer = QgsVectorLayer(path, "Blast Guards", "ogr")
    if not layer.isValid():
        print("Blast_Guards.gpkg could not be loaded. Skipping.")
        return None

    renderer = layer.renderer()
    if isinstance(renderer, QgsSingleSymbolRenderer):
        renderer.setSymbol(QgsMarkerSymbol.createSimple({
            "name": "triangle",
            "color": "255,128,0",
            "outline_color": "0,0,0",
            "size": "3.5",
        }))
    else:
        print(f"Using saved style from Blast_Guards.gpkg ({renderer.type()}).")
    QgsProject.instance().addMapLayer(layer)
    print(f"Loaded Blast Guards: {layer.featureCount()} points.")
    return layer


def main():
    project = QgsProject.instance()
    if CLEAR_PROJECT:
        project.clear()
    project.setCrs(QgsCoordinateReferenceSystem(CRS))

   # Background satellite (added first so it sits at the very bottom)
    project.addMapLayer(google_satellite_layer())

    # Base image (added first so it sits above satellite)
    tif_path = find_file(["*.tif", "*.tiff"])
    raster = QgsRasterLayer(tif_path, os.path.splitext(os.path.basename(tif_path))[0])
    if not raster.isValid():
        raise RuntimeError(f"Could not load raster: {tif_path}")
    project.addMapLayer(raster)

    # Points
    csv_path = find_file(["*.csv"])
    points = load_points(csv_path)
    points.renderer().setSymbol(QgsMarkerSymbol.createSimple({
        "name": "circle", "color": "255,255,0", "outline_color": "0,0,0", "size": "2",
    }))

    # Buffers (500 m below 300 m), then points on top
    largest = None
    for radius, name, rgb in BUFFERS:
        buf = make_buffer(points, radius, name, rgb)
        project.addMapLayer(buf)
        if largest is None:
            largest = buf
    project.addMapLayer(points)

    # Load blast guards
    load_blast_guards()

    # Zoom to the 500 m buffers
    extent = largest.extent()
    extent.grow(50)
    iface.mapCanvas().setExtent(extent)
    iface.mapCanvas().refresh()

    if SAVE_PROJECT:
        out = os.path.join(FOLDER, "QGIS_Map.qgz")
        project.write(out)
        print(f"Project saved: {out}")

    iface.messageBar().pushSuccess("QGIS_Map", "TIF, points and buffers loaded.")


main()