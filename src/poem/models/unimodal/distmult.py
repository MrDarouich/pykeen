# -*- coding: utf-8 -*-

"""Implementation of DistMult."""

from typing import Optional

import numpy as np
import torch
import torch.autograd
from torch import nn
from torch.nn import functional

from ..base import BaseModule
from ...instance_creation_factories.triples_factory import TriplesFactory
from ...typing import OptionalLoss

__all__ = ['DistMult']


class DistMult(BaseModule):
    """An implementation of DistMult from [yang2014]_.

    This model simplifies RESCAL by restricting matrices representing relations as diagonal matrices.

    Note:
      - For FB15k, Yang *et al.* report 2 negatives per each positive.

    .. seealso::

       - OpenKE `implementation of DistMult <https://github.com/thunlp/OpenKE/blob/master/models/DistMult.py>`_
    """

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
        """Initialize the model."""
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
        self.relation_embeddings = nn.Embedding(self.num_relations, self.embedding_dim)
        # The same bound is used for both entity embeddings and relation embeddings because they have the same dimension
        embeddings_init_bound = 6 / np.sqrt(self.embedding_dim)
        nn.init.uniform_(
            self.entity_embeddings.weight.data,
            a=-embeddings_init_bound,
            b=+embeddings_init_bound,
        )
        nn.init.uniform_(
            self.relation_embeddings.weight.data,
            a=-embeddings_init_bound,
            b=+embeddings_init_bound,
        )

        # Initialise relation embeddings to unit length
        functional.normalize(self.relation_embeddings.weight.data, out=self.relation_embeddings.weight.data)

    def _apply_forward_constraints_if_necessary(self):
        # Normalize embeddings of entities
        if not self.forward_constraint_applied:
            functional.normalize(self.entity_embeddings.weight.data, out=self.entity_embeddings.weight.data)
            self.forward_constraint_applied = True

    def forward_owa(self, batch: torch.tensor) -> torch.tensor:
        """Forward pass for training with the OWA."""
        # Normalize embeddings
        self._apply_forward_constraints_if_necessary()

        # Get embeddings
        h = self.entity_embeddings(batch[:, 0])
        r = self.relation_embeddings(batch[:, 1])
        t = self.entity_embeddings(batch[:, 2])

        # Compute score
        scores = torch.sum(h * r * t, dim=-1, keepdim=True)

        return scores

    def forward_cwa(self, batch: torch.tensor) -> torch.tensor:
        """Forward pass using right side (object) prediction for training with the CWA."""
        # Normalize embeddings
        self._apply_forward_constraints_if_necessary()

        # Get embeddings
        h = self.entity_embeddings(batch[:, 0])
        r = self.relation_embeddings(batch[:, 1])
        t = self.entity_embeddings.weight

        # Rank against all entities
        scores = torch.sum(h[:, None, :] * r[:, None, :] * t[None, :, :], dim=-1)

        return scores

    def forward_inverse_cwa(self, batch: torch.tensor) -> torch.tensor:
        """Forward pass using left side (subject) prediction for training with the CWA."""
        # Normalize embeddings
        self._apply_forward_constraints_if_necessary()

        # Get embeddings
        h = self.entity_embeddings.weight
        r = self.relation_embeddings(batch[:, 0])
        t = self.entity_embeddings(batch[:, 1])

        # Rank against all entities
        scores = torch.sum(h[None, :, :] * r[:, None, :] * t[:, None, :], dim=-1)

        return scores
