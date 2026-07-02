import pytest
from web.geo import coords


@pytest.mark.parametrize(
    ("region", "expected"),
    [
        pytest.param("us-east-1", (38.9, -77.4), id="aws-us-east-1"),
        pytest.param("eu-north-1", (59.3, 18.1), id="aws-eu-north-1"),
        pytest.param("ap-south-1", (19.1, 72.9), id="aws-ap-south-1"),
        pytest.param("sa-east-1", (-23.5, -46.6), id="aws-sa-east-1"),
        pytest.param("us-east-1-atl-1", (38.9, -77.4), id="aws-local-zone-east"),
        pytest.param("us-west-2-phx-1", (45.8, -119.7), id="aws-local-zone-west"),
        pytest.param("Virginia, US", (37.5, -78.8), id="vast-virginia-state"),
        pytest.param("Oregon, US", (43.9, -120.6), id="vast-oregon-state"),
        pytest.param("Washington, US", (47.4, -120.5), id="vast-washington-state"),
        pytest.param("Czechia, CZ", (49.8, 15.5), id="country-czechia"),
        pytest.param("India, IN", (21.0, 78.0), id="country-india"),
        pytest.param("The Netherlands, NL", (52.2, 5.3), id="country-netherlands"),
        pytest.param(", US", (39.8, -98.6), id="iso-us-with-empty-place"),
        pytest.param(" virginia, us ", (37.5, -78.8), id="case-whitespace-insensitive"),
    ],
)
def test_coords_resolves_known_regions_and_location_strings(
    region: str,
    expected: tuple[float, float],
) -> None:
    assert coords(region) == expected


@pytest.mark.parametrize(
    "region",
    [
        pytest.param("unknown", id="unknown-name"),
        pytest.param("", id="empty-string"),
        pytest.param("Atlantis, XX", id="unknown-place-and-iso"),
    ],
)
def test_coords_returns_none_for_unknown_or_empty_regions(region: str) -> None:
    assert coords(region) is None
