"""Sample tenders, so a new installation can be tried before the first real collection run.

They are clearly fake (Tender IDs start with SAMPLE-, portal "Sample data"), are never emailed or labelled by the AI, and one button removes them
again together with any bids started on them.
"""
from __future__ import annotations

from datetime import datetime, timedelta

from .gepnic import Tender

PORTAL = "sample"
# (title, department chain, category, location, value in rupees, days until it closes)
_ROWS = [
    ("Construction of 3.2 km cement concrete road from Ward 4 to Industrial Estate", "Sample City Corporation||Roads Department||Division 2", "Roads & highways", "Sample City", 4.8e7, 9),
    ("Construction of RCC box culvert on Sample river road, chainage 12+400", "Sample PWD||Bridges Circle||Division 1", "Bridges & flyovers", "Sample District", 2.9e7, 14),
    ("Improvement of storm water drain and nala training at Sample Nagar", "Sample City Corporation||Drainage Department", "Drainage & sewerage", "Sample City", 1.35e7, 6),
    ("Repairs and renovation of Zilla Parishad school buildings (12 schools)", "Sample Zilla Parishad||Works Division", "Repairs & maintenance", "Sample District", 7.2e6, 21),
    ("Supply and laying of 300 mm DI pipeline for water supply scheme, Phase II", "Sample Jal Nigam||Water Supply Circle", "Water supply", "Sample Town", 9.6e7, 17),
    ("Construction of staff quarters (G+3), 24 units, with internal roads and compound wall", "Sample Housing Board||Works Division", "Buildings", "Sample City", 1.82e8, 28),
    ("Paver block flooring and kerb stone work at Sample Market area", "Sample Municipal Council||Engineering Department", "Roads & highways", "Sample Town", 3.4e6, 4),
    ("Construction of check dam and canal lining, minor irrigation scheme", "Sample Irrigation Department||Minor Irrigation Division", "Irrigation & canals", "Sample District", 5.1e7, 33),
]


def tenders(now: datetime) -> list[Tender]:
    out = []
    for i, (title, org, category, place, value, days) in enumerate(_ROWS, 1):
        out.append(Tender(portal=PORTAL, tender_id=f"SAMPLE-{i:04d}", title=title, reference=f"SMPL/{i}", description=title, organisation=org, category=category, location=place, value=value,
                          emd=round(value * 0.02, -2), published=now - timedelta(days=2 + i % 3), closing=(now + timedelta(days=days)).replace(hour=15, minute=0, second=0, microsecond=0),
                          opening=(now + timedelta(days=days + 2)).replace(hour=11, minute=0, second=0, microsecond=0)))
    return out
