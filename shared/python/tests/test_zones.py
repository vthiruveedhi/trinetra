from pathlib import Path

import pytest

from rasaops_shared.zones import load_zones, tables

ROOT = Path(__file__).resolve().parents[3]
SAMPLE = ROOT / "shared" / "schemas" / "examples" / "zones.v1.sample.json"


def test_load_sample_zones():
    doc = load_zones(SAMPLE)
    assert doc.version == "zones.v1"
    assert doc.site_id == "lab-site-001"
    assert len(tables(doc)) == 2
    assert any(z.kind == "entrance" for z in doc.zones)


def test_reject_bad_polygon(tmp_path: Path):
    bad = tmp_path / "bad.json"
    bad.write_text(
        """
        {
          "version": "zones.v1",
          "site_id": "x",
          "zones": [{"zone_id": "t1", "kind": "table", "polygon": [[0,0],[1,1]]}]
        }
        """,
        encoding="utf-8",
    )
    with pytest.raises(Exception):
        load_zones(bad)
