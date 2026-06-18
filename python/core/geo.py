"""
core/geo.py
â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
Coordinate resolution for Vision-I events.

Two capabilities:
  1. Country name normalisation  (existing)
  2. Geocoding â€” resolves any place-name string to (lat, lon)
     with no external API calls, using a built-in lookup table.

geocode(name) is the main entry point.
resolve_event_coords(event) applies a multi-source fallback chain.

Lookup priority in resolve_event_coords:
  1. Event already has lat/lon              â†’ use it
  2. location.name                          â†’ geocode()
  3. First LOC/GPE actor name              â†’ geocode()
  4. extras.sourcecountry (GDELT)          â†’ geocode()
  5. extras.feed_region (RSS)              â†’ geocode() via region alias
  6. location.country                      â†’ geocode()
"""

from __future__ import annotations

import re
from typing import Dict, Optional, Tuple

try:
    import pycountry  # type: ignore
except Exception:
    pycountry = None

_ALIASES: Dict[str, str] = {
    "USA": "United States",
    "US": "United States",
    "U.S.": "United States",
    "UNITED STATES OF AMERICA": "United States",
    "UK": "United Kingdom",
    "U.K.": "United Kingdom",
    "GREAT BRITAIN": "United Kingdom",
    "RUSSIA": "Russian Federation",
    "IRAN": "Iran",
    "NORTH KOREA": "North Korea",
    "SOUTH KOREA": "South Korea",
    "UAE": "United Arab Emirates",
    "VIETNAM": "Vietnam",
    "BOLIVIA": "Bolivia",
    "TANZANIA": "Tanzania",
    "VENEZUELA": "Venezuela",
    "SYRIA": "Syria",
}
# Keys are lowercase.  Covers every country centroid plus ~600 cities, regions,
# conflict zones, chokepoints, and frequently-cited geopolitical locations.
# Coordinates are centroids / approximate focal points, good to ~50 km.

_COORDS: Dict[str, Tuple[float, float]] = {
    "afghanistan": (33.93911, 67.70995),
    "albania": (41.1533, 20.1683),
    "algeria": (28.0339, 1.6596),
    "angola": (-11.2027, 17.8739),
    "argentina": (-38.4161, -63.6167),
    "armenia": (40.0691, 45.0382),
    "australia": (-25.2744, 133.7751),
    "austria": (47.5162, 14.5501),
    "azerbaijan": (40.1431, 47.5769),
    "bahrain": (26.0275, 50.55),
    "bangladesh": (23.685, 90.3563),
    "belarus": (53.7098, 27.9534),
    "belgium": (50.8503, 4.3517),
    "belize": (17.1899, -88.4976),
    "benin": (9.3077, 2.3158),
    "bolivia": (-16.2902, -63.5887),
    "bosnia": (43.9159, 17.6791),
    "brazil": (-14.235, -51.9253),
    "bulgaria": (42.7339, 25.4858),
    "burkina faso": (12.3641, -1.5275),
    "burma": (21.9162, 95.956),
    "myanmar": (21.9162, 95.956),
    "cambodia": (12.5657, 104.991),
    "cameroon": (7.3697, 12.3547),
    "canada": (56.1304, -106.3468),
    "central african republic": (6.6111, 20.9394),
    "chad": (15.4542, 18.7322),
    "chile": (-35.6751, -71.543),
    "china": (35.8617, 104.1954),
    "colombia": (4.5709, -74.2973),
    "congo": (-0.228, 15.8277),
    "democratic republic of congo": (-4.0383, 21.7587),
    "drc": (-4.0383, 21.7587),
    "costa rica": (9.7489, -83.7534),
    "croatia": (45.1, 15.2),
    "cuba": (21.5218, -77.7812),
    "czech republic": (49.8175, 15.473),
    "czechia": (49.8175, 15.473),
    "denmark": (56.2639, 9.5018),
    "dominican republic": (18.7357, -70.1627),
    "ecuador": (-1.8312, -78.1834),
    "egypt": (26.8206, 30.8025),
    "el salvador": (13.7942, -88.8965),
    "eritrea": (15.1794, 39.7823),
    "estonia": (58.5953, 25.0136),
    "ethiopia": (9.145, 40.4897),
    "finland": (61.9241, 25.7482),
    "france": (46.2276, 2.2137),
    "gabon": (-0.8037, 11.6094),
    "georgia": (42.3154, 43.3569),
    "germany": (51.1657, 10.4515),
    "ghana": (7.9465, -1.0232),
    "greece": (39.0742, 21.8243),
    "guatemala": (15.7835, -90.2308),
    "guinea": (9.9456, -11.0),
    "haiti": (18.9712, -72.2852),
    "honduras": (15.1999, -86.2419),
    "hungary": (47.1625, 19.5033),
    "india": (20.5937, 78.9629),
    "indonesia": (-0.7893, 113.9213),
    "iran": (32.4279, 53.688),
    "iraq": (33.2232, 43.6793),
    "ireland": (53.4129, -8.2439),
    "israel": (31.0461, 34.8516),
    "italy": (41.8719, 12.5674),
    "ivory coast": (7.54, -5.5471),
    "japan": (36.2048, 138.2529),
    "jordan": (30.5852, 36.2384),
    "kazakhstan": (48.0196, 66.9237),
    "kenya": (-0.0236, 37.9062),
    "north korea": (40.3399, 127.5101),
    "south korea": (35.9078, 127.7669),
    "kosovo": (42.6026, 20.903),
    "kuwait": (29.3117, 47.4818),
    "kyrgyzstan": (41.2044, 74.7661),
    "laos": (19.8563, 102.4955),
    "latvia": (56.8796, 24.6032),
    "lebanon": (33.8547, 35.8623),
    "libya": (26.3351, 17.2283),
    "lithuania": (55.1694, 23.8813),
    "mali": (17.5707, -3.9962),
    "mauritania": (21.0079, -10.9408),
    "mexico": (23.6345, -102.5528),
    "moldova": (47.4116, 28.3699),
    "mongolia": (46.8625, 103.8467),
    "morocco": (31.7917, -7.0926),
    "mozambique": (-18.6657, 35.5296),
    "namibia": (-22.9576, 18.4904),
    "nepal": (28.3949, 84.124),
    "netherlands": (52.1326, 5.2913),
    "nicaragua": (12.8654, -85.2072),
    "niger": (17.6078, 8.0817),
    "nigeria": (9.082, 8.6753),
    "norway": (60.472, 8.4689),
    "oman": (21.4735, 55.9754),
    "pakistan": (30.3753, 69.3451),
    "palestine": (31.9466, 35.3027),
    "panama": (8.538, -80.7821),
    "papua new guinea": (-6.315, 143.9555),
    "paraguay": (-23.4425, -58.4438),
    "peru": (-9.19, -75.0152),
    "philippines": (12.8797, 121.774),
    "poland": (51.9194, 19.1451),
    "portugal": (39.3999, -8.2245),
    "qatar": (25.3548, 51.1839),
    "romania": (45.9432, 24.9668),
    "russia": (61.524, 105.3188),
    "russian federation": (61.524, 105.3188),
    "rwanda": (-1.9403, 29.8739),
    "saudi arabia": (23.8859, 45.0792),
    "senegal": (14.4974, -14.4524),
    "serbia": (44.0165, 21.0059),
    "sierra leone": (8.4606, -11.7799),
    "somalia": (5.1521, 46.1996),
    "south africa": (-30.5595, 22.9375),
    "south sudan": (6.877, 31.307),
    "spain": (40.4637, -3.7492),
    "sri lanka": (7.8731, 80.7718),
    "sudan": (12.8628, 30.2176),
    "sweden": (60.1282, 18.6435),
    "switzerland": (46.8182, 8.2275),
    "syria": (34.8021, 38.9968),
    "taiwan": (23.6978, 120.9605),
    "tajikistan": (38.861, 71.2761),
    "tanzania": (-6.369, 34.8888),
    "thailand": (15.87, 100.9925),
    "tunisia": (33.8869, 9.5375),
    "turkey": (38.9637, 35.2433),
    "turkmenistan": (38.9697, 59.5563),
    "uganda": (1.3733, 32.2903),
    "ukraine": (48.3794, 31.1656),
    "united arab emirates": (23.4241, 53.8478),
    "uae": (23.4241, 53.8478),
    "united kingdom": (55.3781, -3.436),
    "uk": (55.3781, -3.436),
    "united states": (37.0902, -95.7129),
    "usa": (37.0902, -95.7129),
    "uruguay": (-32.5228, -55.7658),
    "uzbekistan": (41.3775, 64.5853),
    "venezuela": (6.4238, -66.5897),
    "vietnam": (14.0583, 108.2772),
    "west bank": (31.9466, 35.2433),
    "western sahara": (24.2155, -12.886),
    "yemen": (15.5527, 48.5164),
    "zambia": (-13.1339, 27.8493),
    "zimbabwe": (-19.0154, 29.1549),
    "kabul": (34.5260, 69.1762),
    "tirana": (41.3275, 19.8187),
    "algiers": (36.7538, 3.0588),
    "luanda": (-8.8368, 13.2343),
    "buenos aires": (-34.6037, -58.3816),
    "yerevan": (40.1872, 44.515),
    "canberra": (-35.2809, 149.13),
    "sydney": (-33.8688, 151.2093),
    "vienna": (48.2082, 16.3738),
    "baku": (40.4093, 49.8671),
    "manama": (26.225, 50.586),
    "dhaka": (23.8103, 90.4125),
    "minsk": (53.9045, 27.5615),
    "brussels": (50.8503, 4.3517),
    "porto-novo": (6.3676, 2.4252),
    "sucre": (-16.5, -68.15),
    "sarajevo": (43.8476, 18.3564),
    "brasilia": (-15.7801, -47.9292),
    "sao paulo": (-23.5505, -46.6333),
    "rio de janeiro": (-22.9068, -43.1729),
    "sofia": (42.6977, 23.3219),
    "ouagadougou": (12.3647, -1.5332),
    "naypyidaw": (19.7633, 96.0785),
    "rangoon": (16.8661, 96.1951),
    "yangon": (16.8661, 96.1951),
    "phnom penh": (11.5564, 104.9282),
    "yaounde": (3.848, 11.5021),
    "ottawa": (45.4215, -75.6972),
    "toronto": (43.6532, -79.3832),
    "montreal": (45.5017, -73.5673),
    "bangui": (4.3612, 18.5552),
    "ndjamena": (12.1048, 15.0445),
    "santiago": (-33.4489, -70.6693),
    "beijing": (39.9042, 116.4074),
    "shanghai": (31.2304, 121.4737),
    "hong kong": (22.3193, 114.1694),
    "bogota": (4.711, -74.0721),
    "brazzaville": (-4.2634, 15.2429),
    "kinshasa": (-4.3217, 15.3125),
    "san jose": (9.9281, -84.0907),
    "zagreb": (45.8131, 16.0),
    "havana": (23.1136, -82.3666),
    "prague": (50.0755, 14.4378),
    "copenhagen": (55.6761, 12.5683),
    "santo domingo": (18.4861, -69.9312),
    "quito": (-0.1807, -78.4678),
    "cairo": (30.0444, 31.2357),
    "san salvador": (13.6929, -89.2182),
    "asmara": (15.3229, 38.9251),
    "tallinn": (59.437, 24.7536),
    "addis ababa": (9.145, 38.7452),
    "helsinki": (60.1699, 24.9384),
    "paris": (48.8566, 2.3522),
    "libreville": (0.3901, 9.4542),
    "tbilisi": (41.6938, 44.8015),
    "berlin": (52.52, 13.405),
    "accra": (5.6037, -0.187),
    "athens": (37.9838, 23.7275),
    "guatemala city": (14.6349, -90.5069),
    "conakry": (9.5370, -13.6773),
    "port-au-prince": (18.5392, -72.3288),
    "tegucigalpa": (14.072, -87.2069),
    "budapest": (47.4979, 19.0402),
    "new delhi": (28.6139, 77.209),
    "mumbai": (19.076, 72.8777),
    "delhi": (28.7041, 77.1025),
    "jakarta": (-6.2088, 106.8456),
    "tehran": (35.6892, 51.389),
    "baghdad": (33.3152, 44.3661),
    "dublin": (53.3498, -6.2603),
    "jerusalem": (31.7683, 35.2137),
    "tel aviv": (32.0853, 34.7818),
    "rome": (41.9028, 12.4964),
    "milan": (45.4654, 9.1859),
    "naples": (40.8522, 14.2681),
    "tokyo": (35.6762, 139.6503),
    "osaka": (34.6937, 135.5023),
    "amman": (31.9566, 35.9457),
    "astana": (51.1605, 71.4704),
    "nairobi": (-1.2921, 36.8219),
    "pyongyang": (39.0392, 125.7625),
    "seoul": (37.5665, 126.978),
    "pristina": (42.6629, 21.1655),
    "kuwait city": (29.3797, 47.9744),
    "bishkek": (42.8746, 74.612),
    "vientiane": (17.9757, 102.6331),
    "riga": (56.9496, 24.1052),
    "beirut": (33.8889, 35.4944),
    "tripoli": (32.9021, 13.1822),
    "vilnius": (54.6872, 25.2797),
    "bamako": (12.6392, -8.0029),
    "nouakchott": (18.0735, -15.9582),
    "mexico city": (19.4326, -99.1332),
    "chisinau": (47.0105, 28.8638),
    "ulaanbaatar": (47.8864, 106.9057),
    "rabat": (34.0209, -6.8416),
    "casablanca": (33.5731, -7.5898),
    "maputo": (-25.9655, 32.5832),
    "windhoek": (-22.5597, 17.0832),
    "kathmandu": (27.7172, 85.324),
    "amsterdam": (52.3676, 4.9041),
    "managua": (12.1364, -86.2966),
    "niamey": (13.5137, 2.1098),
    "abuja": (9.0579, 7.4951),
    "lagos": (6.5244, 3.3792),
    "oslo": (59.9139, 10.7522),
    "muscat": (23.5859, 58.4059),
    "islamabad": (33.7215, 73.0433),
    "karachi": (24.8607, 67.0011),
    "lahore": (31.5204, 74.3587),
    "ramallah": (31.9038, 35.2034),
    "gaza": (31.5017, 34.4668),
    "gaza city": (31.5017, 34.4668),
    "panama city": (8.994, -79.5199),
    "port moresby": (-9.4438, 147.1803),
    "asuncion": (-25.2637, -57.5759),
    "lima": (-12.046, -77.0428),
    "manila": (14.5995, 120.9842),
    "warsaw": (52.2297, 21.0122),
    "lisbon": (38.7169, -9.1395),
    "doha": (25.2854, 51.531),
    "bucharest": (44.4268, 26.1025),
    "moscow": (55.7558, 37.6173),
    "st. petersburg": (59.9311, 30.3609),
    "saint petersburg": (59.9311, 30.3609),
    "kigali": (-1.9706, 30.1044),
    "riyadh": (24.6877, 46.7219),
    "jeddah": (21.4858, 39.1925),
    "dakar": (14.7167, -17.4677),
    "belgrade": (44.8176, 20.4569),
    "freetown": (8.4897, -13.2344),
    "mogadishu": (2.0469, 45.3182),
    "cape town": (-33.9249, 18.4241),
    "johannesburg": (-26.2041, 28.0473),
    "pretoria": (-25.7479, 28.2293),
    "juba": (4.85, 31.6),
    "madrid": (40.4168, -3.7038),
    "barcelona": (41.3851, 2.1734),
    "colombo": (6.9271, 79.8612),
    "khartoum": (15.5007, 32.5599),
    "stockholm": (59.3293, 18.0686),
    "bern": (46.948, 7.4474),
    "geneva": (46.2044, 6.1432),
    "zurich": (47.3769, 8.5417),
    "damascus": (33.5138, 36.2765),
    "aleppo": (36.2021, 37.1343),
    "taipei": (25.0329, 121.5654),
    "dushanbe": (38.5598, 68.7738),
    "dar es salaam": (-6.7924, 39.2083),
    "bangkok": (13.7563, 100.5018),
    "tunis": (36.8065, 10.1815),
    "ankara": (39.9334, 32.8597),
    "istanbul": (41.0082, 28.9784),
    "ashgabat": (37.9601, 58.3261),
    "kampala": (0.3476, 32.5825),
    "kyiv": (50.4501, 30.5234),
    "kiev": (50.4501, 30.5234),
    "abu dhabi": (24.4539, 54.3773),
    "dubai": (25.2048, 55.2708),
    "london": (51.5074, -0.1278),
    "washington": (38.9072, -77.0369),
    "washington dc": (38.9072, -77.0369),
    "new york": (40.7128, -74.006),
    "los angeles": (34.0522, -118.2437),
    "chicago": (41.8781, -87.6298),
    "houston": (29.7604, -95.3698),
    "montevideo": (-34.9011, -56.1645),
    "tashkent": (41.2995, 69.2401),
    "caracas": (10.4806, -66.9036),
    "hanoi": (21.0285, 105.8542),
    "ho chi minh city": (10.8231, 106.6297),
    "saigon": (10.8231, 106.6297),
    "harare": (-17.8252, 31.0335),
    "lusaka": (-15.4067, 28.2871),
    "donbas": (48.15, 37.8),
    "donbass": (48.15, 37.8),
    "donetsk": (48.0159, 37.8028),
    "luhansk": (48.574, 39.3078),
    "mariupol": (47.0968, 37.5505),
    "crimea": (45.3354, 33.9),
    "kherson": (46.6354, 32.6169),
    "zaporizhzhia": (47.8388, 35.1396),
    "kharkiv": (49.9935, 36.2304),
    "bakhmut": (48.5959, 37.9975),
    "avdiivka": (48.1386, 37.7516),
    "west bank": (31.9466, 35.2433),
    "gaza strip": (31.4175, 34.3324),
    "rafah": (31.2817, 34.2524),
    "khan younis": (31.3452, 34.3066),
    "jenin": (32.4639, 35.2956),
    "nablus": (32.2211, 35.2544),
    "raqqa": (35.9515, 39.0042),
    "idlib": (35.9309, 36.633),
    "deir ez-zor": (35.3354, 40.1415),
    "mosul": (36.3378, 43.1189),
    "fallujah": (33.3455, 43.7727),
    "tikrit": (34.5985, 43.6757),
    "erbil": (36.1901, 44.009),
    "kandahar": (31.6129, 65.7372),
    "helmand": (29.5574, 64.3505),
    "jalalabad": (34.4415, 70.4372),
    "sahel": (14.0, 2.0),
    "lake chad basin": (13.0, 14.0),
    "horn of africa": (7.0, 46.0),
    "great lakes region": (-2.0, 29.0),
    "south china sea": (12.0, 114.0),
    "east china sea": (30.0, 126.0),
    "yellow sea": (35.0, 123.0),
    "strait of hormuz": (26.5, 56.5),
    "hormuz": (26.5, 56.5),
    "strait of malacca": (2.5, 103.5),
    "bab al-mandab": (12.6, 43.4),
    "suez canal": (30.5852, 32.2654),
    "bosporus": (41.1, 29.05),
    "taiwan strait": (24.5, 119.5),
    "korean peninsula": (36.5, 128.0),
    "kashmir": (34.0, 76.0),
    "xinjiang": (42.1284, 87.0),
    "tibet": (30.0, 88.0),
    "chechnya": (43.5, 45.7),
    "nagorno-karabakh": (39.8, 46.8),
    "karabakh": (39.8, 46.8),
    "transnistria": (46.8, 29.6),
    "abkhazia": (43.0, 41.0),
    "south ossetia": (42.35, 43.97),
    "balochistan": (28.4907, 65.095),
    "waziristan": (32.3, 69.8),
    "tigray": (14.0, 38.5),
    "amhara": (11.7, 37.85),
    "oromia": (7.55, 38.54),
    "darfur": (13.5, 24.0),
    "north kivu": (-1.0, 29.0),
    "south kivu": (-3.0, 28.0),
    "ituri": (1.5, 30.0),
    "cabo delgado": (-12.0, 40.0),
    "kachin": (25.5, 97.5),
    "rakhine": (20.1, 93.5),
    "arakan": (20.1, 93.5),
    "shan state": (22.0, 98.0),
    "fertile crescent": (33.0, 44.0),
    "levant": (33.0, 36.0),
    "maghreb": (30.0, 3.0),
    "subsaharan africa": (5.0, 20.0),
    "eastern europe": (52.0, 30.0),
    "central asia": (45.0, 65.0),
    "middle east": (29.0, 41.0),
    "persian gulf": (26.5, 51.5),
    "arabian sea": (15.0, 65.0),
    "red sea": (20.0, 38.5),
    "black sea": (43.0, 34.0),
    "mediterranean": (35.0, 18.0),
    "mediterranean sea": (35.0, 18.0),
    "caspian sea": (42.0, 51.0),
    "arctic": (85.0, 0.0),
    "antarctic": (-85.0, 0.0),
    "united nations": (40.7489, -73.968),
    "nato": (50.8792, 4.4271),
    "european union": (50.8503, 4.3517),
    "eu": (50.8503, 4.3517),
    "iaea": (48.2082, 16.3738),
    "imf": (38.8985, -77.0444),
    "world bank": (38.8985, -77.0444),
    "pentagon": (38.8719, -77.0563),
    "kremlin": (55.7516, 37.6178),
    "white house": (38.8977, -77.0365),
    "global": (20.0, 0.0),
    "mena": (27.0, 30.0),
    "asia": (34.0, 100.0),
    "europe": (54.0, 15.0),
    "latam": (-15.0, -60.0),
    "africa": (5.0, 20.0),
    "americas": (15.0, -90.0),
    "rs": (44.8176, 20.4569),   # Serbia
    "ir": (32.4279, 53.688),    # Iran
    "af": (33.93911, 67.70995), # Afghanistan
    "iq": (33.2232, 43.6793),   # Iraq
    "sy": (34.8021, 38.9968),   # Syria
    "ye": (15.5527, 48.5164),   # Yemen
    "ua": (48.3794, 31.1656),   # Ukraine
    "ru": (61.524, 105.3188),   # Russia
    "cn": (35.8617, 104.1954),  # China
    "us": (37.0902, -95.7129),  # United States
    "gb": (55.3781, -3.436),    # United Kingdom
    "de": (51.1657, 10.4515),   # Germany
    "fr": (46.2276, 2.2137),    # France
    "in": (20.5937, 78.9629),   # India
    "pk": (30.3753, 69.3451),   # Pakistan
    "il": (31.0461, 34.8516),   # Israel
    "sa": (23.8859, 45.0792),   # Saudi Arabia
    "tr": (38.9637, 35.2433),   # Turkey
    "eg": (26.8206, 30.8025),   # Egypt
    "lb": (33.8547, 35.8623),   # Lebanon
    "kp": (40.3399, 127.5101),  # North Korea
    "kr": (35.9078, 127.7669),  # South Korea
    "jp": (36.2048, 138.2529),  # Japan
}

def _normalise(text: str) -> str:
    """Lowercase, collapse whitespace, strip punctuation for lookup."""
    s = text.lower().strip()
    s = re.sub(r"['''\"\.,;:!?\(\)\[\]]", "", s)
    s = re.sub(r"\s+", " ", s)
    return s


def geocode(name: Optional[str]) -> Optional[Tuple[float, float]]:
    """
    Resolve a place-name string to (lat, lon).

    Tries:
      1. Direct lookup (normalised)
      2. Strip common prefixes ("the ", "republic of ", etc.)
      3. Last segment after comma (e.g. "Kyiv, Ukraine" â†’ "Ukraine")
      4. First segment before comma (e.g. "Kyiv, Ukraine" â†’ "Kyiv")
      5. pycountry country search
    """
    if not name:
        return None

    key = _normalise(name)
    if not key:
        return None

    # Direct hit
    if key in _COORDS:
        return _COORDS[key]

    # Strip common prefixes
    for prefix in ("the ", "republic of ", "state of ", "kingdom of ",
                   "democratic republic of the ", "islamic republic of "):
        if key.startswith(prefix):
            stripped = key[len(prefix):]
            if stripped in _COORDS:
                return _COORDS[stripped]

    # Comma-separated: try last segment first (country), then first (city)
    if "," in key:
        parts = [p.strip() for p in key.split(",")]
        for part in reversed(parts):
            if part in _COORDS:
                return _COORDS[part]
        for part in parts:
            if part in _COORDS:
                return _COORDS[part]

    # pycountry fallback
    if pycountry:
        try:
            hit = pycountry.countries.lookup(name)
            if hit:
                country_key = _normalise(hit.name)
                if country_key in _COORDS:
                    return _COORDS[country_key]
        except LookupError:
            pass

    # Partial prefix match (e.g. "Northern Ireland" â†’ check if any key startswith)
    for k, v in _COORDS.items():
        if len(k) >= 4 and (key.startswith(k) or k.startswith(key)):
            return v

    return None


def resolve_event_coords(event: dict) -> Optional[Tuple[float, float]]:
    """
    Multi-source fallback to get (lat, lon) for any event.

    Priority:
      1. Event already has lat + lon â†’ pass through
      2. location.name              â†’ geocode()
      3. First LOC/GPE actor        â†’ geocode()
      4. extras.sourcecountry       â†’ geocode()
      5. extras.feed_region         â†’ geocode() via RSS region alias
      6. location.country           â†’ geocode()
    """
    loc    = event.get("location") or {}
    extras = event.get("extras")   or {}

    # 1. Already geocoded
    if loc.get("lat") is not None and loc.get("lon") is not None:
        return (float(loc["lat"]), float(loc["lon"]))

    # 2. location.name
    coords = geocode(loc.get("name"))
    if coords:
        return coords

    # 3. First LOC / GPE actor
    for actor in event.get("actors") or []:
        atype = (actor.get("type") or "").upper()
        if atype in ("LOC", "GPE"):
            coords = geocode(actor.get("name"))
            if coords:
                return coords

    # 4. GDELT sourcecountry
    coords = geocode(extras.get("sourcecountry"))
    if coords:
        return coords

    # 5. RSS feed_region
    coords = geocode(extras.get("feed_region"))
    if coords:
        return coords

    # 6. location.country
    coords = geocode(loc.get("country"))
    if coords:
        return coords

    # 7. Domain-based heuristic: e.g. .ru â†’ Russia
    url = event.get("url") or ""
    m = re.search(r"\.([a-z]{2})(?:/|$)", url.lower())
    if m:
        coords = geocode(m.group(1))
        if coords:
            return coords

    return None


def apply_geocoding(event: dict) -> dict:
    """
    Mutate event in-place: set location.lat/lon if not already set.
    Returns the event (for chaining).
    """
    loc = event.get("location") or {}
    if loc.get("lat") is not None:
        return event  # already has coordinates

    coords = resolve_event_coords(event)
    if coords:
        lat, lon = coords
        event["location"] = {**loc, "lat": lat, "lon": lon}

    return event

def _normalize_label(value: str) -> str:
    return re.sub(r"\s+", " ", value or "").strip()


def normalize_country(value: Optional[str], allow_fallback: bool = True) -> Optional[str]:
    if not value:
        return None
    raw = _normalize_label(value)
    if not raw:
        return None
    alias = _ALIASES.get(raw.upper())
    if alias:
        raw = alias
    if pycountry:
        try:
            if len(raw) in (2, 3) and raw.isalpha():
                hit = (pycountry.countries.get(alpha_2=raw.upper())
                       or pycountry.countries.get(alpha_3=raw.upper()))
                if hit:
                    return hit.name
            hit = pycountry.countries.lookup(raw)
            if hit:
                return hit.name
        except LookupError:
            pass
    if not allow_fallback:
        return None
    if raw.isupper() and len(raw) <= 3:
        return raw
    return raw.title()


def _extract_country_from_location(name: Optional[str]) -> Optional[str]:
    if not name:
        return None
    raw = _normalize_label(name)
    parts = [p.strip() for p in re.split(r"[,\u2013\u2014-]", raw) if p.strip()]
    if parts:
        candidate = parts[-1]
        return normalize_country(candidate, allow_fallback=False)
    return normalize_country(raw, allow_fallback=False)


def resolve_event_country(event: dict) -> Optional[str]:
    """
    Resolve a country name from an event dict.
    Priority:
      1) event.location.country
      2) extras.country / extras.sourcecountry / extras.origin_country
      3) location.name last segment
      4) actor LOC names that look like countries
    """
    loc    = event.get("location") or {}
    extras = event.get("extras")   or {}

    for key in ("country", "sourcecountry", "origin_country"):
        value = extras.get(key)
        country = normalize_country(value, allow_fallback=False)
        if country:
            return country

    country = normalize_country(loc.get("country"), allow_fallback=False)
    if country:
        return country

    country = _extract_country_from_location(loc.get("name"))
    if country:
        return country

    for actor in event.get("actors") or []:
        if (actor.get("type") or "").upper() != "LOC":
            continue
        country = normalize_country(actor.get("name"), allow_fallback=False)
        if country:
            return country

    return None

