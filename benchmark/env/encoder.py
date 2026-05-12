"""Lightweight TF-IDF encoder used in place of sentence-transformers.

For the synthetic pilot, TF-IDF over (1,2)-grams is plenty discriminative:
topic names and fact keys appear in both corpus and queries, so cosine
similarity ranks correctly. Swap for real embeddings once we have a
torch-capable environment.
"""
from __future__ import annotations

from typing import Iterable

import numpy as np
from sklearn.feature_extraction.text import TfidfVectorizer


class TfidfEncoder:
    fitted: bool = False

    def __init__(self) -> None:
        self.vectorizer = TfidfVectorizer(
            lowercase=True,
            ngram_range=(1, 2),
            min_df=1,
            sublinear_tf=True,
        )

    def fit(self, corpus: Iterable[str]) -> "TfidfEncoder":
        self.vectorizer.fit(list(corpus))
        self.fitted = True
        return self

    def encode(
        self,
        texts,
        convert_to_numpy: bool = True,
        show_progress_bar: bool = False,
    ):
        if not self.fitted:
            raise RuntimeError("TfidfEncoder must be fitted on the corpus before encoding")
        single = isinstance(texts, str)
        batch = [texts] if single else list(texts)
        vectors = self.vectorizer.transform(batch).toarray().astype(np.float32)
        return vectors[0] if single else vectors
