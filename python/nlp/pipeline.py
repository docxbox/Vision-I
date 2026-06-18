"""
nlp/pipeline.py
â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€
Vision-I multilingual NLP enrichment pipeline.

Models:
  NER:       Davlan/xlm-roberta-base-ner-hrl
             True multilingual â€" handles Arabic, Chinese, Persian, Russian,
             Turkish, and 9 other language families out of the box.
             Falls back to spaCy en_core_web_sm for English-only environments.

  Sentiment: cardiffnlp/twitter-xlm-roberta-base-sentiment
             Covers 8 languages natively; degrades gracefully on others.
             Falls back to cardiffnlp/twitter-roberta-base-sentiment-latest (EN only).

Both models are loaded lazily on first use so startup is instant.
If a model fails to load, that pipeline step is silently skipped.

Entity type mapping (HRL model uses CoNLL-style tags):
  PER â†’ PERSON
  ORG â†’ ORG
  LOC â†’ LOC

Steps:
  0. Actor deduplication    â€" always runs, no model deps
  1. Multilingual NER       â€" xlm-roberta (or spaCy fallback)
  2. Multilingual sentiment â€" multilingual roberta (or EN roberta fallback)
  3. Entity resolution      â€" RapidFuzz fuzzy dedup across events
  4. Geo-tag                â€" promote first LOC to event.location if unset
"""

import logging
import re
from typing import Any, Dict, List, Optional, Tuple

from config.settings import settings
from core.entity_normalizer import normalize_actor_name, normalize_actor_payloads
from core.schema import Actor, VisionEvent
from core.geo import resolve_event_country, apply_geocoding

logger = logging.getLogger("vision_i.nlp.pipeline")
_ner_pipe       = None   # Hugging Face transformers pipeline (multilingual)
_spacy_nlp      = None   # spaCy fallback (English only)
_sentiment_pipe = None   # Hugging Face transformers pipeline

def _load_ner():
    """
    Load Davlan/xlm-roberta-base-ner-hrl (multilingual NER).
    Falls back to spaCy if transformers/torch unavailable.
    """
    global _ner_pipe
    if _ner_pipe is not None:
        return _ner_pipe

    try:
        from transformers import pipeline as hf_pipeline
        _ner_pipe = hf_pipeline(
            "ner",
            model              = settings.ner_model,
            aggregation_strategy = "simple",   # merges B-/I- tokens into spans
            device             = -1,           # CPU; set 0 for GPU
        )
        logger.info("Multilingual NER loaded: %s", settings.ner_model)
    except Exception as exc:
        logger.warning("Multilingual NER unavailable (%s) - trying spaCy fallback", exc)
        _ner_pipe = False

    return _ner_pipe


def _load_spacy():
    """spaCy fallback â€" English only, used when transformers NER fails to load."""
    global _spacy_nlp
    if _spacy_nlp is not None:
        return _spacy_nlp
    try:
        import spacy
        _spacy_nlp = spacy.load(settings.spacy_model)
        logger.info("spaCy fallback NER loaded: %s", settings.spacy_model)
    except Exception as exc:
        logger.warning("spaCy also unavailable (%s) - NER step will be skipped", exc)
        _spacy_nlp = False
    return _spacy_nlp


def _load_sentiment():
    """
    Load multilingual sentiment model.
    Falls back to English-only Cardiff model if unavailable.
    """
    global _sentiment_pipe
    if _sentiment_pipe is not None:
        return _sentiment_pipe

    for model_name in [settings.sentiment_model, settings.sentiment_model_fallback]:
        try:
            from transformers import pipeline as hf_pipeline
            _sentiment_pipe = hf_pipeline(
                "text-classification",
                model      = model_name,
                top_k      = 1,
                truncation = True,
                max_length = 512,
                device     = -1,
            )
            logger.info("Sentiment model loaded: %s", model_name)
            break
        except Exception as exc:
            logger.warning("Sentiment model %s unavailable: %s", model_name, exc)
            _sentiment_pipe = False

    return _sentiment_pipe

# xlm-roberta-base-ner-hrl uses CoNLL tags: PER, ORG, LOC, MISC
_HRL_TYPE_MAP = {
    "PER":  "PERSON",
    "ORG":  "ORG",
    "LOC":  "LOC",
    "MISC": "ORG",     # miscellaneous â†’ org (events, products, etc.)
}

# spaCy fallback type map
_SPACY_TYPE_MAP = {
    "PERSON":    "PERSON",
    "ORG":       "ORG",
    "GPE":       "LOC",
    "LOC":       "LOC",
    "NORP":      "ORG",
    "FAC":       "LOC",
    "PRODUCT":   "ORG",
    "EVENT":     "ORG",
    "WORK_OF_ART": "ORG",
}

# Normalised sentiment label map (handles both model families)
_LABEL_MAP = {
    # Cardiff EN model
    "positive":  "POSITIVE",
    "negative":  "NEGATIVE",
    "neutral":   "NEUTRAL",
    # Cardiff multilingual model
    "pos":       "POSITIVE",
    "neg":       "NEGATIVE",
    "neu":       "NEUTRAL",
    # Generic label_N fallbacks
    "label_0":   "NEGATIVE",
    "label_1":   "NEUTRAL",
    "label_2":   "POSITIVE",
}

_STOPWORDS = {
    "the", "a", "an", "and", "or", "but", "in", "on", "at", "to",
    "for", "of", "with", "by", "from", "that", "this", "it", "he",
    "she", "we", "they", "i", "you", "said", "mr", "ms", "dr",
}

_ABBREV_BLOCKLIST = {
    "sse", "wnw", "wsw", "ene", "ese", "nnw", "nne", "ssw",
    "fpv", "spi", "eta", "utc",
}

# Maximum meaningful actor name length â€" blocks full headline clauses
_MAX_ENTITY_LEN = 50


def _is_valid_entity(text: str) -> bool:
    """
    Quality filter for extracted entity spans.

    Blocks:
      - Too short / too long
      - Starts with digit (distances, magnitudes)
      - Any token is purely numeric  (catches "Magnitude 6.7")
      - Compass abbreviations (SSE, WNW â€¦)
      - Hashtags and URLs
      - Headline verb phrases ("Sounds ALARM", "Blocks Weapons Sales")
      - Punctuation-only or bracket-wrapped fragments
    """
    s = text.strip()

    if len(s) < 3 or len(s) > _MAX_ENTITY_LEN:
        return False

    # Contains purely numeric token (Magnitude 6.7, +7386)
    if any(re.fullmatch(r"[+\-]?\d+[\d.,]*", tok) for tok in s.split()):
        return False

    if s.lower() in _STOPWORDS:
        return False

    # Short all-caps abbreviation
    if s.upper() == s and len(s) <= 5 and s.lower() in _ABBREV_BLOCKLIST:
        return False

    # Hashtag or URL
    if s.startswith("#") or s.startswith("http"):
        return False

    # Starts with punctuation or bracket
    if s[0] in "\"'([{<+":
        return False

    # Verb-phrase heuristic: "Sounds ALARM", "Blocks Sales"
    tokens = s.split()
    if len(tokens) == 2 and tokens[1].isupper() and len(tokens[1]) > 3:
        return False

    return True

class NLPPipeline:
    """
    Multilingual NLP enrichment pipeline.

    One instance lives on app.state (created in api/main.py lifespan).
    All methods are stateless except for the EntityResolver registry.

    Usage:
        pipeline = NLPPipeline()
        enriched_events = pipeline.process(events)
    """

    def __init__(self) -> None:
        self.resolver = EntityResolver(threshold=settings.entity_resolution_threshold)

    def process(self, events: List[VisionEvent], max_nlp: int = 150) -> List[VisionEvent]:
        if not events:
            return events

        logger.info("NLP pipeline: %d events", len(events))

        # Cap heavy NLP steps to prevent CPU starvation on laptops
        head = events[:max_nlp]
        tail = events[max_nlp:]
        if tail:
            logger.info("NLP pipeline: capping transformer passes to %d (skipping NER/sentiment for %d)", max_nlp, len(tail))

        head = self._dedup_actors(head)           # Step 0 â€" always
        head = self._run_ner(head)                # Step 1 â€" multilingual NER
        head = self._run_sentiment(head)          # Step 2 â€" multilingual sentiment

        # Dedup actors on tail too (cheap, no model)
        tail = self._dedup_actors(tail)

        events = head + tail
        events = self._run_entity_resolution(events)   # Step 3 — fuzzy dedup
        events = self._apply_country_resolution(events) # Step 4 — country tagging
        events = self._apply_geocoding(events)          # Step 5 — resolve lat/lon

        # Step 6 — MITRE ATT&CK tag mapping (lightweight keyword lookup)
        try:
            from nlp.mitre_mapper import apply_mitre_tags
            events = apply_mitre_tags(events)
        except Exception as _mitre_exc:
            logger.debug("MITRE mapper skipped: %s", _mitre_exc)

        logger.info("NLP pipeline complete")
        return events

    @staticmethod
    def _dedup_actors(events: List[VisionEvent]) -> List[VisionEvent]:
        """
        Remove duplicate actors within each event.
        Always runs â€" no model dependencies.
        """
        for event in events:
            event["actors"] = normalize_actor_payloads(event.get("actors") or [])
        return events

    def _run_ner(self, events: List[VisionEvent]) -> List[VisionEvent]:
        ner = _load_ner()
        if ner:
            return self._ner_transformer(events, ner)

        # Transformer NER unavailable â€" try spaCy (English only)
        spacy_nlp = _load_spacy()
        if spacy_nlp:
            return self._ner_spacy(events, spacy_nlp)

        logger.debug("No NER model available - skipping NER step")
        return events

    def _ner_transformer(self, events: List[VisionEvent], pipe) -> List[VisionEvent]:
        """
        Run xlm-roberta-base-ner-hrl on all events.
        Works on any language â€" no language filtering needed.
        Uses batch processing for 5-10x speedup over sequential.
        """
        texts = [self._event_text(e)[:512] for e in events]

        try:
            # Batch process for performance â€" 32 texts at a time
            all_results = pipe(texts, batch_size=settings.nlp_batch_size)
        except Exception as exc:
            logger.warning("NER batch failed (%s), falling back to sequential", exc)
            all_results = []
            for text in texts:
                try:
                    all_results.append(pipe(text))
                except Exception:
                    all_results.append([])

        for event, ner_results in zip(events, all_results):
            new_actors: List[Actor] = []
            geo_entities: List[str] = []

            for ent in ner_results:
                text    = (ent.get("word") or "").strip()
                raw_tag = ent.get("entity_group") or ent.get("entity", "")

                # Strip B-/I- prefixes from non-aggregated results
                if raw_tag.startswith(("B-", "I-")):
                    raw_tag = raw_tag[2:]

                actor_type = _HRL_TYPE_MAP.get(raw_tag.upper())
                if not actor_type:
                    continue

                # Clean up subword tokenisation artefacts (â–, ##)
                text = re.sub(r"^[â–#]+", "", text).strip()

                if not _is_valid_entity(text):
                    continue
                if text.lower() in _STOPWORDS:
                    continue

                new_actors.append({"name": text, "type": actor_type})
                if actor_type == "LOC":
                    geo_entities.append(text)

            self._merge_actors(event, new_actors)
            self._maybe_geotag(event, geo_entities)

        return events

    def _ner_spacy(self, events: List[VisionEvent], nlp) -> List[VisionEvent]:
        """spaCy fallback â€" English-only events only."""
        en_events  = [e for e in events if (e.get("language") or "en").startswith("en")]
        en_texts   = [self._event_text(e) for e in en_events]

        if not en_texts:
            return events

        try:
            docs = list(nlp.pipe(en_texts, batch_size=settings.nlp_batch_size))
        except Exception as exc:
            logger.error("spaCy NER batch failed: %s", exc)
            return events

        for event, doc in zip(en_events, docs):
            new_actors: List[Actor] = []
            geo_entities: List[str] = []

            for ent in doc.ents:
                text       = ent.text.strip()
                actor_type = _SPACY_TYPE_MAP.get(ent.label_)
                if not actor_type:
                    continue
                if not _is_valid_entity(text):
                    continue
                if text.lower() in _STOPWORDS:
                    continue
                new_actors.append({"name": text, "type": actor_type})
                if actor_type == "LOC":
                    geo_entities.append(text)

            self._merge_actors(event, new_actors)
            self._maybe_geotag(event, geo_entities)

        return events

    def _run_sentiment(self, events: List[VisionEvent]) -> List[VisionEvent]:
        pipe = _load_sentiment()
        if not pipe:
            return events

        # Skip events already scored by extractors (stocks, USGS use rule-based scoring)
        to_score = [e for e in events if not e.get("sentiment")]
        if not to_score:
            return events

        texts = [self._event_text(e)[:512] for e in to_score]

        try:
            results = pipe(texts, batch_size=settings.nlp_batch_size)
            for event, res in zip(to_score, results):
                top       = res[0] if isinstance(res, list) else res
                raw_label = (top.get("label") or "neutral").lower()
                label     = _LABEL_MAP.get(raw_label, "NEUTRAL")
                score     = float(top.get("score", 0.5))
                event["sentiment"] = {"label": label, "score": round(score, 4)}
        except Exception as exc:
            logger.error("Sentiment batch failed: %s", exc)

        return events

    def _run_entity_resolution(self, events: List[VisionEvent]) -> List[VisionEvent]:
        """Canonicalise actor names across the batch using fuzzy matching."""
        try:
            all_actors: List[Tuple[int, int, str]] = []
            for ei, event in enumerate(events):
                for ai, actor in enumerate(event.get("actors") or []):
                    name = actor.get("name", "")
                    if name:
                        all_actors.append((ei, ai, name))

            if not all_actors:
                return events

            canon_map = self.resolver.resolve([n for _, _, n in all_actors])

            for (ei, ai, _), canonical in zip(all_actors, canon_map):
                normalized = normalize_actor_name(
                    canonical,
                    events[ei]["actors"][ai].get("type"),
                ) or canonical
                events[ei]["actors"][ai]["name"] = normalized
                events[ei]["actors"][ai]["canonical"] = normalized

            for event in events:
                event["actors"] = normalize_actor_payloads(event.get("actors") or [])

        except Exception as exc:
            logger.warning("Entity resolution failed: %s", exc)

        return events

    @staticmethod
    def _merge_actors(event: VisionEvent, new_actors: List[Actor]) -> None:
        """Merge new NLP actors into event, deduplicating by name."""
        existing = {a["name"].lower() for a in event.get("actors") or []}
        seen_new: set = set()
        for actor in new_actors:
            normalized_name = normalize_actor_name(actor.get("name"), actor.get("type"))
            if not normalized_name:
                continue
            actor["name"] = normalized_name
            actor["canonical"] = normalized_name
            key = normalized_name.lower()
            if key not in existing and key not in seen_new:
                event.setdefault("actors", []).append(actor)
                seen_new.add(key)

    @staticmethod
    def _maybe_geotag(event: VisionEvent, geo_entities: List[str]) -> None:
        """Promote first LOC entity to event.location.name if no coordinates set."""
        if not geo_entities:
            return
        loc = event.get("location") or {}
        if loc.get("lat"):
            return
        if not loc.get("name"):
            event["location"] = {**loc, "name": geo_entities[0]}

    @staticmethod
    def _event_text(event: VisionEvent) -> str:
        """
        Build NLP input text.
        Title is repeated to upweight named entities that appear in the headline.
        Body is truncated to keep inference fast.
        """
        title = event.get("title") or ""
        body  = (event.get("body") or event.get("description") or "")[:800]
        return f"{title}. {title}. {body}".strip()

    @staticmethod
    def _apply_country_resolution(events: List[VisionEvent]) -> List[VisionEvent]:
        for event in events:
            country = resolve_event_country(event)
            if not country:
                continue
            location = event.get("location") or {}
            if not location.get("country"):
                location["country"] = country
                event["location"] = location
            extras = event.get("extras") or {}
            if not extras.get("country"):
                extras["country"] = country
                event["extras"] = extras
        return events

    @staticmethod
    def _apply_geocoding(events: List[VisionEvent]) -> List[VisionEvent]:
        """Resolve lat/lon for any event that lacks coordinates."""
        geocoded = 0
        for event in events:
            loc = event.get("location") or {}
            if loc.get("lat") is None:
                apply_geocoding(event)
                if (event.get("location") or {}).get("lat") is not None:
                    geocoded += 1
        if geocoded:
            logger.info("Geocoding: resolved coordinates for %d events", geocoded)
        return events

class EntityResolver:
    """
    Cross-event actor deduplication using RapidFuzz token_sort_ratio.

    "Donald Trump" / "Trump" / "D. Trump" â†’ all resolve to "Donald Trump"
    (longest matching form wins as canonical).

    The registry is stateful and grows across pipeline runs â€" reset() between
    unrelated ingestion jobs if cross-contamination is a concern.
    """

    def __init__(self, threshold: int = 88) -> None:
        self.threshold = threshold
        self._registry: Dict[str, str] = {}   # lower_key â†’ canonical form

    def resolve(self, names: List[str]) -> List[str]:
        try:
            from rapidfuzz import process as rf_process, fuzz
        except ImportError:
            logger.debug("rapidfuzz not installed - skipping entity resolution")
            return names

        canonicals: List[str] = []
        registry_keys = list(self._registry.keys())

        for name in names:
            if not name:
                canonicals.append(name)
                continue

            key = name.lower().strip()

            # Exact match
            if key in self._registry:
                canonicals.append(self._registry[key])
                continue

            # Fuzzy match
            if registry_keys:
                hit = rf_process.extractOne(
                    key,
                    registry_keys,
                    scorer       = fuzz.token_sort_ratio,
                    score_cutoff = self.threshold,
                )
                if hit:
                    canonical = self._registry[hit[0]]
                    self._registry[key] = canonical
                    canonicals.append(canonical)
                    continue

            # New entity â€" prefer longer form as canonical
            existing = self._registry.get(key)
            canonical = name if (not existing or len(name) >= len(existing)) else existing
            self._registry[key] = canonical
            registry_keys.append(key)
            canonicals.append(canonical)

        return canonicals

    def reset(self) -> None:
        self._registry.clear()

