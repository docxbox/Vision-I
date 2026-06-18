# Lazy imports — avoids pulling in torch/transformers at package import time
# (which would cause test failures when those heavy deps are absent).
# Import explicitly: from nlp.pipeline import NLPPipeline

__all__ = ["NLPPipeline", "EntityResolver"]
