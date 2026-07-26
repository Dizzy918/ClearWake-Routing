"""Ready-made zones for the passages that actually get closed.

Closing the Suez Canal should be one click. Tracing its polygon with a mouse
every time is slow and produces a slightly different shape each go, which
makes routes incomparable between incidents.

Boxes are deliberately generous — they exist to make a passage impassable to
the router, not to survey it.
"""

from __future__ import annotations


def _box(west: float, south: float, east: float, north: float) -> dict:
    return {
        "type": "Polygon",
        "coordinates": [
            [
                [west, south],
                [east, south],
                [east, north],
                [west, north],
                [west, south],
            ]
        ],
    }


ZONE_PRESETS: list[dict] = [
    {
        "id": "suez_canal",
        "name": "Suez Canal",
        "zone_type": "canal",
        "description": "Suez Canal transit closed — traffic must route via the Cape of Good Hope.",
        "geometry": _box(32.2, 29.9, 32.7, 31.3),
    },
    {
        "id": "panama_canal",
        "name": "Panama Canal",
        "zone_type": "canal",
        "description": "Panama Canal transit closed or restricted.",
        "geometry": _box(-80.2, 8.8, -79.5, 9.4),
    },
    {
        "id": "strait_of_hormuz",
        "name": "Strait of Hormuz",
        "zone_type": "conflict",
        "description": "Strait of Hormuz closed to traffic.",
        "geometry": _box(55.8, 25.6, 57.2, 26.8),
    },
    {
        "id": "bab_el_mandeb",
        "name": "Bab-el-Mandeb",
        "zone_type": "conflict",
        "description": "Southern Red Sea approach closed.",
        "geometry": _box(42.8, 12.3, 43.8, 13.2),
    },
    {
        "id": "strait_of_malacca",
        "name": "Strait of Malacca",
        "zone_type": "piracy",
        "description": "Malacca Strait — elevated risk of armed robbery against ships.",
        "geometry": _box(98.5, 1.0, 104.0, 6.0),
    },
    {
        "id": "gulf_of_aden",
        "name": "Gulf of Aden High Risk Area",
        "zone_type": "piracy",
        "description": "Gulf of Aden — internationally recognised high risk area.",
        "geometry": _box(43.0, 10.5, 52.0, 15.5),
    },
    {
        "id": "gulf_of_guinea",
        "name": "Gulf of Guinea High Risk Area",
        "zone_type": "piracy",
        "description": "Gulf of Guinea — piracy and kidnapping risk.",
        "geometry": _box(-2.0, -2.0, 9.0, 6.5),
    },
    {
        "id": "black_sea_nw",
        "name": "North-Western Black Sea",
        "zone_type": "conflict",
        "description": "North-western Black Sea — war risk area.",
        "geometry": _box(30.0, 44.5, 34.0, 46.8),
    },
    {
        "id": "bosphorus",
        "name": "Bosphorus Strait",
        "zone_type": "canal",
        "description": "Bosphorus transit suspended.",
        "geometry": _box(28.9, 40.9, 29.2, 41.3),
    },
    {
        "id": "dover_strait",
        "name": "Dover Strait",
        "zone_type": "temporary",
        "description": "Dover Strait traffic separation scheme closed.",
        "geometry": _box(1.0, 50.6, 2.1, 51.3),
    },
    {
        "id": "gibraltar",
        "name": "Strait of Gibraltar",
        "zone_type": "temporary",
        "description": "Strait of Gibraltar closed to traffic.",
        "geometry": _box(-6.0, 35.8, -5.2, 36.2),
    },
    {
        "id": "great_barrier_reef",
        "name": "Great Barrier Reef Marine Park",
        "zone_type": "eco",
        "description": "Protected reef — commercial transit restricted.",
        "geometry": _box(142.5, -24.5, 154.0, -10.5),
    },
]
