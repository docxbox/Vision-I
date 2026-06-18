"""
nlp/mitre_mapper.py
-------------------
Lightweight MITRE ATT&CK tag mapper.

Maps NLP-detected entity types and keywords to MITRE ATT&CK technique IDs.
Uses a keyword/phrase lookup table — no external API required.

Returns a list of tag dicts: [{"technique_id": "T1566", "technique_name": "Phishing", "tactic": "Initial Access"}]
"""
from __future__ import annotations

from typing import Any, Dict, List

# Keyword → (technique_id, technique_name, tactic)
# Ordered longest-match first where prefixes clash.
_KEYWORD_MAP: list[tuple[str, str, str, str]] = [
    # Keyword                      ID       Name                            Tactic
    ("phishing",                  "T1566", "Phishing",                     "Initial Access"),
    ("spearphish",                "T1566", "Phishing",                     "Initial Access"),
    ("ransomware",                "T1486", "Data Encrypted for Impact",    "Impact"),
    ("ddos",                      "T1498", "Network Denial of Service",    "Impact"),
    ("denial of service",         "T1498", "Network Denial of Service",    "Impact"),
    ("data exfiltration",         "T1041", "Exfiltration Over C2 Channel", "Exfiltration"),
    ("credential",                "T1078", "Valid Accounts",               "Defense Evasion"),
    ("supply chain",              "T1195", "Supply Chain Compromise",      "Initial Access"),
    ("watering hole",             "T1189", "Drive-by Compromise",          "Initial Access"),
    ("zero day",                  "T1203", "Exploitation for Client Execution","Execution"),
    ("zero-day",                  "T1203", "Exploitation for Client Execution","Execution"),
    ("exploit",                   "T1203", "Exploitation for Client Execution","Execution"),
    ("social engineering",        "T1566", "Phishing",                     "Initial Access"),
    ("disinformation",            "T1583", "Acquire Infrastructure",       "Resource Development"),
    ("influence operation",       "T1583", "Acquire Infrastructure",       "Resource Development"),
    ("propaganda",                "T1583", "Acquire Infrastructure",       "Resource Development"),
    ("malware",                   "T1059", "Command and Scripting Interpreter","Execution"),
    ("trojan",                    "T1059", "Command and Scripting Interpreter","Execution"),
    ("botnet",                    "T1583", "Acquire Infrastructure",       "Resource Development"),
    ("apt",                       "T1583", "Acquire Infrastructure",       "Resource Development"),
    ("nation state",              "T1583", "Acquire Infrastructure",       "Resource Development"),
    ("insider threat",            "T1078", "Valid Accounts",               "Defense Evasion"),
    ("cyber attack",              "T1059", "Command and Scripting Interpreter","Execution"),
    ("intrusion",                 "T1190", "Exploit Public-Facing Application","Initial Access"),
    ("vulnerability",             "T1190", "Exploit Public-Facing Application","Initial Access"),
    ("sanction",                  "T1657", "Financial Theft",              "Impact"),
    ("money laundering",          "T1657", "Financial Theft",              "Impact"),
    ("cryptocurrency",            "T1657", "Financial Theft",              "Impact"),
    ("airdrop",                   "T1657", "Financial Theft",              "Impact"),
    ("weapons",                   "T1588", "Obtain Capabilities",          "Resource Development"),
    ("smuggling",                 "T1588", "Obtain Capabilities",          "Resource Development"),
    ("maritime",                  "T1498", "Network Denial of Service",    "Impact"),
    ("gps jamming",               "T1498", "Network Denial of Service",    "Impact"),
    ("electronic warfare",        "T1498", "Network Denial of Service",    "Impact"),
]


def tag_event(event: Dict[str, Any]) -> List[Dict[str, str]]:
    """
    Return MITRE ATT&CK tags for an event based on title + body keywords.
    Returns at most 5 unique technique tags.
    """
    haystack = " ".join([
        (event.get("title") or ""),
        (event.get("body") or ""),
        (event.get("summary") or ""),
        " ".join(
            a.get("name", "") if isinstance(a, dict) else str(a)
            for a in (event.get("actors") or [])
        ),
    ]).lower()

    seen_ids: set[str] = set()
    tags: list[Dict[str, str]] = []

    for kw, tid, tname, tactic in _KEYWORD_MAP:
        if kw in haystack and tid not in seen_ids:
            seen_ids.add(tid)
            tags.append({"technique_id": tid, "technique_name": tname, "tactic": tactic})
        if len(tags) >= 5:
            break

    return tags


def apply_mitre_tags(events: list[Dict[str, Any]]) -> list[Dict[str, Any]]:
    """Enrich a list of events with mitre_tags in-place."""
    for ev in events:
        if not ev.get("mitre_tags"):
            ev["mitre_tags"] = tag_event(ev)
    return events
