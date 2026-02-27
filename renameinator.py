#!/usr/bin/env python3
"""
Renameinator — Bulk image renamer for Amazon product listings.

Two modes:
  1. Spreadsheet mode: reads a mapping spreadsheet (XLSX/CSV)
  2. Scan mode: auto-parses a folder of DAM images + a simple ASIN map

Usage:
    # Spreadsheet mode (original workflow)
    python renameinator.py mapping.xlsx
    python renameinator.py mapping.xlsx --dry-run

    # Scan mode (new — auto-parses DAM filenames)
    python renameinator.py --scan ./images/ --asin-map asins.csv --output-dir ./upload
    python renameinator.py --scan ./images/ --asin-map asins.csv --output-dir ./upload --dry-run

    # Scan mode — just generate the spreadsheet for review
    python renameinator.py --scan ./images/ --generate-sheet mapping.xlsx
"""

from __future__ import annotations

import argparse
import os
import re
import shutil
import sys
import time
from pathlib import Path
from typing import Optional

import pandas as pd
import yaml


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def load_config(config_path: Optional[str]) -> dict:
    """Load config from YAML file, falling back to defaults."""
    defaults = {
        "max_images_per_folder": 800,
        "max_folder_size_mb": 500,
        "max_copy_retries": 3,
        "retry_delay_seconds": 0.5,
        "image_extensions": [".jpg", ".jpeg", ".png", ".gif", ".bmp", ".tiff", ".webp"],
    }
    if config_path and os.path.isfile(config_path):
        with open(config_path, "r") as f:
            user_cfg = yaml.safe_load(f) or {}
        defaults.update(user_cfg)
    return defaults


def clean_str(val) -> str:
    """Strip whitespace and surrounding quotes. Returns '' for NaN/None."""
    if pd.isna(val):
        return ""
    return str(val).strip().strip('"').strip("'")


def count_images(directory: str, extensions: tuple) -> int:
    """Recursively count image files in a directory."""
    if not os.path.exists(directory):
        return 0
    count = 0
    for _, _, files in os.walk(directory):
        for f in files:
            if f.lower().endswith(extensions):
                count += 1
    return count


def dir_size_bytes(directory: str) -> int:
    """Total size of files in a directory tree (bytes)."""
    total = 0
    if not os.path.exists(directory):
        return 0
    for dirpath, _, filenames in os.walk(directory):
        for f in filenames:
            fp = os.path.join(dirpath, f)
            if not os.path.islink(fp):
                try:
                    total += os.path.getsize(fp)
                except OSError:
                    pass
    return total


def mb(size_bytes: int) -> str:
    """Format bytes as MB string."""
    return f"{size_bytes / (1024 * 1024):.2f} MB"


# ---------------------------------------------------------------------------
# Filename parsing (DAM scan mode)
# ---------------------------------------------------------------------------

# Default angle-to-suffix mapping. Configurable via config.yaml.
DEFAULT_ANGLE_MAP = {
    # Common views (SanMar + TNF)
    "front": "MAIN",
    "back": "PT01",
    "side": "PT02",
    "detail": "PT03",
    "lifestyle": "PT04",
    "closeup": "PT05",
    "flat": "PT06",
    "swatch": "PT07",
    # TNF-specific views
    "hero": "MAIN",
    "hero2": "PT02",
    "hero3": "PT03",
    "alt1": "PT04",
    "altfront": "PT05",
    "model34": "PT06",
    "modelalt1": "PT07",
    "modelalt4": "PT08",
    "modelback": "PT09",
    "modelhood2": "PT10",
    "modelclose3": "PT11",
    "modelint": "PT12",
}


def parse_dam_filename(filename: str) -> Optional[dict]:
    """
    Parse a DAM image filename into style, color, and angle.

    Supports patterns like:
        YST512_Deep Red_White_Model_Front.png
        ST850_Black_Model_Back.jpg
        YST512_True Navy_White_Model_Side.png

    Pattern: {Style}_{Color}_Model_{Angle}.{ext}
    The style is the leading alphanumeric code (letters+digits).
    Color is everything between the style and '_Model_'.
    Angle is the token after '_Model_'.

    Returns dict with keys: style, color, angle, extension
    or None if the filename doesn't match the expected pattern.
    """
    stem, ext = os.path.splitext(filename)
    if not ext:
        return None

    # Try the _Model_ separator pattern first (SanMar style)
    model_match = re.match(r'^([A-Za-z]+\d+[A-Za-z0-9]*)_(.+)_Model_(\w+)$', stem)
    if model_match:
        return {
            "style": model_match.group(1),
            "color": model_match.group(2),
            "angle": model_match.group(3),
            "extension": ext,
        }

    # TNF pattern: {SKU}-{ViewType}[ (N)].{ext}
    # e.g. NF0A5ABT0VO-HERO.png, NF0A5ABT4EN-BACK.png, NF0A5ABT8K2-MODEL34 (2).png
    # SKU = style base + color code (e.g., NF0A5ABT + 0VO)
    # Strip macOS duplicate suffix like " (2)" before parsing
    clean_stem = re.sub(r'\s*\(\d+\)$', '', stem)
    tnf_match = re.match(r'^(NF[A-Za-z0-9]+)-([A-Za-z0-9]+)$', clean_stem)
    if tnf_match:
        return {
            "style": tnf_match.group(1),  # Full SKU (style+color code combined)
            "color": "",                    # Color is embedded in the SKU
            "angle": tnf_match.group(2),    # HERO, BACK, ALT1, etc.
            "extension": ext,
        }

    # Fallback: try {Style}_{Color}_{Angle} (no "Model" keyword)
    # Angle is assumed to be the last underscore-separated token if it matches known angles
    parts = stem.split("_")
    if len(parts) >= 3:
        last_token = parts[-1].lower()
        if last_token in DEFAULT_ANGLE_MAP or last_token in ("model", "flat", "ghost", "on-model"):
            # Check if first part looks like a style code
            style_match = re.match(r'^[A-Za-z]+\d+[A-Za-z0-9]*$', parts[0])
            if style_match:
                return {
                    "style": parts[0],
                    "color": "_".join(parts[1:-1]),
                    "angle": parts[-1],
                    "extension": ext,
                }

    return None


def scan_image_folder(folder: str, image_extensions: list) -> list:
    """
    Scan a folder for image files and parse their DAM filenames.
    Returns a list of dicts: {path, filename, style, color, angle, extension}
    Unparseable files are included with style/color/angle = None.
    """
    ext_tuple = tuple(image_extensions)
    results = []

    if not os.path.isdir(folder):
        raise FileNotFoundError(f"Scan folder not found: '{folder}'")

    for root, _, files in os.walk(folder):
        for f in sorted(files):
            if not f.lower().endswith(ext_tuple):
                continue
            full_path = os.path.join(root, f)
            parsed = parse_dam_filename(f)
            entry = {
                "path": full_path,
                "filename": f,
                "style": parsed["style"] if parsed else None,
                "color": parsed["color"] if parsed else None,
                "angle": parsed["angle"] if parsed else None,
                "extension": parsed["extension"] if parsed else os.path.splitext(f)[1],
            }
            results.append(entry)

    return results


def load_dam_metadata(metadata_path: str, image_folder: str, image_extensions: list) -> list:
    """
    Load a SanMar-style DAM metadata CSV and match entries to actual image files.

    The metadata CSV has a header row ("Sanmar Metadata Type") then the real headers.
    Key columns: Name, Style Number, Color, View.

    The 'Name' column references original .tif filenames, but downloads may be
    .jpg/.png etc. We match by stem (filename without extension).

    Returns a list of dicts compatible with scan_image_folder output.
    """
    if not os.path.isfile(metadata_path):
        raise FileNotFoundError(f"Metadata file not found: '{metadata_path}'")

    # SanMar metadata has a label row before the real headers
    df = pd.read_csv(metadata_path, skiprows=1)
    df.columns = [str(c).strip() for c in df.columns]

    required = ["Name", "Style Number", "Color", "View"]
    missing = [c for c in required if c not in df.columns]
    if missing:
        raise ValueError(f"Metadata CSV missing columns: {missing}\nFound: {list(df.columns)}")

    # Build a lookup of actual files on disk by stem
    ext_tuple = tuple(image_extensions)
    files_by_stem = {}
    if os.path.isdir(image_folder):
        for root, _, files in os.walk(image_folder):
            for f in files:
                if f.lower().endswith(ext_tuple):
                    stem = os.path.splitext(f)[0]
                    files_by_stem[stem] = os.path.join(root, f)

    results = []
    matched = 0
    unmatched = 0

    for _, row in df.iterrows():
        name = str(row["Name"]).strip() if pd.notna(row["Name"]) else ""
        style = str(row["Style Number"]).strip() if pd.notna(row["Style Number"]) else ""
        color = str(row["Color"]).strip() if pd.notna(row["Color"]) else ""
        view = str(row["View"]).strip() if pd.notna(row["View"]) else ""

        if not name or not style:
            continue

        # Match metadata entry to actual file (metadata says .tif, file might be .jpg)
        meta_stem = os.path.splitext(name)[0]
        actual_path = files_by_stem.get(meta_stem)

        if not actual_path:
            unmatched += 1
            continue

        matched += 1
        actual_ext = os.path.splitext(actual_path)[1]
        results.append({
            "path": actual_path,
            "filename": os.path.basename(actual_path),
            "style": style,
            "color": color,
            "angle": view,
            "extension": actual_ext,
        })

    print(f"  Metadata: {len(df)} entries, {matched} matched to files, {unmatched} unmatched")
    return results


def load_asin_map(path: str) -> pd.DataFrame:
    """
    Load a simple ASIN mapping CSV/XLSX.

    Two formats supported:
      SanMar:  ASIN, COLOR (and optionally STYLE)
      TNF:     ASIN, SKU
    """
    path = path.strip().strip('"').strip("'")
    if not os.path.isfile(path):
        raise FileNotFoundError(f"ASIN map file not found: '{path}'")

    try:
        df = pd.read_excel(path)
    except Exception:
        df = pd.read_csv(path)

    df.columns = [str(c).strip().upper() for c in df.columns]

    if "ASIN" not in df.columns:
        raise ValueError(f"ASIN map must have an ASIN column.\nFound: {list(df.columns)}")

    has_color = "COLOR" in df.columns
    has_sku = "SKU" in df.columns

    if not has_color and not has_sku:
        raise ValueError(
            f"ASIN map must have either a COLOR column (SanMar) or SKU column (TNF).\n"
            f"Found: {list(df.columns)}"
        )

    df["ASIN"] = df["ASIN"].apply(clean_str)
    if has_color:
        df["COLOR"] = df["COLOR"].apply(clean_str)
    if has_sku:
        df["SKU"] = df["SKU"].apply(clean_str)
    if "STYLE" in df.columns:
        df["STYLE"] = df["STYLE"].apply(clean_str)

    return df


def generate_spreadsheet_from_scan(scanned: list, output_path: str, asin_map_df: Optional[pd.DataFrame] = None) -> str:
    """
    Generate a Renameinator-compatible XLSX from scanned images.
    If asin_map_df is provided, pre-fills ASIN assignments.
    Returns the path to the generated file.
    """
    # Collect unique styles and colors from scan
    styles = sorted(set(e["style"] for e in scanned if e["style"]))
    colors = sorted(set(e["color"] for e in scanned if e["color"]))

    rows = []

    # ASIN assignment rows
    if asin_map_df is not None:
        for _, asin_row in asin_map_df.iterrows():
            style = asin_row.get("STYLE", styles[0] if len(styles) == 1 else "")
            rows.append({
                "ASIN": asin_row["ASIN"],
                "ASIN STYLE": style,
                "ASIN COLOR": asin_row["COLOR"],
                "IMG PATH": "",
                "FORMULA": "",
                "IMG #": "",
                "IMG STYLE #": "",
                "IMG COLOR": "",
                "SUFFIX FORMULA": "",
                "OUTPUT FOLDER": "",
            })
    else:
        # Placeholder rows — user fills in ASINs
        for color in colors:
            rows.append({
                "ASIN": "FILL_IN_ASIN",
                "ASIN STYLE": styles[0] if styles else "FILL_IN",
                "ASIN COLOR": color,
                "IMG PATH": "",
                "FORMULA": "",
                "IMG #": "",
                "IMG STYLE #": "",
                "IMG COLOR": "",
                "SUFFIX FORMULA": "",
                "OUTPUT FOLDER": "",
            })

    # Set output folder on first row
    if rows:
        rows[0]["OUTPUT FOLDER"] = os.path.join(os.path.dirname(output_path), "output")

    # Image template rows
    angle_map = DEFAULT_ANGLE_MAP
    img_num = 0
    for entry in scanned:
        if not entry["style"]:
            continue
        img_num += 1
        angle_lower = entry["angle"].lower() if entry["angle"] else ""
        suffix = angle_map.get(angle_lower, f"PT{img_num:02d}")

        rows.append({
            "ASIN": "",
            "ASIN STYLE": "",
            "ASIN COLOR": "",
            "IMG PATH": entry["path"],
            "FORMULA": "",
            "IMG #": str(img_num),
            "IMG STYLE #": entry["style"],
            "IMG COLOR": entry["color"] if entry["color"] else "GENERIC",
            "SUFFIX FORMULA": suffix,
            "OUTPUT FOLDER": "",
        })

    df = pd.DataFrame(rows)
    df.to_excel(output_path, index=False)
    return output_path


def scan_and_rename(scan_folder: str, asin_map_path: str, output_dir: str, cfg: dict,
                    dry_run: bool = False, dam_metadata_path: Optional[str] = None) -> None:
    """
    Full scan-mode pipeline: parse DAM filenames (or metadata CSV), match to ASINs, copy + rename.
    No spreadsheet needed.
    """
    image_ext = cfg.get("image_extensions", [".jpg", ".jpeg", ".png"])
    angle_map = cfg.get("angle_map", DEFAULT_ANGLE_MAP)

    # Use metadata CSV if provided, otherwise fall back to filename parsing
    if dam_metadata_path:
        print(f"\nLoading DAM metadata: {dam_metadata_path}")
        parsed = load_dam_metadata(dam_metadata_path, scan_folder, image_ext)
        unparsed = []
    else:
        print(f"\nScanning folder: {scan_folder}")
        scanned = scan_image_folder(scan_folder, image_ext)
        parsed = [e for e in scanned if e["style"]]
        unparsed = [e for e in scanned if not e["style"]]

    print(f"  Found {len(parsed)} image(s)" + (f", {len(unparsed)} unrecognized" if unparsed else ""))

    if unparsed:
        print(f"  Unrecognized files (skipped):")
        for e in unparsed:
            print(f"    {e['filename']}")

    if not parsed:
        print("ERROR: No parseable images found.")
        sys.exit(1)

    styles = sorted(set(e["style"] for e in parsed))
    colors = sorted(set(e["color"] for e in parsed))
    print(f"  Styles: {', '.join(styles)}")
    print(f"  Colors: {', '.join(colors)}")

    # Load ASIN map
    print(f"\nLoading ASIN map: {asin_map_path}")
    asin_df = load_asin_map(asin_map_path)
    print(f"  {len(asin_df)} ASIN(s) loaded")

    # Detect mapping mode: SKU-based (TNF) or COLOR-based (SanMar)
    sku_mode = "SKU" in asin_df.columns

    # Warn if multiple styles but no STYLE column in ASIN map (SanMar color mode only)
    if not sku_mode and len(styles) > 1 and "STYLE" not in asin_df.columns:
        print(f"\n  WARNING: {len(styles)} styles found but ASIN map has no STYLE column.")
        print(f"  Each ASIN will match ALL styles for its color. Add a STYLE column to fix:")
        print(f"    ASIN,STYLE,COLOR")
        print(f"    B0XXXXX01,{styles[0]},Deep Black")

    # Build the mapping: for each ASIN+color, find matching images
    max_images = cfg["max_images_per_folder"]
    max_size_bytes = cfg["max_folder_size_mb"] * 1024 * 1024
    max_retries = cfg["max_copy_retries"]
    retry_delay = cfg["retry_delay_seconds"]

    successes = []
    failures = []
    folders_created = []
    part = 1
    part_count = 0
    part_size = 0
    active_folder = output_dir

    if not dry_run:
        os.makedirs(active_folder, exist_ok=True)
    folders_created.append(active_folder)

    print(f"\n  Output:  {output_dir}")
    print(f"  Limits:  {max_images} images / {cfg['max_folder_size_mb']} MB per folder")
    if dry_run:
        print(f"  ** DRY RUN — no files will be copied **")
    print()

    for _, asin_row in asin_df.iterrows():
        asin = asin_row["ASIN"]

        if sku_mode:
            # TNF mode: match by SKU (full style code = style+color combined)
            asin_sku = asin_row["SKU"]
            matching = [e for e in parsed if e["style"] == asin_sku]
            match_label = asin_sku
        else:
            # SanMar mode: match by color (and optionally style)
            asin_color = asin_row.get("COLOR", "")
            asin_style = asin_row.get("STYLE", "")
            matching = []
            for entry in parsed:
                color_match = entry["color"].upper() == asin_color.upper()
                style_match = (not asin_style) or (entry["style"] == asin_style)
                if color_match and style_match:
                    matching.append(entry)
            match_label = asin_color

        if not matching:
            failures.append(f"No images found for ASIN {asin} ({match_label})")
            continue

        # Deduplicate: if multiple files map to the same dest name (e.g. HERO and HERO (2)),
        # keep only the first one
        seen_dest_names = set()
        deduped = []
        for entry in matching:
            angle_lower = entry["angle"].lower() if entry["angle"] else ""
            suffix = angle_map.get(angle_lower, "PT01")
            dest_name = f"{asin}.{suffix}{entry['extension']}"
            if dest_name not in seen_dest_names:
                seen_dest_names.add(dest_name)
                deduped.append(entry)

        print(f"  ASIN {asin} ({match_label}): {len(deduped)} image(s)" +
              (f" ({len(matching) - len(deduped)} duplicates skipped)" if len(deduped) < len(matching) else ""))

        for entry in deduped:
            src = entry["path"]
            angle_lower = entry["angle"].lower() if entry["angle"] else ""
            suffix = angle_map.get(angle_lower, "PT01")
            ext = entry["extension"]
            dest_name = f"{asin}.{suffix}{ext}"

            file_size = os.path.getsize(src) if os.path.isfile(src) else 0

            # Folder splitting
            if part_count >= max_images or (part_size + file_size) > max_size_bytes:
                part += 1
                part_count = 0
                part_size = 0
                active_folder = f"{output_dir}_Part{part}"
                if not dry_run:
                    os.makedirs(active_folder, exist_ok=True)
                folders_created.append(active_folder)
                print(f"\n  >> Folder limit reached — new folder: {active_folder}")

            dest_path = os.path.join(active_folder, dest_name)

            if dry_run:
                successes.append(f"{entry['filename']} -> {dest_name}  [{active_folder}]")
                part_count += 1
                part_size += file_size
                continue

            if not os.path.isfile(src):
                failures.append(f"Source not found: {src}")
                continue

            copied = False
            for attempt in range(max_retries):
                try:
                    shutil.copy2(src, dest_path)
                    if os.path.exists(dest_path):
                        copied = True
                        break
                    if attempt < max_retries - 1:
                        time.sleep(retry_delay)
                except Exception as e:
                    if attempt < max_retries - 1:
                        time.sleep(retry_delay)
                    else:
                        failures.append(f"Copy failed: {src} -> {dest_path}: {e}")

            if copied:
                successes.append(f"{entry['filename']} -> {dest_path}")
                part_count += 1
                part_size += file_size
            else:
                failures.append(f"Failed: {src} -> {dest_path}")

    # Summary
    print("\n" + "=" * 60)
    print("  RENAMEINATOR — SCAN MODE SUMMARY")
    print("=" * 60)

    if dry_run:
        print(f"\n  [DRY RUN] Would copy {len(successes)} file(s):")
        for op in successes:
            print(f"    {op}")
    else:
        actual_count = 0
        actual_size = 0
        for folder in folders_created:
            fc = count_images(folder, tuple(image_ext))
            fs = dir_size_bytes(folder)
            actual_count += fc
            actual_size += fs
            print(f"  {folder}: {fc} images, {mb(fs)}")
        print(f"\n  Expected: {len(successes)} | Actual on disk: {actual_count}")
        print(f"  Total size: {mb(actual_size)}")

    if failures:
        print(f"\n  {len(failures)} issue(s):")
        for f in failures:
            print(f"    ! {f}")
    else:
        print("\n  No errors.")

    print("=" * 60)


# ---------------------------------------------------------------------------
# Spreadsheet loading
# ---------------------------------------------------------------------------

def load_spreadsheet(path: str) -> pd.DataFrame:
    """Load an XLSX or CSV file into a DataFrame."""
    path = path.strip().strip('"').strip("'")

    if not os.path.isfile(path):
        raise FileNotFoundError(f"File not found: '{path}'")

    # Try XLSX first
    try:
        df = pd.read_excel(path)
        print(f"  Loaded as XLSX: {path}")
        return df
    except Exception:
        pass

    # Fall back to CSV with common delimiters
    for sep in [",", ";", "\t"]:
        try:
            df = pd.read_csv(path, sep=sep)
            if df.shape[1] > 1:
                print(f"  Loaded as CSV (delimiter='{sep}'): {path}")
                return df
        except Exception:
            continue

    raise ValueError(f"Could not parse file as XLSX or CSV: '{path}'")


# ---------------------------------------------------------------------------
# Core engine
# ---------------------------------------------------------------------------

class Renameinator:
    def __init__(self, df: pd.DataFrame, output_dir: str, cfg: dict, dry_run: bool = False):
        self.df = df
        self.output_dir = output_dir
        self.cfg = cfg
        self.dry_run = dry_run

        self.max_images = cfg["max_images_per_folder"]
        self.max_size_bytes = cfg["max_folder_size_mb"] * 1024 * 1024
        self.max_retries = cfg["max_copy_retries"]
        self.retry_delay = cfg["retry_delay_seconds"]
        self.image_ext = tuple(cfg["image_extensions"])

        self.successes: list[str] = []
        self.failures: list[str] = []
        self.folders_created: list[str] = []

        # Folder-splitting state
        self._part = 1
        self._part_count = 0
        self._part_size = 0
        self._active_folder = self.output_dir

        # Auto-detect color mode
        self.color_mode = "ASIN COLOR" in df.columns and "IMG COLOR" in df.columns

    # ---- validation --------------------------------------------------------

    def validate(self) -> None:
        """Validate required columns exist."""
        base_cols = ["ASIN", "ASIN STYLE", "IMG PATH", "IMG STYLE #", "SUFFIX FORMULA", "OUTPUT FOLDER"]
        color_cols = ["ASIN COLOR", "IMG COLOR"]

        required = base_cols + color_cols if self.color_mode else base_cols
        missing = [c for c in required if c not in self.df.columns]
        if missing:
            raise ValueError(
                f"Missing columns: {missing}\n"
                f"Found: {list(self.df.columns)}\n"
                f"Color mode: {'ON' if self.color_mode else 'OFF'}"
            )

    # ---- data prep ---------------------------------------------------------

    def prepare(self) -> None:
        """Clean column names and cell values."""
        self.df.columns = [str(c).strip() for c in self.df.columns]
        cols_to_clean = ["ASIN", "ASIN STYLE", "IMG PATH", "IMG STYLE #", "SUFFIX FORMULA", "OUTPUT FOLDER"]
        if self.color_mode:
            cols_to_clean += ["ASIN COLOR", "IMG COLOR"]
        for col in cols_to_clean:
            if col in self.df.columns:
                self.df[col] = self.df[col].apply(clean_str)

    # ---- extraction --------------------------------------------------------

    def _get_asin_assignments(self) -> pd.DataFrame:
        mask = (self.df["ASIN"] != "") & (self.df["ASIN STYLE"] != "")
        if self.color_mode:
            mask = mask & (self.df["ASIN COLOR"] != "")
        result = self.df[mask].copy()
        if result.empty:
            raise ValueError("No valid ASIN assignments found. Check ASIN / ASIN STYLE columns.")
        return result

    def _get_image_templates(self) -> pd.DataFrame:
        mask = (self.df["IMG PATH"] != "") & (self.df["IMG STYLE #"] != "") & (self.df["SUFFIX FORMULA"] != "")
        if self.color_mode:
            mask = mask & (self.df["IMG COLOR"] != "")
        result = self.df[mask].copy()
        if result.empty:
            raise ValueError("No valid image templates found. Check IMG PATH / IMG STYLE # / SUFFIX FORMULA columns.")
        return result

    # ---- folder management -------------------------------------------------

    def _init_output(self) -> None:
        if not self.dry_run:
            os.makedirs(self._active_folder, exist_ok=True)
        self.folders_created.append(self._active_folder)

    def _next_folder_if_needed(self, file_size: int) -> None:
        if self._part_count >= self.max_images or (self._part_size + file_size) > self.max_size_bytes:
            self._part += 1
            self._part_count = 0
            self._part_size = 0
            self._active_folder = f"{self.output_dir}_Part{self._part}"
            if not self.dry_run:
                os.makedirs(self._active_folder, exist_ok=True)
            self.folders_created.append(self._active_folder)
            print(f"\n  >> Folder limit reached — new folder: {self._active_folder}")

    # ---- copy with retry ---------------------------------------------------

    def _copy_file(self, src: str, dest: str) -> bool:
        if self.dry_run:
            return True

        for attempt in range(self.max_retries):
            try:
                shutil.copy2(src, dest)
                if os.path.exists(dest):
                    return True
                if attempt < self.max_retries - 1:
                    time.sleep(self.retry_delay)
            except Exception as e:
                if attempt < self.max_retries - 1:
                    time.sleep(self.retry_delay)
                else:
                    self.failures.append(f"Copy failed after {self.max_retries} attempts: {src} -> {dest}: {e}")
        return False

    # ---- should this template apply to this ASIN? -------------------------

    def _should_apply(self, asin_row: pd.Series, template_row: pd.Series) -> bool:
        """In color mode, match by color or GENERIC. Otherwise always True."""
        if not self.color_mode:
            return True
        img_color = template_row["IMG COLOR"].upper()
        asin_color = asin_row["ASIN COLOR"]
        return img_color == "GENERIC" or img_color == asin_color.upper()

    # ---- main run ----------------------------------------------------------

    def run(self) -> None:
        self.validate()
        self.prepare()

        asins_df = self._get_asin_assignments()
        templates_df = self._get_image_templates()
        styles = asins_df["ASIN STYLE"].unique().tolist()

        mode_label = "COLOR" if self.color_mode else "STYLE-ONLY"
        print(f"\n  Mode:    {mode_label}")
        print(f"  Styles:  {len(styles)} unique ({', '.join(styles)})")
        print(f"  ASINs:   {len(asins_df)}")
        print(f"  Images:  {len(templates_df)} template(s)")
        print(f"  Output:  {self.output_dir}")
        print(f"  Limits:  {self.max_images} images / {self.cfg['max_folder_size_mb']} MB per folder")
        if self.dry_run:
            print(f"  ** DRY RUN — no files will be copied **")
        print()

        self._init_output()

        for style in styles:
            print(f"--- Style: {style} ---")

            style_asins = asins_df[asins_df["ASIN STYLE"] == style]
            style_templates = templates_df[templates_df["IMG STYLE #"] == style]

            if style_templates.empty:
                self.failures.append(f"No image templates for style '{style}' — skipped")
                continue

            print(f"    {len(style_asins)} ASIN(s), {len(style_templates)} template(s)")

            for _, asin_row in style_asins.iterrows():
                asin = asin_row["ASIN"]

                for _, tmpl in style_templates.iterrows():
                    if not self._should_apply(asin_row, tmpl):
                        continue

                    src = tmpl["IMG PATH"]
                    suffix = tmpl["SUFFIX FORMULA"]
                    ext = os.path.splitext(src)[1]
                    dest_name = f"{asin}.{suffix}{ext}"

                    if not os.path.isfile(src) and not self.dry_run:
                        self.failures.append(f"Source not found: {src} (ASIN {asin}, style {style})")
                        continue

                    file_size = os.path.getsize(src) if os.path.isfile(src) else 0
                    self._next_folder_if_needed(file_size)

                    dest_path = os.path.join(self._active_folder, dest_name)

                    if self.dry_run:
                        self.successes.append(f"{os.path.basename(src)} -> {dest_name}  [{self._active_folder}]")
                        self._part_count += 1
                        self._part_size += file_size
                        continue

                    if self._copy_file(src, dest_path):
                        self.successes.append(f"{os.path.basename(src)} -> {dest_path}")
                        self._part_count += 1
                        self._part_size += file_size
                    else:
                        self.failures.append(f"Failed: {src} -> {dest_path}")

        self._print_summary()

    # ---- summary -----------------------------------------------------------

    def _print_summary(self) -> None:
        print("\n" + "=" * 60)
        print("  RENAMEINATOR — SUMMARY")
        print("=" * 60)

        if self.dry_run:
            print(f"\n  [DRY RUN] Would copy {len(self.successes)} file(s):")
            for op in self.successes:
                print(f"    {op}")
            if self.failures:
                print(f"\n  {len(self.failures)} issue(s):")
                for f in self.failures:
                    print(f"    ! {f}")
            print(f"\n  Would create {len(self.folders_created)} folder(s):")
            for folder in self.folders_created:
                print(f"    {folder}")
            print("=" * 60)
            return

        # Verify files on disk
        actual_count = 0
        actual_size = 0
        for folder in self.folders_created:
            fc = count_images(folder, self.image_ext)
            fs = dir_size_bytes(folder)
            actual_count += fc
            actual_size += fs
            print(f"  {folder}: {fc} images, {mb(fs)}")

        print(f"\n  Expected: {len(self.successes)} | Actual on disk: {actual_count}")
        print(f"  Total size: {mb(actual_size)}")

        if actual_count != len(self.successes):
            diff = actual_count - len(self.successes)
            print(f"  WARNING: {abs(diff)} {'extra' if diff > 0 else 'missing'} file(s)!")

        if self.failures:
            print(f"\n  {len(self.failures)} error(s):")
            for f in self.failures:
                print(f"    ! {f}")
        else:
            print("\n  No errors.")

        print("=" * 60)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="renameinator",
        description="Bulk rename images for Amazon product listings.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "\n"
            "  Spreadsheet mode (original workflow):\n"
            "    python renameinator.py mapping.xlsx\n"
            "    python renameinator.py mapping.xlsx --dry-run\n"
            "    python renameinator.py mapping.csv --output-dir ./upload --max-images 500\n"
            "\n"
            "  Scan mode (auto-parse DAM images + simple ASIN map):\n"
            "    python renameinator.py --scan ./images/ --asin-map asins.csv --output-dir ./upload\n"
            "    python renameinator.py --scan ./images/ --asin-map asins.csv --output-dir ./upload --dry-run\n"
            "\n"
            "  Generate spreadsheet from scan (for review before running):\n"
            "    python renameinator.py --scan ./images/ --generate-sheet mapping.xlsx\n"
            "    python renameinator.py --scan ./images/ --asin-map asins.csv --generate-sheet mapping.xlsx\n"
        ),
    )

    # Spreadsheet mode
    p.add_argument("spreadsheet", nargs="?", help="Path to the XLSX or CSV mapping file (spreadsheet mode)")

    # Scan mode
    p.add_argument("--scan", metavar="FOLDER", help="Scan a folder of DAM images instead of using a spreadsheet")
    p.add_argument("--asin-map", metavar="FILE", help="CSV/XLSX with ASIN and COLOR columns (used with --scan)")
    p.add_argument("--generate-sheet", metavar="FILE", help="Generate a mapping spreadsheet from scanned images (used with --scan)")
    p.add_argument("--dam-metadata", metavar="FILE", help="SanMar metadata CSV (exported with DAM download) — more reliable than filename parsing")

    # Shared options
    p.add_argument("--output-dir", help="Output folder for renamed images")
    p.add_argument("--max-images", type=int, help="Max images per output folder")
    p.add_argument("--max-size-mb", type=int, help="Max folder size in MB")
    p.add_argument("--dry-run", action="store_true", help="Preview operations without copying files")
    p.add_argument("--config", help="Path to YAML config file (default: config.yaml next to this script)")
    p.add_argument("--verbose", action="store_true", help="Show detailed debug output")
    return p


def main():
    parser = build_parser()
    args = parser.parse_args()

    print("=" * 60)
    print("  RENAMEINATOR v4.0")
    print("=" * 60)

    # Load config
    default_config = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.yaml")
    cfg = load_config(args.config or default_config)

    # CLI overrides
    if args.max_images:
        cfg["max_images_per_folder"] = args.max_images
    if args.max_size_mb:
        cfg["max_folder_size_mb"] = args.max_size_mb

    # --- SCAN MODE ---
    if args.scan:
        image_ext = cfg.get("image_extensions", [".jpg", ".jpeg", ".png"])

        # Generate spreadsheet only
        if args.generate_sheet:
            print(f"\nScanning: {args.scan}")
            scanned = scan_image_folder(args.scan, image_ext)
            parsed = [e for e in scanned if e["style"]]
            print(f"  {len(scanned)} images found, {len(parsed)} parsed")

            asin_df = load_asin_map(args.asin_map) if args.asin_map else None
            out_path = generate_spreadsheet_from_scan(scanned, args.generate_sheet, asin_df)
            print(f"\n  Generated spreadsheet: {out_path}")
            print(f"  Review it, fill in any missing ASINs, then run:")
            print(f"    python renameinator.py {out_path} --output-dir ./upload")
            return

        # Full scan + rename
        if not args.asin_map:
            print("\nERROR: --scan requires --asin-map (CSV with ASIN,COLOR columns)")
            print("  Or use --generate-sheet to create a spreadsheet for review first.")
            sys.exit(1)

        if not args.output_dir:
            print("\nERROR: --scan requires --output-dir")
            sys.exit(1)

        scan_and_rename(args.scan, args.asin_map, args.output_dir, cfg,
                       dry_run=args.dry_run, dam_metadata_path=args.dam_metadata)
        return

    # --- SPREADSHEET MODE ---
    if not args.spreadsheet:
        parser.print_help()
        sys.exit(1)

    # Load spreadsheet
    print(f"\nLoading spreadsheet...")
    df = load_spreadsheet(args.spreadsheet)
    print(f"  {len(df)} rows, {len(df.columns)} columns")
    print(f"  Columns: {', '.join(df.columns)}")

    # Determine output directory
    df.columns = [str(c).strip() for c in df.columns]
    if args.output_dir:
        output_dir = args.output_dir
    elif "OUTPUT FOLDER" in df.columns:
        output_dir = clean_str(df.iloc[0][df.columns[df.columns == "OUTPUT FOLDER"][0]])
    else:
        output_dir = None

    if not output_dir:
        print("\nERROR: No output directory. Use --output-dir or fill the OUTPUT FOLDER column.")
        sys.exit(1)

    print(f"  Output dir: {output_dir}")

    # Run
    engine = Renameinator(df, output_dir, cfg, dry_run=args.dry_run)
    engine.run()


if __name__ == "__main__":
    main()
