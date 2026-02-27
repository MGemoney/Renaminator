#!/usr/bin/env python3
"""Integration tests — spreadsheet mode + scan mode with SanMar-style filenames."""
from __future__ import annotations

import os
import shutil
import subprocess
import tempfile

import pandas as pd

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))


def create_test_image(path: str) -> None:
    """Create a tiny placeholder file with the right extension."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        f.write(b"\xff\xd8\xff\xe0" + b"\x00" * 100)


def run_cmd(args: list, label: str) -> subprocess.CompletedProcess:
    print(f"\n===== {label} =====")
    result = subprocess.run(
        ["python3"] + args,
        capture_output=True, text=True, cwd=SCRIPT_DIR,
    )
    print(result.stdout)
    if result.stderr:
        print("STDERR:", result.stderr)
    return result


def check_output(out_dir: str, expected: set) -> bool:
    if not os.path.isdir(out_dir):
        print(f"FAIL: Output dir not created: {out_dir}")
        return False
    actual = set(os.listdir(out_dir))
    print(f"Output files ({len(actual)}):")
    for f in sorted(actual):
        print(f"  {f}")
    if actual == expected:
        print(f"\nPASS: All {len(expected)} expected files present.")
        return True
    else:
        print(f"\nFAIL: Missing={expected - actual}, Extra={actual - expected}")
        return False


# ---------------------------------------------------------------------------
# Test 1: Spreadsheet mode (original workflow)
# ---------------------------------------------------------------------------
def test_spreadsheet_mode():
    print("\n" + "=" * 60)
    print("  TEST 1: SPREADSHEET MODE")
    print("=" * 60)

    base = tempfile.mkdtemp(prefix="renameinator_test_sheet_")
    img_dir = os.path.join(base, "source_images")
    out_dir = os.path.join(base, "output")
    spreadsheet = os.path.join(base, "mapping.xlsx")

    images = {}
    for name in ["style_a_front.jpg", "style_a_back.jpg", "style_a_side.jpg",
                  "style_b_front.jpg", "style_b_back.jpg"]:
        path = os.path.join(img_dir, name)
        create_test_image(path)
        images[name] = path

    rows = [
        {"ASIN": "B0AAAAAA01", "ASIN STYLE": "StyleA", "IMG PATH": "", "FORMULA": "", "IMG #": "", "IMG STYLE #": "", "SUFFIX FORMULA": "", "OUTPUT FOLDER": out_dir},
        {"ASIN": "B0AAAAAA02", "ASIN STYLE": "StyleA", "IMG PATH": "", "FORMULA": "", "IMG #": "", "IMG STYLE #": "", "SUFFIX FORMULA": "", "OUTPUT FOLDER": ""},
        {"ASIN": "B0BBBBBB01", "ASIN STYLE": "StyleB", "IMG PATH": "", "FORMULA": "", "IMG #": "", "IMG STYLE #": "", "SUFFIX FORMULA": "", "OUTPUT FOLDER": ""},
        {"ASIN": "", "ASIN STYLE": "", "IMG PATH": images["style_a_front.jpg"], "FORMULA": "", "IMG #": "1", "IMG STYLE #": "StyleA", "SUFFIX FORMULA": "PT01", "OUTPUT FOLDER": ""},
        {"ASIN": "", "ASIN STYLE": "", "IMG PATH": images["style_a_back.jpg"], "FORMULA": "", "IMG #": "2", "IMG STYLE #": "StyleA", "SUFFIX FORMULA": "PT02", "OUTPUT FOLDER": ""},
        {"ASIN": "", "ASIN STYLE": "", "IMG PATH": images["style_a_side.jpg"], "FORMULA": "", "IMG #": "3", "IMG STYLE #": "StyleA", "SUFFIX FORMULA": "PT03", "OUTPUT FOLDER": ""},
        {"ASIN": "", "ASIN STYLE": "", "IMG PATH": images["style_b_front.jpg"], "FORMULA": "", "IMG #": "1", "IMG STYLE #": "StyleB", "SUFFIX FORMULA": "PT01", "OUTPUT FOLDER": ""},
        {"ASIN": "", "ASIN STYLE": "", "IMG PATH": images["style_b_back.jpg"], "FORMULA": "", "IMG #": "2", "IMG STYLE #": "StyleB", "SUFFIX FORMULA": "PT02", "OUTPUT FOLDER": ""},
    ]
    pd.DataFrame(rows).to_excel(spreadsheet, index=False)

    run_cmd(["renameinator.py", spreadsheet, "--dry-run"], "SPREADSHEET DRY RUN")
    run_cmd(["renameinator.py", spreadsheet], "SPREADSHEET REAL RUN")

    expected = {
        "B0AAAAAA01.PT01.jpg", "B0AAAAAA01.PT02.jpg", "B0AAAAAA01.PT03.jpg",
        "B0AAAAAA02.PT01.jpg", "B0AAAAAA02.PT02.jpg", "B0AAAAAA02.PT03.jpg",
        "B0BBBBBB01.PT01.jpg", "B0BBBBBB01.PT02.jpg",
    }
    passed = check_output(out_dir, expected)
    shutil.rmtree(base)
    return passed


# ---------------------------------------------------------------------------
# Test 2: Scan mode with SanMar-style filenames
# ---------------------------------------------------------------------------
def test_scan_mode():
    print("\n" + "=" * 60)
    print("  TEST 2: SCAN MODE (SanMar filenames)")
    print("=" * 60)

    base = tempfile.mkdtemp(prefix="renameinator_test_scan_")
    img_dir = os.path.join(base, "dam_download")
    out_dir = os.path.join(base, "upload")
    asin_map = os.path.join(base, "asins.csv")

    # Create SanMar-style filenames
    sanmar_files = [
        "YST512_Deep Red_White_Model_Front.png",
        "YST512_Deep Red_White_Model_Back.png",
        "YST512_Deep Red_White_Model_Side.png",
        "YST512_Forest Green_White_Model_Front.png",
        "YST512_Forest Green_White_Model_Back.png",
        "YST512_Forest Green_White_Model_Side.png",
        "YST512_Maroon_White_Model_Front.png",
        "YST512_Maroon_White_Model_Back.png",
        "YST512_Maroon_White_Model_Side.png",
    ]
    for name in sanmar_files:
        create_test_image(os.path.join(img_dir, name))

    # Create simple ASIN map
    asin_data = pd.DataFrame([
        {"ASIN": "B0DR111111", "COLOR": "Deep Red_White"},
        {"ASIN": "B0FG222222", "COLOR": "Forest Green_White"},
        {"ASIN": "B0MA333333", "COLOR": "Maroon_White"},
    ])
    asin_data.to_csv(asin_map, index=False)
    print(f"ASIN map ({asin_map}):")
    print(asin_data.to_string(index=False))

    # Dry run
    run_cmd([
        "renameinator.py", "--scan", img_dir,
        "--asin-map", asin_map, "--output-dir", out_dir, "--dry-run"
    ], "SCAN DRY RUN")

    # Real run
    run_cmd([
        "renameinator.py", "--scan", img_dir,
        "--asin-map", asin_map, "--output-dir", out_dir
    ], "SCAN REAL RUN")

    # 3 ASINs x 3 angles = 9 files (Front=MAIN, Back=PT01, Side=PT02)
    expected = {
        "B0DR111111.MAIN.png", "B0DR111111.PT01.png", "B0DR111111.PT02.png",
        "B0FG222222.MAIN.png", "B0FG222222.PT01.png", "B0FG222222.PT02.png",
        "B0MA333333.MAIN.png", "B0MA333333.PT01.png", "B0MA333333.PT02.png",
    }
    passed = check_output(out_dir, expected)
    shutil.rmtree(base)
    return passed


# ---------------------------------------------------------------------------
# Test 3: Generate spreadsheet from scan
# ---------------------------------------------------------------------------
def test_generate_sheet():
    print("\n" + "=" * 60)
    print("  TEST 3: GENERATE SPREADSHEET FROM SCAN")
    print("=" * 60)

    base = tempfile.mkdtemp(prefix="renameinator_test_gen_")
    img_dir = os.path.join(base, "dam_download")
    sheet_out = os.path.join(base, "generated_mapping.xlsx")
    asin_map = os.path.join(base, "asins.csv")

    for name in ["YST512_Black_White_Model_Front.png", "YST512_Black_White_Model_Back.png"]:
        create_test_image(os.path.join(img_dir, name))

    pd.DataFrame([{"ASIN": "B0TEST0001", "COLOR": "Black_White"}]).to_csv(asin_map, index=False)

    run_cmd([
        "renameinator.py", "--scan", img_dir,
        "--asin-map", asin_map, "--generate-sheet", sheet_out
    ], "GENERATE SHEET")

    if os.path.isfile(sheet_out):
        df = pd.read_excel(sheet_out)
        print(f"\nGenerated spreadsheet ({len(df)} rows):")
        print(df.to_string(index=False))
        print("\nPASS: Spreadsheet generated.")
        passed = True
    else:
        print("FAIL: Spreadsheet not created.")
        passed = False

    shutil.rmtree(base)
    return passed


# ---------------------------------------------------------------------------

if __name__ == "__main__":
    results = {
        "Spreadsheet mode": test_spreadsheet_mode(),
        "Scan mode": test_scan_mode(),
        "Generate sheet": test_generate_sheet(),
    }

    print("\n" + "=" * 60)
    print("  FINAL RESULTS")
    print("=" * 60)
    for name, passed in results.items():
        status = "PASS" if passed else "FAIL"
        print(f"  {status}: {name}")
    print("=" * 60)

    if not all(results.values()):
        sys.exit(1)
