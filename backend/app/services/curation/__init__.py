"""KB curation loop — the nightly job that turns yesterday's failures into review cards.

The pipeline is four stages, deliberately ordered cheapest-first, because the whole cost
argument of the feature lives in that ordering:

    miner.collect()   pure SQL over columns the system already wrote   — zero tokens
    cluster.cluster() one embedding batch (self-hosted BGE-M3)         — zero tokens
    propose.propose() ONE forced-tool Claude call PER CLUSTER          — the only spend
    apply.apply_accepted() a human said yes -> kb_ingest re-chunks + re-embeds

The principle is in `miner`'s docstring and is worth restating here: we do not summarise
yesterday's conversations. We mine the failures the system already labelled for free. That
corpus is orders of magnitude smaller and far higher precision, and it is what makes a
nightly job affordable on one box.

`runner` is the entry point the `cq-worker` process calls; nothing here is imported by the
API request path.
"""
from .miner import Candidate, SIGNAL_WEIGHT, collect, normalise_question

__all__ = ["Candidate", "SIGNAL_WEIGHT", "collect", "normalise_question"]
