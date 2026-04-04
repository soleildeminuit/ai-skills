#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
================================================================================
ANALYS AV TRÄDTÄCKNING KRING FÖRSKOLOR
================================================================================

Detta skript är byggt för att analysera trädtäckning i närmiljön kring förskolor
och samtidigt skapa en tydlig, pedagogisk och GIS-korrekt visualisering.

Skriptet kombinerar två typer av geodata:

1. Ett BINÄRT trädtäckningsraster
   - pixelvärde 1 = trädtäckning / vegetation över 3 meter
   - pixelvärde 0 eller NoData = ingen trädtäckning / bakgrund

2. Ett punktlager med förskolor
   - varje förskola representeras av en punktgeometri

För varje förskola beräknar skriptet trädtäckningsgrad inom tre buffertavstånd:
- 25 meter
- 50 meter
- 100 meter

Därefter skapas:
- en CSV-tabell med resultaten
- en interaktiv HTML-karta

================================================================================
VARFÖR VI SKAPAR DETTA SKRIPT
================================================================================

Det här skriptet finns av tre huvudsakliga skäl:

1. ANALYTISK NYTTA
   Vi vill mäta hur trädnära olika förskolor är på ett reproducerbart sätt.

2. PEDAGOGISK NYTTA
   Vi vill visa hur öppna geodata och rasteranalys kan omsättas till konkret
   beslutsstöd i samhällsanalys och samhällsplanering.

3. GIS-KORREKTHET
   Vi vill undvika vanliga fel, särskilt:
   - felaktig CRS-hantering
   - förenklad och felaktig rasteroverlay i webbkarta
   - förväxling mellan punktläge och faktisk gård/fastighet

================================================================================
VIKTIGA METODVAL
================================================================================

- Analysen görs i rasterets koordinatsystem, där meter betyder meter.
- Buffertar beräknas därför i korrekt projektions-CRS.
- Webbkartan visas i EPSG:4326.
- Hela rastergriden reprojiceras till webbkartans CRS.
- Vi använder INTE den felaktiga metoden att bara transformera rasterets hörn
  eller bounding box och sedan lägga bilden som overlay.

================================================================================
VAD RESULTATET BETYDER
================================================================================

Resultatet visar hur stor andel av ytan inom 25 m, 50 m och 100 m runt
förskolepunkten som utgörs av trädtäckning (> 3 m vegetation).

Det betyder:
- detta är ett mått på trädtäckning kring punktläget
- det är inte exakt samma sak som trädtäckning på själva gården
- men det är ett användbart första mått för jämförelse och översikt

================================================================================
KÖRNING
================================================================================

1. Säkerställ att beroenden redan finns installerade i miljön.
2. Anpassa filvägarna i PARAMETRAR nedan om det behövs.
3. Kör skriptet:

   python analysera_tradtackning_forskolor.py

================================================================================
"""

# ==============================================================================
# 1. BERoENDEKONTROLL - CODEX WEB COMPLIANT
# ==============================================================================
#
# Detta skript försöker INTE installera paket automatiskt.
#
# Skäl:
# - Låsta miljöer som Codex web tillåter ofta inte "pip install" under körning.
# - Automatisk installation kan ge 403 Forbidden, proxyfel eller andra nätverksfel.
# - Ett robust skript ska därför kontrollera beroenden och ge tydligt fel om något
#   saknas, men inte försöka ändra miljön självt.
#
# Detta är medvetet och korrekt för Codex web.
# ==============================================================================

import sys
import importlib
from pathlib import Path


REQUIRED_PACKAGES = [
    ("numpy", "numpy"),
    ("pandas", "pandas"),
    ("fiona", "fiona"),
    ("rasterio", "rasterio"),
    ("pyproj", "pyproj"),
    ("shapely", "shapely"),
    ("folium", "folium"),
    ("PIL", "Pillow"),
]


def check_required_packages() -> None:
    """
    Kontrollera att alla nödvändiga paket finns installerade.

    Viktigt:
    --------
    Denna funktion installerar INTE paket. Den gör endast en passiv kontroll.

    Om något paket saknas avslutas skriptet med ett tydligt felmeddelande.
    Detta är avsiktligt för att fungera i låsta körmiljöer, t.ex. Codex web.
    """
    missing = []

    for import_name, pip_name in REQUIRED_PACKAGES:
        try:
            importlib.import_module(import_name)
        except ImportError:
            missing.append((import_name, pip_name))

    if missing:
        lines = [
            "",
            "=" * 80,
            "SAKNADE PYTHON-PAKET",
            "=" * 80,
            "Följande paket saknas i körmiljön:",
            "",
        ]
        for import_name, pip_name in missing:
            lines.append(f"- import '{import_name}'  -> paket '{pip_name}'")

        lines.extend(
            [
                "",
                "Detta skript försöker inte installera paket automatiskt.",
                "Det är avsiktligt för att vara kompatibelt med låsta miljöer som",
                "Codex web, där pip-installation under körning ofta är blockerad.",
                "",
                "Installera paketen i förväg i den miljö där skriptet ska köras.",
                "=" * 80,
                "",
            ]
        )
        raise SystemExit("\n".join(lines))


check_required_packages()

# ==============================================================================
# 2. IMPORTER
# ==============================================================================

import base64
from io import BytesIO

import numpy as np
import pandas as pd
import fiona
import rasterio
from rasterio.features import geometry_mask
from rasterio.enums import Resampling
from rasterio.warp import calculate_default_transform, reproject
from shapely.geometry import shape, box
from shapely.ops import transform as shapely_transform
from pyproj import Transformer
from PIL import Image
import folium

# ==============================================================================
# 3. PARAMETRAR
# ==============================================================================
#
# Här samlas allt som du normalt kan vilja justera mellan körningar.
# Det gör skriptet enklare att förstå och ändra.
# ==============================================================================

# Indatafiler
TREE_RASTER_PATH = Path("data/raw/tradtackning_binar3m_Norrbottens_2580TC115_Luleå.tif")
PRESCHOOL_GPKG_PATH = Path("data/raw/Förskolor_2025_sweref.gpkg")

# Lagernamn i geopackage. Om None används första lagret.
PRESCHOOL_LAYER_NAME = "Förskolor_2025_sweref"

# Buffertavstånd i meter
BUFFER_DISTANCES_M = [25, 50, 100]

# Utdatakatalog
OUTPUT_DIR = Path("output_tradtackning_forskolor")

# Utdatafiler
OUTPUT_CSV = OUTPUT_DIR / "forskolor_lulea_tradtackning_25_50_100m.csv"
OUTPUT_HTML = OUTPUT_DIR / "interaktiv_forskolor_tradtackning_100m_med_25_50_100_popup.html"

# Webbkartans CRS
WEBMAP_TARGET_CRS = "EPSG:4326"

# Maxdimension på raster som används i HTML-kartan
MAX_WEBMAP_RASTER_DIMENSION = 2200

# Vikter för ett enkelt sammanvägt index
WEIGHT_25M = 0.5
WEIGHT_50M = 0.3
WEIGHT_100M = 0.2

# ==============================================================================
# 4. HJÄLPFUNKTIONER
# ==============================================================================


def validate_input_paths() -> None:
    """
    Kontrollera att indatafilerna finns.

    Varför?
    -------
    Det är bättre att stoppa tidigt med tydligt felmeddelande än att låta skriptet
    krascha senare på ett svårtolkat sätt.
    """
    if not TREE_RASTER_PATH.exists():
        raise FileNotFoundError(f"Rasterfil saknas: {TREE_RASTER_PATH}")

    if not PRESCHOOL_GPKG_PATH.exists():
        raise FileNotFoundError(f"GeoPackage saknas: {PRESCHOOL_GPKG_PATH}")


def ensure_output_dir() -> None:
    """
    Skapa utdatakatalog om den inte redan finns.
    """
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)


def choose_gpkg_layer(gpkg_path: Path, requested_layer: str = None) -> str:
    """
    Bestäm vilket lager i geopackage-filen som ska användas.

    Om ett specifikt lager har angetts används det.
    Annars används första tillgängliga lagret.
    """
    layers = fiona.listlayers(gpkg_path)

    if len(layers) == 0:
        raise ValueError(f"Inga lager hittades i geopackage: {gpkg_path}")

    if requested_layer is None:
        return layers[0]

    if requested_layer not in layers:
        raise ValueError(
            f"Angivet lager '{requested_layer}' finns inte i {gpkg_path}. "
            f"Tillgängliga lager: {layers}"
        )

    return requested_layer


def create_transformer_function(source_crs, target_crs):
    """
    Skapa en transformeringsfunktion som kan användas tillsammans med Shapely.

    Varför?
    -------
    Vi behöver ofta transformera geometrier mellan olika CRS.
    Denna funktion kapslar in det och gör koden tydligare.
    """
    transformer = Transformer.from_crs(source_crs, target_crs, always_xy=True)

    def _transform(x, y, z=None):
        return transformer.transform(x, y)

    return _transform


def color_for_100m(pct: float) -> str:
    """
    Välj färg för kartpunkt utifrån trädtäckning inom 100 m.

    Varför 100 m i kartan?
    ----------------------
    100 m fungerar bättre som översiktsmått än 25 m och 50 m.
    Punktkartan blir tydligare och mindre känslig för exakt punktplacering.
    """
    if pct >= 50:
        return "#006d2c"
    if pct >= 35:
        return "#31a354"
    if pct >= 20:
        return "#74c476"
    if pct >= 10:
        return "#bae4b3"
    return "#edf8e9"


def format_pct(value: float) -> str:
    """
    Formatera procenttal med svensk decimalvisning.
    """
    if pd.isna(value):
        return ""
    return str(round(float(value), 2)).replace(".", ",")


# ==============================================================================
# 5. INLÄSNING AV RASTER
# ==============================================================================


def load_tree_raster():
    """
    Läs in trädtäckningsrastret.

    Vad som hämtas
    --------------
    - rasterdata
    - CRS
    - affine transform
    - utbredning
    - storlek
    - nodata

    Varför detta behövs
    -------------------
    Själva analysen görs i rasterets koordinatsystem. Därför måste vi känna till
    alla dessa delar av rasterets georeferens.
    """
    with rasterio.open(TREE_RASTER_PATH) as src:
        raster_data = src.read(1)
        raster_crs = src.crs
        raster_transform = src.transform
        raster_bounds = src.bounds
        raster_width = src.width
        raster_height = src.height
        raster_nodata = src.nodata

    raster_bbox = box(
        raster_bounds.left,
        raster_bounds.bottom,
        raster_bounds.right,
        raster_bounds.top,
    )

    return {
        "data": raster_data,
        "crs": raster_crs,
        "transform": raster_transform,
        "bounds": raster_bounds,
        "bbox": raster_bbox,
        "width": raster_width,
        "height": raster_height,
        "nodata": raster_nodata,
    }


# ==============================================================================
# 6. INLÄSNING AV FÖRSKOLOR
# ==============================================================================


def load_preschool_points(target_analysis_crs):
    """
    Läs in förskolor från geopackage och transformera dem till analys-CRS.

    Varför?
    -------
    Buffertar i meter måste beräknas i ett koordinatsystem där meter verkligen
    betyder meter. Därför använder vi rasterets CRS som analys-CRS.
    """
    layer_name = choose_gpkg_layer(PRESCHOOL_GPKG_PATH, PRESCHOOL_LAYER_NAME)

    features = []
    with fiona.open(PRESCHOOL_GPKG_PATH, layer=layer_name) as src:
        source_crs = src.crs
        for feat in src:
            features.append(feat)

    to_analysis = create_transformer_function(source_crs, target_analysis_crs)
    to_wgs84 = create_transformer_function(target_analysis_crs, WEBMAP_TARGET_CRS)

    results = []

    for feat in features:
        geom = shape(feat["geometry"])
        props = dict(feat["properties"])

        # Vi förväntar oss punktgeometrier.
        if geom.geom_type != "Point":
            continue

        # Transformera till analys-CRS om det behövs.
        if str(source_crs) != str(target_analysis_crs):
            geom_analysis = shapely_transform(to_analysis, geom)
        else:
            geom_analysis = geom

        # Vi sparar också en kopia i WGS84 för webbkartan.
        geom_wgs84 = shapely_transform(to_wgs84, geom_analysis)

        results.append(
            {
                "geometry_analysis": geom_analysis,
                "geometry_wgs84": geom_wgs84,
                "properties": props,
                "source_crs": source_crs,
            }
        )

    return results, layer_name, source_crs


# ==============================================================================
# 7. ZONSTATISTIK: TRÄDTÄCKNING KRING FÖRSKOLOR
# ==============================================================================


def compute_tree_cover_statistics(raster_info, preschool_features):
    """
    Beräkna trädtäckning inom flera buffertavstånd för varje förskola.

    Metod
    -----
    För varje förskolepunkt:
    1. skapa buffert
    2. skapa rastermask
    3. räkna alla pixlar i bufferten
    4. räkna trädpixlar i bufferten
    5. omvandla till andel (%)

    Viktig tolkning
    ---------------
    Eftersom rastret är binärt blir detta ett direkt mått på andel trädtäckt yta
    inom bufferten.
    """
    raster_data = raster_info["data"]
    raster_bbox = raster_info["bbox"]
    raster_transform = raster_info["transform"]
    raster_width = raster_info["width"]
    raster_height = raster_info["height"]

    # Binärt raster: 1 = trädtäckning > 3 m
    tree_mask = raster_data == 1

    rows = []

    for item in preschool_features:
        geom_analysis = item["geometry_analysis"]
        geom_wgs84 = item["geometry_wgs84"]
        props = item["properties"]

        # Ta bara med förskolor som ligger inom rasterutbredningen.
        if not raster_bbox.contains(geom_analysis):
            continue

        name = (
            props.get("Firmabenämning")
            or props.get("Företagsnamn")
            or props.get("Namn")
            or "Förskola"
        )

        address = props.get("Besöksadress", "")
        postal_code = props.get("Postnummer", "")
        postal_area = props.get("Postort", "")
        municipality = props.get("Kommunnamn", "")

        row = {
            "namn": name,
            "adress": address,
            "postnummer": postal_code,
            "postort": postal_area,
            "kommunnamn": municipality,
            "lon": round(geom_wgs84.x, 6),
            "lat": round(geom_wgs84.y, 6),
        }

        for distance_m in BUFFER_DISTANCES_M:
            buffer_geom = geom_analysis.buffer(distance_m)

            mask = geometry_mask(
                [buffer_geom.__geo_interface__],
                out_shape=(raster_height, raster_width),
                transform=raster_transform,
                invert=True,
            )

            total_pixels = int(mask.sum())
            tree_pixels = int(np.logical_and(mask, tree_mask).sum())

            if total_pixels > 0:
                tree_cover_pct = (tree_pixels / total_pixels) * 100.0
            else:
                tree_cover_pct = np.nan

            row[f"pixlar_{distance_m}m"] = total_pixels
            row[f"tradpixlar_{distance_m}m"] = tree_pixels
            row[f"tradtackning_{distance_m}m_procent"] = round(tree_cover_pct, 2)

        # Sammanvägt index.
        # Detta är inte någon absolut sanning, utan ett praktiskt sätt att väga
        # samman flera radier där närzonen får högre vikt.
        row["tradindex_25_50_100"] = round(
            row["tradtackning_25m_procent"] * WEIGHT_25M
            + row["tradtackning_50m_procent"] * WEIGHT_50M
            + row["tradtackning_100m_procent"] * WEIGHT_100M,
            2,
        )

        rows.append(row)

    df = pd.DataFrame(rows)

    if not df.empty:
        df = df.sort_values(
            ["tradtackning_100m_procent", "namn"],
            ascending=[False, True],
        ).reset_index(drop=True)

    return df


# ==============================================================================
# 8. REPROJICERING AV HELA RASTERGRIDEN FÖR WEBBKARTA
# ==============================================================================


def reproject_raster_for_webmap():
    """
    Reprojicera hela rastergriden till EPSG:4326 för korrekt webbkarta.

    Detta är centralt.
    ------------------
    Vi får INTE simulera reprojicering genom att bara flytta rasterets hörn,
    bounds eller bounding box.

    Rätt metod är:
    - verifiera käll-CRS och mål-CRS
    - reprojicera hela rastergriden
    - använda korrekt affine transform
    - använda lämplig resampling

    Det är precis vad denna funktion gör.
    """
    with rasterio.open(TREE_RASTER_PATH) as src:
        transform, width, height = calculate_default_transform(
            src.crs,
            WEBMAP_TARGET_CRS,
            src.width,
            src.height,
            *src.bounds,
        )

        scale = min(
            MAX_WEBMAP_RASTER_DIMENSION / width,
            MAX_WEBMAP_RASTER_DIMENSION / height,
            1.0,
        )

        out_width = max(1, int(width * scale))
        out_height = max(1, int(height * scale))

        scaled_transform = rasterio.Affine(
            transform.a * width / out_width,
            transform.b,
            transform.c,
            transform.d,
            transform.e * height / out_height,
            transform.f,
        )

        dst = np.zeros((out_height, out_width), dtype=np.uint8)

        reproject(
            source=rasterio.band(src, 1),
            destination=dst,
            src_transform=src.transform,
            src_crs=src.crs,
            src_nodata=src.nodata,
            dst_transform=scaled_transform,
            dst_crs=WEBMAP_TARGET_CRS,
            dst_nodata=0,
            resampling=Resampling.nearest,
        )

    return dst, scaled_transform


def raster_to_transparent_png_data_url(raster_4326: np.ndarray) -> str:
    """
    Konvertera det reprojicerade rastret till en transparent PNG som kan visas
    som overlay i Folium/Leaflet.
    """
    rgba = np.zeros((raster_4326.shape[0], raster_4326.shape[1], 4), dtype=np.uint8)

    tree_pixels = raster_4326 == 1
    rgba[..., 0] = 34
    rgba[..., 1] = 139
    rgba[..., 2] = 34
    rgba[..., 3] = np.where(tree_pixels, 115, 0)

    image = Image.fromarray(rgba, mode="RGBA")
    buffer = BytesIO()
    image.save(buffer, format="PNG", optimize=True)

    return "data:image/png;base64," + base64.b64encode(buffer.getvalue()).decode("ascii")


# ==============================================================================
# 9. KARTSKAPANDE
# ==============================================================================


def create_map(
    df: pd.DataFrame,
    raster_overlay_data_url: str,
    raster_4326: np.ndarray,
    raster_transform_4326,
):
    """
    Skapa den interaktiva kartan.

    Designidé
    ---------
    Vi har redan konstaterat att synliga buffertpolygoner för alla förskolor blir
    plottrigt och svårläst. Därför använder vi här en punktkarta:

    - punktfärg = trädtäckning inom 100 m
    - popup = 25 m, 50 m, 100 m samt index
    - raster i bakgrunden för kontext

    Detta ger en tydligare översiktskarta.
    """
    out_height, out_width = raster_4326.shape

    west = raster_transform_4326.c
    north = raster_transform_4326.f
    east = west + raster_transform_4326.a * out_width
    south = north + raster_transform_4326.e * out_height

    center = [(south + north) / 2, (west + east) / 2]

    m = folium.Map(
        location=center,
        zoom_start=12,
        control_scale=True,
        tiles=None,
    )

    # Vi använder CartoDB Positron som bakgrund.
    # Detta är mer robust för lokal HTML än OSM-standardtiles.
    folium.TileLayer("CartoDB positron", name="Bakgrund", show=True).add_to(m)

    folium.raster_layers.ImageOverlay(
        image=raster_overlay_data_url,
        bounds=[[south, west], [north, east]],
        opacity=0.62,
        name="Trädtäckning > 3 m",
        show=True,
    ).add_to(m)

    point_group = folium.FeatureGroup(
        name="Förskolor (färg = trädtäckning inom 100 m)",
        show=True,
    )

    for _, row in df.iterrows():
        point_color = color_for_100m(row["tradtackning_100m_procent"])

        popup_html = f"""
        <div style="font-family: Arial, sans-serif; font-size: 13px; line-height: 1.45; min-width: 250px;">
            <div style="font-weight: 700; margin-bottom: 4px;">{row['namn']}</div>
            <div>{row['adress']}</div>
            <div>{row['postnummer']} {row['postort']}</div>
            <div style="margin: 8px 0 4px; font-weight: 700;">Trädtäckning</div>
            <div>25 m: {format_pct(row['tradtackning_25m_procent'])} %</div>
            <div>50 m: {format_pct(row['tradtackning_50m_procent'])} %</div>
            <div>100 m: {format_pct(row['tradtackning_100m_procent'])} %</div>
            <div style="margin-top: 6px;">Index 25/50/100: {format_pct(row['tradindex_25_50_100'])}</div>
        </div>
        """

        folium.CircleMarker(
            location=[row["lat"], row["lon"]],
            radius=9,
            weight=1.5,
            color="#404040",
            fill=True,
            fill_color=point_color,
            fill_opacity=0.92,
            tooltip=f"{row['namn']}: {format_pct(row['tradtackning_100m_procent'])} % inom 100 m",
            popup=folium.Popup(popup_html, max_width=320),
        ).add_to(point_group)

    point_group.add_to(m)

    df_sorted = df.sort_values(
        ["tradtackning_100m_procent", "namn"],
        ascending=[False, True],
    ).reset_index(drop=True)

    top5 = df_sorted.head(5)[["namn", "tradtackning_100m_procent"]].values.tolist()
    bottom5 = df_sorted.tail(5)[["namn", "tradtackning_100m_procent"]].values.tolist()

    top_html = "<br>".join(
        [f"{i + 1}. {name} – {format_pct(value)} %" for i, (name, value) in enumerate(top5)]
    )
    bottom_html = "<br>".join(
        [f"{i + 1}. {name} – {format_pct(value)} %" for i, (name, value) in enumerate(bottom5)]
    )

    title_html = """
    <div style="
        position: fixed;
        top: 10px; left: 50px; z-index: 1000;
        background: white; padding: 10px 12px; border: 1px solid #bbb;
        border-radius: 6px; box-shadow: 0 1px 4px rgba(0,0,0,0.2);
        font-family: Arial, sans-serif; font-size: 14px;">
        <div style="font-weight: 700; margin-bottom: 4px;">Förskolor och trädtäckning</div>
        <div>Luleå • punktkarta • färg = trädtäckning inom 100 m</div>
    </div>
    """

    legend_html = """
    <div style="
        position: fixed;
        bottom: 25px; left: 10px; z-index: 1000;
        background: white; padding: 10px 12px; border: 1px solid #bbb;
        border-radius: 6px; box-shadow: 0 1px 4px rgba(0,0,0,0.2);
        font-family: Arial, sans-serif; font-size: 12px; line-height: 1.4;">
        <div style="font-weight: 700; margin-bottom: 6px;">Punktfärg = trädtäckning inom 100 m</div>
        <div><span style="display:inline-block;width:14px;height:14px;background:#006d2c;border:1px solid #666;margin-right:6px;"></span> ≥ 50 %</div>
        <div><span style="display:inline-block;width:14px;height:14px;background:#31a354;border:1px solid #666;margin-right:6px;"></span> 35–49,9 %</div>
        <div><span style="display:inline-block;width:14px;height:14px;background:#74c476;border:1px solid #666;margin-right:6px;"></span> 20–34,9 %</div>
        <div><span style="display:inline-block;width:14px;height:14px;background:#bae4b3;border:1px solid #666;margin-right:6px;"></span> 10–19,9 %</div>
        <div><span style="display:inline-block;width:14px;height:14px;background:#edf8e9;border:1px solid #666;margin-right:6px;"></span> &lt; 10 %</div>
        <div style="margin-top: 8px; color: #444;">Popup visar 25 m, 50 m och 100 m</div>
    </div>
    """

    rank_html = f"""
    <div style="
        position: fixed;
        bottom: 25px; right: 10px; z-index: 1000;
        background: white; padding: 10px 12px; border: 1px solid #bbb;
        border-radius: 6px; box-shadow: 0 1px 4px rgba(0,0,0,0.2);
        font-family: Arial, sans-serif; font-size: 12px; line-height: 1.35; max-width: 340px;">
        <div style="font-weight: 700; margin-bottom: 4px;">Högst trädtäckning inom 100 m</div>
        <div>{top_html}</div>
        <div style="font-weight: 700; margin: 8px 0 4px;">Lägst trädtäckning inom 100 m</div>
        <div>{bottom_html}</div>
    </div>
    """

    m.get_root().html.add_child(folium.Element(title_html))
    m.get_root().html.add_child(folium.Element(legend_html))
    m.get_root().html.add_child(folium.Element(rank_html))

    folium.LayerControl(collapsed=False).add_to(m)
    m.fit_bounds([[south, west], [north, east]])

    return m


# ==============================================================================
# 10. TERMINALSAMMANFATTNING
# ==============================================================================


def print_summary(df: pd.DataFrame) -> None:
    """
    Skriv en kort sammanfattning till terminalen så att användaren direkt ser
    att skriptet fungerade och vad huvudresultaten blev.
    """
    print("\n" + "=" * 80)
    print("SAMMANFATTNING")
    print("=" * 80)
    print(f"Antal förskolor i analysen: {len(df)}")
    print()

    if len(df) == 0:
        print("Inga förskolor analyserades.")
        return

    print("Topp 10 efter trädtäckning inom 100 m:")
    print(
        df[
            [
                "namn",
                "tradtackning_25m_procent",
                "tradtackning_50m_procent",
                "tradtackning_100m_procent",
            ]
        ]
        .head(10)
        .to_string(index=False)
    )
    print()

    print("Lägst 10 efter trädtäckning inom 100 m:")
    print(
        df[
            [
                "namn",
                "tradtackning_25m_procent",
                "tradtackning_50m_procent",
                "tradtackning_100m_procent",
            ]
        ]
        .tail(10)
        .to_string(index=False)
    )
    print()


# ==============================================================================
# 11. MAIN
# ==============================================================================


def main():
    """
    Huvudflöde för hela skriptet.

    Arbetsgång
    ----------
    1. Kontrollera beroenden
    2. Kontrollera indata
    3. Skapa utdatakatalog
    4. Läs raster
    5. Läs förskolor
    6. Beräkna zonstatistik
    7. Spara CSV
    8. Reprojicera hela rastret till webbkartan
    9. Skapa rasteroverlay
    10. Bygg interaktiv karta
    11. Spara HTML
    12. Skriv sammanfattning i terminalen
    """
    print("[INFO] Startar analys av trädtäckning kring förskolor...")

    validate_input_paths()
    ensure_output_dir()

    print("[INFO] Läser trädtäckningsraster...")
    raster_info = load_tree_raster()
    print(f"[INFO] Raster CRS: {raster_info['crs']}")

    print("[INFO] Läser förskolor...")
    preschool_features, layer_name, preschool_crs = load_preschool_points(raster_info["crs"])
    print(f"[INFO] Förskolelager: {layer_name}")
    print(f"[INFO] Förskole-CRS: {preschool_crs}")

    print("[INFO] Beräknar trädtäckning för 25 m, 50 m och 100 m...")
    df = compute_tree_cover_statistics(raster_info, preschool_features)

    print(f"[INFO] Sparar CSV: {OUTPUT_CSV}")
    df.to_csv(OUTPUT_CSV, index=False, encoding="utf-8-sig")

    print("[INFO] Reprojicerar hela rastergriden till webbkartan...")
    raster_4326, raster_transform_4326 = reproject_raster_for_webmap()

    print("[INFO] Skapar transparent rasteroverlay...")
    raster_overlay_data_url = raster_to_transparent_png_data_url(raster_4326)

    print("[INFO] Bygger interaktiv karta...")
    map_object = create_map(df, raster_overlay_data_url, raster_4326, raster_transform_4326)

    print(f"[INFO] Sparar HTML-karta: {OUTPUT_HTML}")
    map_object.save(OUTPUT_HTML)

    print_summary(df)

    print("[INFO] Klart.")
    print(f"[INFO] CSV:  {OUTPUT_CSV.resolve()}")
    print(f"[INFO] HTML: {OUTPUT_HTML.resolve()}")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        raise SystemExit("\n[AVBRUTET] Körningen avbröts av användaren.")
    except Exception as exc:
        raise SystemExit(f"\n[FEL] {exc}")