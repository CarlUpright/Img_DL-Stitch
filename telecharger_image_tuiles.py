#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Téléchargeur d'images haute résolution - Google Arts & Culture
Supporte deux modes :
  - Image tuilée  (URL avec x/y/z) : assemble toutes les tuiles
  - Image simple  (URL avec w/h)   : télécharge à la résolution maximale
"""

import re
import sys
import os
import argparse
from io import BytesIO
from pathlib import Path
from datetime import datetime

# Dossier du script — les fichiers seront sauvegardés ici par défaut
SCRIPT_DIR = Path(__file__).parent

# Force UTF-8 sur Windows
if sys.platform == 'win32':
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')

try:
    import requests
except ImportError:
    print("Installation de 'requests'...")
    os.system(f'"{sys.executable}" -m pip install requests')
    import requests

try:
    from PIL import Image
except ImportError:
    print("Installation de 'Pillow'...")
    os.system(f'"{sys.executable}" -m pip install Pillow')
    from PIL import Image


# ── Réseau ───────────────────────────────────────────────────────────────────

def make_session():
    s = requests.Session()
    s.headers.update({
        'User-Agent': (
            'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
            'AppleWebKit/537.36 (KHTML, like Gecko) '
            'Chrome/124.0.0.0 Safari/537.36'
        ),
        'Referer': 'https://artsandculture.google.com/',
    })
    return s


def fetch_image(session, url):
    """Télécharge une image ; retourne (Image PIL, bytes) ou (None, None)."""
    try:
        r = session.get(url, timeout=30)
        ct = r.headers.get('Content-Type', '')
        if r.status_code == 200 and 'image' in ct:
            data = r.content
            return Image.open(BytesIO(data)), data
    except Exception:
        pass
    return None, None


def has_transparency(img):
    return img.mode in ('RGBA', 'LA') or (img.mode == 'P' and 'transparency' in img.info)


def fetch_tile(session, url):
    img, _ = fetch_image(session, url)
    if img is None:
        return None
    # Pour l'assemblage des tuiles on a besoin d'un mode cohérent
    if has_transparency(img):
        return img.convert('RGBA')
    return img.convert('RGB')


# ── Détection du type d'URL ───────────────────────────────────────────────────

def is_blob_url(url):
    return url.startswith('blob:')


def parse_url(url):
    """
    Retourne (base, params_dict) pour toute URL lh3.googleusercontent.com.
    Accepte les URLs avec ou sans paramètres après '='.
    """
    # URL avec paramètres : .../[ID]=[params]
    m = re.match(r'(https?://lh3\.googleusercontent\.com/[^=\s]+)=([^\s]*)', url)
    if m:
        base = m.group(1)
        raw = m.group(2)
        params = {'_raw': raw}
        for kv in re.finditer(r'([a-zA-Z])(\d+)', raw):
            params[kv.group(1)] = int(kv.group(2))
        return base, params

    # URL sans paramètres : .../[ID]  (pas de '=')
    m = re.match(r'(https?://lh3\.googleusercontent\.com/\S+)', url)
    if m:
        return m.group(1), {'_raw': ''}

    return None, None


def is_tiled_url(params):
    """True si l'URL contient des coordonnées de tuile (x et y)."""
    return 'x' in params and 'y' in params


# ── Format ggpht (panoramas) ─────────────────────────────────────────────────
# URL : https://lh3.ggpht.com/{prefix}/x{X}-y{Y}-z{Z}/{IMG_ID}

def parse_ggpht_url(url):
    """Retourne (prefix, img_id, x, y, z) ou None."""
    m = re.match(
        r'(https?://lh3\.ggpht\.com/[^/]+(?:/[^/]+)*)/x(\d+)-y(\d+)-z(\d+)/([A-Za-z0-9_\-]+)\s*$',
        url.strip()
    )
    if m:
        return m.group(1), m.group(5), int(m.group(2)), int(m.group(3)), int(m.group(4))
    return None


def build_ggpht_url(prefix, img_id, x, y, z):
    return f"{prefix}/x{x}-y{y}-z{z}/{img_id}"


def find_max_zoom_ggpht(session, prefix, img_id, z_start):
    print("Recherche du zoom maximum...")
    best = z_start
    for z in range(z_start, z_start + 8):
        if fetch_tile(session, build_ggpht_url(prefix, img_id, 0, 0, z)):
            best = z
        else:
            break
    print(f"  Zoom maximum : z={best}")
    return best


def find_grid_ggpht(session, prefix, img_id, z):
    print("Detection des dimensions de la grille...")
    cols = 0
    for x in range(500):
        if fetch_tile(session, build_ggpht_url(prefix, img_id, x, 0, z)) is None:
            cols = x
            break
    else:
        cols = 500

    rows = 0
    for y in range(500):
        if fetch_tile(session, build_ggpht_url(prefix, img_id, 0, y, z)) is None:
            rows = y
            break
    else:
        rows = 500

    print(f"  {cols} colonnes x {rows} lignes = {cols * rows} tuiles")
    return cols, rows


def download_ggpht_panorama(session, prefix, img_id, z_start, output_path, force_zoom, no_max_zoom):
    print(f"Mode PANORAMA ggpht detecte")
    print(f"  Prefix : {prefix}")
    print(f"  ID     : {img_id}\n")

    if force_zoom is not None:
        z = force_zoom
        print(f"Zoom force : z={z}")
    elif no_max_zoom:
        z = z_start
        print(f"Zoom de l'URL : z={z}")
    else:
        z = find_max_zoom_ggpht(session, prefix, img_id, z_start)

    cols, rows = find_grid_ggpht(session, prefix, img_id, z)

    if cols == 0 or rows == 0:
        print("Impossible de detecter la grille.")
        return None

    total = cols * rows
    tiles = {}
    failed = []
    print(f"\nTelechargement de {total} tuiles...")

    for y in range(rows):
        for x in range(cols):
            u = build_ggpht_url(prefix, img_id, x, y, z)
            tile = fetch_tile(session, u)
            n = len(tiles) + len(failed) + 1
            if tile:
                tiles[(x, y)] = tile
                print(f"\r  {n}/{total}  ({x},{y}) OK   ", end='', flush=True)
            else:
                failed.append((x, y))
                print(f"\r  {n}/{total}  ({x},{y}) ECHEC", end='', flush=True)

    print(f"\n  OK : {len(tiles)}   Echecs : {len(failed)}")

    if not tiles:
        print("Aucune tuile telechargee.")
        return None

    sample = next(iter(tiles.values()))
    tw, th = sample.size

    print("\nAssemblage...")
    full = stitch(tiles, cols, rows, tw, th)

    transparent = has_transparency(full)
    ext = 'png' if transparent else 'jpg'
    if output_path is None:
        ts = datetime.now().strftime('%Y-%m-%d-%H-%M-%S')
        output_path = SCRIPT_DIR / f'{ts}_-_Image.{ext}'
    out = Path(output_path)

    if transparent:
        print("Transparence detectee -> sauvegarde en PNG")
        full.save(out, 'PNG')
    else:
        full.save(out, 'JPEG', quality=95)

    mb = out.stat().st_size / 1024 / 1024
    print(f"\nSauvegarde : {out.resolve()}")
    print(f"Dimensions : {full.width} x {full.height} pixels")
    print(f"Taille     : {mb:.1f} Mo")
    return str(out.resolve())


# ── Mode image simple ─────────────────────────────────────────────────────────

# Suffixes à essayer du plus grand au plus petit
_RESOLUTION_CANDIDATES = [
    ('=s0',              'original (s0)'),
    ('=d',               'download (d)'),
    ('=w9999-h9999',     'w9999-h9999'),
    ('=w9999',           'w9999'),
    ('=w4096-h4096',     'w4096-h4096'),
    ('=w2048-h2048',     'w2048-h2048'),
]


def download_single_image(session, base, output_path):
    """
    Essaie plusieurs tailles et garde la plus grande image obtenue.
    """
    print("Mode image simple (pas de tuiles détectées)")
    print("Recherche de la résolution maximale disponible...")

    best_img = None
    best_pixels = 0
    best_label = ''

    for suffix, label in _RESOLUTION_CANDIDATES:
        url = base + suffix
        img, _ = fetch_image(session, url)
        if img:
            px = img.width * img.height
            print(f"  {label:20s} => {img.width} x {img.height} px")
            if px > best_pixels:
                best_pixels = px
                best_img = img
                best_label = label
        else:
            print(f"  {label:20s} => indisponible")

    if best_img is None:
        print("\nImpossible de télécharger l'image.")
        return None

    print(f"\nMeilleure resolution : {best_img.width} x {best_img.height} ({best_label})")

    ts = datetime.now().strftime('%Y-%m-%d-%H-%M-%S')
    transparent = has_transparency(best_img)
    ext = 'png' if transparent else 'jpg'
    if output_path is None:
        output_path = SCRIPT_DIR / f'{ts}_-_Image.{ext}'
    out = Path(output_path)

    if transparent:
        print("Transparence detectee -> sauvegarde en PNG")
        best_img.save(out, 'PNG')
    else:
        best_img.save(out, 'JPEG', quality=95)

    mb = out.stat().st_size / 1024 / 1024
    print(f"\nSauvegarde : {out.resolve()}")
    print(f"Dimensions : {best_img.width} x {best_img.height} pixels")
    print(f"Taille     : {mb:.1f} Mo")
    return str(out.resolve())


# ── Mode image tuilée ─────────────────────────────────────────────────────────

def build_tile_url(base, x, y, z, w, h):
    return f"{base}=x{x}-y{y}-z{z}-w{w}-h{h}-no"


def find_max_zoom(session, base, z_start, w, h):
    print("Recherche du zoom maximum...")
    best = z_start
    for z in range(z_start, z_start + 10):
        if fetch_tile(session, build_tile_url(base, 0, 0, z, w, h)):
            best = z
        else:
            break
    return best


def find_grid(session, base, z, w, h):
    """Nombre de colonnes et de lignes par scan linéaire."""
    cols = 0
    for x in range(500):
        if fetch_tile(session, build_tile_url(base, x, 0, z, w, h)) is None:
            cols = x
            break
    else:
        cols = 500

    rows = 0
    for y in range(500):
        if fetch_tile(session, build_tile_url(base, 0, y, z, w, h)) is None:
            rows = y
            break
    else:
        rows = 500

    return cols, rows


def stitch(tiles, cols, rows, tw, th):
    sample = next(iter(tiles.values()))
    mode = sample.mode  # RGB ou RGBA selon les tuiles
    bg = (255, 255, 255, 0) if mode == 'RGBA' else (255, 255, 255)
    img = Image.new(mode, (cols * tw, rows * th), bg)
    for (x, y), tile in tiles.items():
        if mode == 'RGBA':
            img.paste(tile, (x * tw, y * th), tile)
        else:
            img.paste(tile, (x * tw, y * th))
    return img


def download_tiled_image(session, base, params, output_path, force_zoom, no_max_zoom):
    w = params.get('w', 512)
    h = params.get('h', 512)

    # Zoom
    if force_zoom is not None:
        z = force_zoom
        print(f"Zoom force : z={z}")
    elif no_max_zoom:
        z = params.get('z', 0)
        print(f"Zoom de l'URL : z={z}")
    else:
        z = find_max_zoom(session, base, params.get('z', 0), w, h)
        print(f"  Zoom maximum : z={z}")

    # Grille
    print("Detection des dimensions de la grille...")
    cols, rows = find_grid(session, base, z, w, h)
    print(f"  {cols} colonnes x {rows} lignes = {cols * rows} tuiles")

    if cols == 0 or rows == 0:
        print("Impossible de detecter la grille.")
        return None

    # Telechargement
    total = cols * rows
    tiles = {}
    failed = []
    print(f"\nTelechargement de {total} tuiles...")

    for y in range(rows):
        for x in range(cols):
            url = build_tile_url(base, x, y, z, w, h)
            tile = fetch_tile(session, url)
            n = len(tiles) + len(failed) + 1
            if tile:
                tiles[(x, y)] = tile
                print(f"\r  {n}/{total}  ({x},{y}) OK   ", end='', flush=True)
            else:
                failed.append((x, y))
                print(f"\r  {n}/{total}  ({x},{y}) ECHEC", end='', flush=True)

    print(f"\n  OK : {len(tiles)}   Echecs : {len(failed)}")

    if not tiles:
        print("Aucune tuile telechargee.")
        return None

    sample = next(iter(tiles.values()))
    tw, th = sample.size

    print("\nAssemblage...")
    full = stitch(tiles, cols, rows, tw, th)

    transparent = has_transparency(full)
    ext = 'png' if transparent else 'jpg'
    if output_path is None:
        ts = datetime.now().strftime('%Y-%m-%d-%H-%M-%S')
        output_path = SCRIPT_DIR / f'{ts}_-_Image.{ext}'
    out = Path(output_path)

    if transparent:
        print("Transparence detectee -> sauvegarde en PNG")
        full.save(out, 'PNG')
    else:
        full.save(out, 'JPEG', quality=95)

    mb = out.stat().st_size / 1024 / 1024
    print(f"\nSauvegarde : {out.resolve()}")
    print(f"Dimensions : {full.width} x {full.height} pixels")
    print(f"Taille     : {mb:.1f} Mo")
    return str(out.resolve())


# ── Format Matterport cubemap ────────────────────────────────────────────────
# URL : https://cdn-N.matterport.com/models/[model]/assets/~/tiles/[sweep]/[size]_face[F]_[X]_[Y][suffix].jpg?t=...

# Disposition en croix : (col, row) pour chaque face
#        col0   col1   col2   col3
# row0          face0
# row1   face4  face1  face2  face3
# row2          face5
CUBEMAP_CROSS = {
    0: (1, 0),  # top    (+Y)
    1: (1, 1),  # front  (+Z)
    2: (2, 1),  # right  (+X)
    3: (3, 1),  # back   (-Z)
    4: (0, 1),  # left   (-X)
    5: (1, 2),  # bottom (-Y)
}

# Paramètres de projection pour chaque face (sc, tc, ma_sign, x_sign, y_sign, z_sign)
# Convention OpenGL cubemap → direction (dx,dy,dz) vers UV sur la face
_FACE_PROJ = {
    # face_id: (lambda dx,dy,dz -> (sc, tc, ma))
    0: lambda x, y, z: ( x,  z,  y),   # +Y top
    5: lambda x, y, z: ( x, -z, -y),   # -Y bottom
    1: lambda x, y, z: ( x, -y,  z),   # +Z front
    3: lambda x, y, z: (-x, -y, -z),   # -Z back
    2: lambda x, y, z: (-z, -y,  x),   # +X right
    4: lambda x, y, z: ( z, -y, -x),   # -X left
}


def parse_matterport_url(url):
    """
    Retourne (cdn_base, quality, query) ou None.
    cdn_base = .../tiles/[sweep_id]
    quality  = '4k' | '2k' | '1k' | '512' | etc.
    query    = '?t=...&k=...&imgopt=1'
    """
    m = re.match(
        r'(https://cdn-\d\.matterport\.com/models/[a-f0-9]+/assets/[^/]+/tiles/[a-f0-9]+)'
        r'/([A-Za-z0-9]+)_face\d+_\d+_\d+\.jpg(\?.*)?$',
        url.strip()
    )
    if m:
        return m.group(1), m.group(2), m.group(3) or ''
    return None


# Qualites dans l'ordre décroissant de résolution
_MP_QUALITIES = ['8k', '4k', '2k', '1k', '512', '256']


def build_mp_tile_url(cdn_base, quality, face, x, y, query):
    return f"{cdn_base}/{quality}_face{face}_{x}_{y}.jpg{query}"


def find_best_quality(session, cdn_base, query):
    """Retourne la meilleure qualité disponible et la taille de la grille."""
    for q in _MP_QUALITIES:
        u = build_mp_tile_url(cdn_base, q, 0, 0, 0, query)
        tile = fetch_tile(session, u)
        if tile is None:
            continue
        tile_px = tile.width  # tuiles toujours carrees (512px)

        cols = 0
        for x in range(50):
            if fetch_tile(session, build_mp_tile_url(cdn_base, q, 0, x, 0, query)) is None:
                cols = x; break
        else:
            cols = 50

        rows = 0
        for y in range(50):
            if fetch_tile(session, build_mp_tile_url(cdn_base, q, 0, 0, y, query)) is None:
                rows = y; break
        else:
            rows = 50

        cols = max(cols, 1)
        rows = max(rows, 1)
        print(f"  Qualite {q} : {cols} x {rows} tuiles = {cols*tile_px} x {rows*tile_px} px/face")
        return q, cols, rows, tile_px

    return None, 0, 0, 0


def cubemap_to_equirectangular(face_images):
    """
    Convertit les 6 faces d'un cubemap Matterport en image équirectangulaire.
    face_images : dict {face_id (0-5): PIL.Image}
    Résolution de sortie : face_size*4 × face_size*2
    """
    import numpy as np

    face_size = next(iter(face_images.values())).width
    out_w = face_size * 4
    out_h = face_size * 2

    # Convertir chaque face en tableau numpy
    faces = {
        fid: np.array(img.convert('RGB').resize((face_size, face_size), Image.LANCZOS))
        for fid, img in face_images.items()
    }

    # Angles sphériques pour chaque pixel de sortie
    theta = np.linspace(0, 2 * np.pi, out_w, endpoint=False)   # longitude
    phi   = np.linspace(np.pi / 2, -np.pi / 2, out_h)          # latitude

    THETA, PHI = np.meshgrid(theta, phi)

    # Vecteur direction 3D
    dx = np.cos(PHI) * np.sin(THETA)
    dy = np.sin(PHI)
    dz = np.cos(PHI) * np.cos(THETA)

    abs_x, abs_y, abs_z = np.abs(dx), np.abs(dy), np.abs(dz)

    # Masque par face (face dominante)
    face_masks = {
        0: (abs_y >= abs_x) & (abs_y >= abs_z) & (dy >= 0),   # +Y top
        5: (abs_y >= abs_x) & (abs_y >= abs_z) & (dy <  0),   # -Y bottom
        1: (abs_z >= abs_x) & (abs_z >  abs_y) & (dz >= 0),   # +Z front
        3: (abs_z >= abs_x) & (abs_z >  abs_y) & (dz <  0),   # -Z back
        2: (abs_x >  abs_y) & (abs_x >  abs_z) & (dx >= 0),   # +X right
        4: (abs_x >  abs_y) & (abs_x >  abs_z) & (dx <  0),   # -X left
    }

    output = np.zeros((out_h, out_w, 3), dtype=np.uint8)
    fs1 = face_size - 1

    for fid, mask in face_masks.items():
        if fid not in faces:
            continue
        proj = _FACE_PROJ[fid]
        sc, tc, ma = proj(dx[mask], dy[mask], dz[mask])

        # UV dans [-1, 1] → pixels [0, face_size-1]
        pu = np.clip(((sc / ma + 1) * 0.5 * fs1).astype(np.int32), 0, fs1)
        pv = np.clip(((tc / ma + 1) * 0.5 * fs1).astype(np.int32), 0, fs1)

        oy, ox = np.where(mask)
        output[oy, ox] = faces[fid][pv, pu]

    return Image.fromarray(output, 'RGB')


def download_matterport_cubemap(session, cdn_base, quality_hint, query, output_path):
    print(f"Mode MATTERPORT cubemap detecte")
    print(f"  Base : ...{cdn_base[-50:]}")
    print(f"Recherche de la meilleure qualite disponible...")

    quality, cols, rows, tile_px = find_best_quality(session, cdn_base, query)
    if not quality:
        print("Aucune qualite disponible.")
        return None

    face_w = cols * tile_px
    face_h = rows * tile_px
    total  = 6 * cols * rows
    n = 0

    face_images = {}
    print(f"\nTelechargement des {total} tuiles (6 faces x {cols}x{rows})...")

    for face in range(6):
        tiles = {}
        for y in range(rows):
            for x in range(cols):
                u = build_mp_tile_url(cdn_base, quality, face, x, y, query)
                tile = fetch_tile(session, u)
                n += 1
                if tile:
                    tiles[(x, y)] = tile
                    print(f"\r  {n}/{total}  face{face} ({x},{y}) OK   ", end='', flush=True)
                else:
                    print(f"\r  {n}/{total}  face{face} ({x},{y}) ECHEC", end='', flush=True)

        if tiles:
            face_img = stitch(tiles, cols, rows, tile_px, tile_px)
            face_images[face] = face_img

    print(f"\n  {len(face_images)}/6 faces telechargees")

    if not face_images:
        print("Aucune face telechargee.")
        return None

    # Assemblage en croix (4 x 3 faces)
    print("\nAssemblage en croix...")
    cross_w = face_w * 4
    cross_h = face_h * 3
    cross = Image.new('RGB', (cross_w, cross_h), (0, 0, 0))

    for face, img in face_images.items():
        col, row = CUBEMAP_CROSS[face]
        cross.paste(img, (col * face_w, row * face_h))

    # Conversion cubemap → équirectangulaire
    print(f"Conversion equirectangulaire ({face_w*4} x {face_h*2} px)...")
    full = cubemap_to_equirectangular(face_images)
    print("  Conversion terminee.")

    transparent = has_transparency(full)
    ext = 'png' if transparent else 'jpg'
    if output_path is None:
        ts = datetime.now().strftime('%Y-%m-%d-%H-%M-%S')
        output_path = SCRIPT_DIR / f'{ts}_-_Image.{ext}'
    out = Path(output_path)

    if transparent:
        full.save(out, 'PNG')
    else:
        full.save(out, 'JPEG', quality=95)

    mb = out.stat().st_size / 1024 / 1024
    print(f"\nSauvegarde : {out.resolve()}")
    print(f"Dimensions : {full.width} x {full.height} px  ({face_w}x{face_h} par face)")
    print(f"Taille     : {mb:.1f} Mo")
    return str(out.resolve())


# ── Scraper Google Arts & Culture ────────────────────────────────────────────

def is_arts_culture_url(url):
    return 'artsandculture.google.com' in url


def scrape_arts_culture(page_url, session):
    """
    Charge la page (ou la page du childAssetId si present) et extrait l'URL lh3.
    Retourne une base URL lh3 ou None.
    """
    print("Page Google Arts & Culture detectee.")

    # Si l'URL contient un childAssetId, c'est lui l'image a telecharger
    child_match = re.search(r'[?&]childAssetId=([A-Za-z0-9_\-]+)', page_url)
    if child_match:
        child_id = child_match.group(1)
        fetch_url = f"https://artsandculture.google.com/asset/{child_id}"
        print(f"childAssetId detecte : {child_id}")
        print(f"Chargement de la page de l'image...")
    else:
        fetch_url = page_url
        print(f"Chargement de la page...")

    try:
        r = session.get(fetch_url, timeout=30)
    except Exception as e:
        print(f"Impossible de charger la page : {e}")
        return None

    if r.status_code != 200:
        print(f"Erreur HTTP {r.status_code}")
        return None

    html = r.text

    # Extraire tous les hashes lh3 uniques
    hashes = re.findall(
        r'https://lh3\.googleusercontent\.com/(?:ci/)?([A-Za-z0-9_\-]{20,})',
        html
    )
    unique_hashes = list(dict.fromkeys(hashes))

    if not unique_hashes:
        print("Aucune image trouvee dans la page.")
        return None

    print(f"  {len(unique_hashes)} image(s) trouvee(s) dans la page.")

    # Tester chaque hash et garder celui qui donne la plus grande image
    best_base = None
    best_pixels = 0

    for h in unique_hashes:
        base = f"https://lh3.googleusercontent.com/ci/{h}"
        img, _ = fetch_image(session, base + "=s0")
        if img:
            px = img.width * img.height
            print(f"  ...{h[-30:]}  => {img.width}x{img.height}")
            if px > best_pixels:
                best_pixels = px
                best_base = base
        else:
            base2 = f"https://lh3.googleusercontent.com/{h}"
            img2, _ = fetch_image(session, base2 + "=s0")
            if img2:
                px = img2.width * img2.height
                print(f"  ...{h[-30:]}  => {img2.width}x{img2.height}")
                if px > best_pixels:
                    best_pixels = px
                    best_base = base2

    if best_base:
        print(f"  Image selectionnee.")
    return best_base


# ── Orchestrateur ─────────────────────────────────────────────────────────────

def run(url, output_path=None, force_zoom=None, no_max_zoom=False):
    print("\n=== Telechargeur d'image Google Arts & Culture ===\n")

    # Blob URL
    if is_blob_url(url):
        print("ATTENTION : Vous avez fourni un 'blob URL'.")
        print("Un blob URL est une reference interne du navigateur.")
        print("Il ne peut PAS etre utilise en dehors de votre session.\n")
        print("Comment obtenir l'URL reelle :")
        print("  1. Ouvrez la page sur artsandculture.google.com")
        print("  2. Appuyez sur F12 -> onglet 'Reseau' (Network)")
        print("  3. Faites defiler l'image")
        print("  4. Filtrez par : lh3.googleusercontent.com")
        print("  5. Clic-droit sur une requete -> 'Copier l'URL'")
        return None

    session = make_session()

    # Page Google Arts & Culture -> extraire l'URL lh3 automatiquement
    if is_arts_culture_url(url):
        url = scrape_arts_culture(url, session)
        if url is None:
            return None
        print()

    # Format Matterport cubemap : https://cdn-N.matterport.com/.../tiles/.../[q]_face[F]_[X]_[Y].jpg?t=...
    mp = parse_matterport_url(url)
    if mp:
        cdn_base, quality, query = mp
        return download_matterport_cubemap(session, cdn_base, quality, query, output_path)

    # Format ggpht (panoramas) : https://lh3.ggpht.com/.../xN-yN-zN/ID
    ggpht = parse_ggpht_url(url)
    if ggpht:
        prefix, img_id, x, y, z = ggpht
        return download_ggpht_panorama(session, prefix, img_id, z, output_path, force_zoom, no_max_zoom)

    # Parser l'URL lh3.googleusercontent.com
    base, params = parse_url(url)
    if base is None:
        print(f"Format d'URL non reconnu : {url}")
        print("Formats supportes :")
        print("  https://lh3.googleusercontent.com/[ID]=[params]")
        print("  https://lh3.ggpht.com/[prefix]/xN-yN-zN/[ID]")
        print("  https://artsandculture.google.com/asset/[ID]")
        return None

    print(f"Base URL  : {base}")
    print(f"Parametres: {params}\n")

    # Choisir le mode selon la presence de coordonnees de tuile
    if is_tiled_url(params):
        print("Mode TUILE detecte (coordonnees x/y presentes)")
        return download_tiled_image(session, base, params, output_path, force_zoom, no_max_zoom)
    else:
        return download_single_image(session, base, output_path)


# ── CLI ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description='Telechargeur d\'image Google Arts & Culture (tuilee ou simple)',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Exemples :
  # Image simple (URL avec w/h)
  python telecharger_image_tuiles.py "https://lh3.googleusercontent.com/ci/ABC=w1126-h1200-..."

  # Image tuilée (URL avec x/y/z) - zoom auto
  python telecharger_image_tuiles.py "https://lh3.googleusercontent.com/ABC=x5-y3-z2-w512-h512"

  # Forcer le fichier de sortie
  python telecharger_image_tuiles.py "https://..." -o oeuvre.jpg

  # Forcer un niveau de zoom (mode tuile uniquement)
  python telecharger_image_tuiles.py "https://..." --zoom 4
        """,
    )
    parser.add_argument('url', nargs='?', default=None,
                        help="URL de l'image ou d'une tuile (depuis F12 > Reseau)")
    parser.add_argument('-o', '--output', default=None, help='Fichier de sortie')
    parser.add_argument('--zoom', type=int, default=None,
                        help='Forcer un niveau de zoom (mode tuile uniquement)')
    parser.add_argument('--no-max-zoom', action='store_true',
                        help="Utiliser le zoom de l'URL sans chercher le maximum")

    args = parser.parse_args()

    # Première URL : depuis l'argument ou en la demandant
    premiere_url = args.url

    while True:
        # ── Collecte des URLs ──
        queue = []

        if premiere_url:
            queue.append(premiere_url)
            premiere_url = None
        else:
            print("\n" + "=" * 50)
            print("Entrez les URLs a telecharger (une par ligne).")
            print("Ligne vide = lancer le telechargement. Ctrl+C = quitter.")
            print("=" * 50)
            while True:
                try:
                    url = input(f"  URL {len(queue)+1} : ").strip()
                except EOFError:
                    break
                if not url:
                    break
                queue.append(url)

        if not queue:
            print("Au revoir.")
            break

        # ── Traitement de la file ──
        total = len(queue)
        for i, url in enumerate(queue, 1):
            print(f"\n[{i}/{total}] {url[:80]}{'...' if len(url)>80 else ''}")
            try:
                run(url, args.output, args.zoom, args.no_max_zoom)
            except Exception as e:
                print(f"\nErreur : {e}")
                import traceback
                traceback.print_exc()

        print(f"\n{'-'*50}")
        print(f"Termine : {total} image(s) traitee(s).")


if __name__ == '__main__':
    main()
