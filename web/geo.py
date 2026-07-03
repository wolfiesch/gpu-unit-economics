"""Offline region-to-centroid lookup for price map quotes."""

from __future__ import annotations

RegionCoords = tuple[float, float]

AWS_REGION_COORDS: dict[str, RegionCoords] = {
    "us-east-1": (38.9, -77.4),
    "us-east-2": (40.1, -82.9),
    "us-west-1": (37.4, -121.9),
    "us-west-2": (45.8, -119.7),
    "ca-central-1": (45.5, -73.6),
    "sa-east-1": (-23.5, -46.6),
    "eu-west-1": (53.3, -6.3),
    "eu-west-2": (51.5, -0.1),
    "eu-west-3": (48.9, 2.4),
    "eu-central-1": (50.1, 8.7),
    "eu-north-1": (59.3, 18.1),
    "eu-south-1": (45.5, 9.2),
    "eu-south-2": (41.6, -3.7),
    "ap-south-1": (19.1, 72.9),
    "ap-northeast-1": (35.7, 139.7),
    "ap-northeast-2": (37.6, 127.0),
    "ap-northeast-3": (34.7, 135.5),
    "ap-southeast-1": (1.35, 103.8),
    "ap-southeast-2": (-33.9, 151.2),
    "ap-southeast-3": (-6.2, 106.8),
    "ap-east-1": (22.3, 114.2),
    "me-south-1": (26.0, 50.5),
    "me-central-1": (24.5, 54.4),
    "af-south-1": (-33.9, 18.4),
}

AZURE_REGION_COORDS: dict[str, RegionCoords] = {
    "eastus": (38.9, -77.4),
    "eastus2": (36.7, -78.4),
    "westus": (37.4, -121.9),
    "westus2": (47.2, -119.9),
    "westus3": (33.4, -112.1),
    "centralus": (41.6, -93.6),
    "northcentralus": (41.9, -87.6),
    "southcentralus": (29.4, -98.5),
    "southcentralus2": (29.4, -98.5),
    "southeastus": (33.7, -84.4),
    "westcentralus": (40.9, -110.2),
    "canadacentral": (43.7, -79.4),
    "brazilsouth": (-23.5, -46.6),
    "northeurope": (53.3, -6.3),
    "westeurope": (52.4, 4.9),
    "uksouth": (50.9, -0.1),
    "ukwest": (51.5, -3.2),
    "francecentral": (48.9, 2.4),
    "germanywestcentral": (50.1, 8.7),
    "switzerlandnorth": (47.4, 8.5),
    "swedencentral": (60.7, 17.1),
    "norwayeast": (59.9, 10.8),
    "polandcentral": (52.2, 21.0),
    "italynorth": (45.5, 9.2),
    "spaincentral": (40.4, -3.7),
    "uaenorth": (25.3, 55.4),
    "southafricanorth": (-25.7, 28.2),
    "southafricawest": (-33.9, 18.4),
    "australiaeast": (-33.9, 151.2),
    "australiasoutheast": (-37.8, 144.9),
    "japaneast": (35.7, 139.7),
    "japanwest": (34.7, 135.5),
    "koreacentral": (37.6, 127.0),
    "koreasouth": (35.2, 129.1),
    "southeastasia": (1.35, 103.8),
    "indonesiacentral": (-6.2, 106.8),
    "malaysiawest": (3.1, 101.7),
    "mexicocentral": (19.4, -99.1),
    "usgovvirginia": (38.9, -77.4),
    "usgovarizona": (33.4, -112.1),
}

US_STATE_COORDS: dict[str, RegionCoords] = {
    "alabama": (32.8, -86.8),
    "alaska": (64.2, -152.3),
    "arizona": (34.3, -111.7),
    "arkansas": (35.0, -92.4),
    "california": (36.8, -119.4),
    "colorado": (39.0, -105.5),
    "connecticut": (41.6, -72.7),
    "delaware": (39.0, -75.5),
    "district of columbia": (38.9, -77.0),
    "florida": (27.7, -81.6),
    "georgia": (32.7, -83.2),
    "hawaii": (20.8, -156.3),
    "idaho": (44.2, -114.5),
    "illinois": (40.0, -89.2),
    "indiana": (39.9, -86.3),
    "iowa": (42.0, -93.5),
    "kansas": (38.5, -98.3),
    "kentucky": (37.5, -85.3),
    "louisiana": (31.0, -92.0),
    "maine": (45.3, -69.0),
    "maryland": (39.0, -76.7),
    "massachusetts": (42.3, -71.8),
    "michigan": (44.3, -85.6),
    "minnesota": (46.3, -94.2),
    "mississippi": (32.7, -89.7),
    "missouri": (38.4, -92.5),
    "montana": (46.9, -110.4),
    "nebraska": (41.5, -99.8),
    "nevada": (39.3, -116.6),
    "new hampshire": (43.7, -71.6),
    "new jersey": (40.1, -74.7),
    "new mexico": (34.5, -106.0),
    "new york": (42.9, -75.0),
    "north carolina": (35.5, -79.4),
    "north dakota": (47.5, -100.5),
    "ohio": (40.3, -82.8),
    "oklahoma": (35.6, -97.5),
    "oregon": (43.9, -120.6),
    "pennsylvania": (40.9, -77.8),
    "rhode island": (41.7, -71.6),
    "south carolina": (33.8, -80.9),
    "south dakota": (44.4, -100.2),
    "tennessee": (35.8, -86.4),
    "texas": (31.0, -99.9),
    "utah": (39.3, -111.7),
    "vermont": (44.1, -72.7),
    "virginia": (37.5, -78.8),
    "washington": (47.4, -120.5),
    "west virginia": (38.6, -80.6),
    "wisconsin": (44.6, -89.8),
    "wyoming": (43.0, -107.6),
}

COUNTRY_COORDS: dict[str, RegionCoords] = {
    "czechia": (49.8, 15.5),
    "bulgaria": (42.7, 25.5),
    "france": (46.6, 2.4),
    "india": (21.0, 78.0),
    "ireland": (53.2, -7.7),
    "japan": (36.2, 138.3),
    "malaysia": (4.2, 102.0),
    "saudi arabia": (24.0, 45.0),
    "taiwan": (23.7, 121.0),
    "the netherlands": (52.2, 5.3),
    "netherlands": (52.2, 5.3),
    "germany": (51.1, 10.4),
    "united kingdom": (54.0, -2.5),
    "canada": (56.1, -106.3),
    "sweden": (62.0, 15.0),
    "poland": (51.9, 19.1),
    "spain": (40.4, -3.7),
    "portugal": (39.5, -8.0),
    "norway": (64.5, 11.0),
    "finland": (64.0, 26.0),
    "south korea": (36.5, 127.8),
    "china": (35.0, 103.0),
    "brazil": (-14.2, -51.9),
    "australia": (-25.3, 133.8),
}

ISO_COORDS: dict[str, RegionCoords] = {
    "cz": (49.8, 15.5),
    "bg": (42.7, 25.5),
    "fr": (46.6, 2.4),
    "in": (21.0, 78.0),
    "ie": (53.2, -7.7),
    "jp": (36.2, 138.3),
    "my": (4.2, 102.0),
    "sa": (24.0, 45.0),
    "tw": (23.7, 121.0),
    "nl": (52.2, 5.3),
    "de": (51.1, 10.4),
    "gb": (54.0, -2.5),
    "uk": (54.0, -2.5),
    "ca": (56.1, -106.3),
    "se": (62.0, 15.0),
    "pl": (51.9, 19.1),
    "es": (40.4, -3.7),
    "pt": (39.5, -8.0),
    "no": (64.5, 11.0),
    "fi": (64.0, 26.0),
    "kr": (36.5, 127.8),
    "cn": (35.0, 103.0),
    "br": (-14.2, -51.9),
    "au": (-25.3, 133.8),
    "us": (39.8, -98.6),
}

REGION_COORDS: dict[str, RegionCoords] = {
    **AWS_REGION_COORDS,
    **US_STATE_COORDS,
    **COUNTRY_COORDS,
    **ISO_COORDS,
}


def _normalized(region: str) -> str:
    return region.strip().lower()


def _aws_prefix_coords(region: str) -> RegionCoords | None:
    parts = region.split("-")
    while len(parts) > 3:
        parts = parts[:-2]
        candidate = "-".join(parts)
        if candidate in AWS_REGION_COORDS:
            return AWS_REGION_COORDS[candidate]
    return None


def coords(region: str) -> RegionCoords | None:
    """Return an approximate centroid for Azure, AWS, Vast state, country, or ISO region."""
    normalized = _normalized(region)
    if not normalized:
        return None

    if normalized in AZURE_REGION_COORDS:
        return AZURE_REGION_COORDS[normalized]

    if normalized in REGION_COORDS:
        return REGION_COORDS[normalized]

    aws_coords = _aws_prefix_coords(normalized)
    if aws_coords is not None:
        return aws_coords

    if "," in normalized:
        place, code = (part.strip() for part in normalized.split(",", 1))
        if place:
            place_coords = REGION_COORDS.get(place)
            if place_coords is not None:
                return place_coords
        if code:
            return ISO_COORDS.get(code)

    return None
