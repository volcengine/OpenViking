"""Regression test: parse_geo_point must accept valid WGS-84 boundary coordinates.

The geographic standard defines a *closed* interval for both axes:
  longitude ∈ [-180, 180]  (includes the antimeridian / International Date Line)
  latitude  ∈ [ -90,  90]  (includes the North and South Poles)

Using strict inequalities (< instead of <=) incorrectly rejects these
legal endpoints, breaking both writes (boundary-coordinate resources cannot
be indexed) and geo_range queries centred on poles or the date line.
"""

import pytest

from openviking.storage.vectordb.utils.data_processor import DataProcessor

_PROCESSOR = DataProcessor({"geo": {"FieldType": "geo_point"}})


@pytest.mark.parametrize(
    "coord, expected",
    [
        ("0,90", (0.0, 90.0)),      # North Pole
        ("0,-90", (0.0, -90.0)),    # South Pole
        ("180,0", (180.0, 0.0)),    # antimeridian east
        ("-180,0", (-180.0, 0.0)),  # antimeridian west
    ],
)
def test_geo_point_accepts_valid_boundary_coordinates(coord, expected):
    """Boundary values are valid WGS-84 coordinates and must not raise."""
    assert _PROCESSOR.parse_geo_point(coord) == expected


@pytest.mark.parametrize(
    "coord",
    ["181,0", "-181,0", "0,91", "0,-91"],
)
def test_geo_point_rejects_out_of_range_coordinates(coord):
    """Values strictly outside the valid range must still raise ValueError."""
    with pytest.raises(ValueError):
        _PROCESSOR.parse_geo_point(coord)
