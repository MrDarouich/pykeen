# -*- coding: utf-8 -*-

"""Implementation of RESCAL."""

from typing import Optional

from torch import nn

from poem.instance_creation_factories.triples_factory import TriplesFactory
from poem.models.base import BaseModule
from poem.utils import slice_triples
from ...typing import OptionalLoss

__all__ = ['RESCAL']


class RESCAL(BaseModule):
    """An implementation of RESCAL from [nickel2011]_.

    This model represents relations as matrices and models interactions between latent features.

    .. seealso::

       - OpenKE `implementation of RESCAL <https://github.com/thunlp/OpenKE/blob/master/models/RESCAL.py>`_
    """

    # TODO: The paper uses a regularization term on both, the entity embeddings, as well as the relation matrices, to avoid overfitting.
    margin_ranking_loss_size_average: bool = True

    def __init__(
            self,
            triples_factory: TriplesFactory,
            embedding_dim: int = 50,
            entity_embeddings: Optional[nn.Embedding] = None,
            relation_embeddings: Optional[nn.Embedding] = None,
            criterion: OptionalLoss = None,
            preferred_device: Optional[str] = None,
            random_seed: Optional[int] = None,
    ) -> None:
        if criterion is None:
            criterion = nn.MarginRankingLoss(margin=1., reduction='mean')

        super().__init__(
            triples_factory=triples_factory,
            embedding_dim=embedding_dim,
            entity_embeddings=entity_embeddings,
            criterion=criterion,
            preferred_device=preferred_device,
            random_seed=random_seed,
        )

        self.relation_embeddings = relation_embeddings
        if None in [self.entity_embeddings, self.relation_embeddings]:
            self._init_embeddings()

    def _init_embeddings(self):
        super()._init_embeddings()
        self.relation_embeddings = nn.Embedding(self.num_relations, self.embedding_dim ** 2)

    def forward_owa(self, triples):
        # Get triple embeddings
        heads, relations, tails = slice_triples(triples)

        # shape: (b, d)
        head_embeddings = self.entity_embeddings(heads).view(-1, 1, self.embedding_dim)
        # shape: (b, d, d)
        relation_embeddings = self.relation_embeddings(relations).view(-1, self.embedding_dim, self.embedding_dim)
        # shape: (b, d)
        tail_embeddings = self.entity_embeddings(tails).view(-1, self.embedding_dim, 1)

        scores = head_embeddings @ relation_embeddings @ tail_embeddings

        return scores

    # TODO: Implement forward_cwa
