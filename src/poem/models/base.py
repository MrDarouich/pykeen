# -*- coding: utf-8 -*-

"""Utilities for getting and initializing KGE models."""

import logging
import random
from abc import abstractmethod
from collections import defaultdict
from typing import Iterable, List, Optional, Tuple

import numpy as np
import torch
from torch import nn

from ..constants import EMBEDDING_DIM
from ..instance_creation_factories.triples_factory import TriplesFactory
from ..typing import OptionalLoss
from ..utils import get_params_requiring_grad

__all__ = [
    'BaseModule',
]

log = logging.getLogger(__name__)


class BaseModule(nn.Module):
    """A base module for all of the KGE models."""

    # A dictionary of hyper-parameters to the models that use them
    _hyperparameter_usage = defaultdict(set)

    entity_embedding_max_norm: Optional[int] = None
    entity_embedding_norm_type: int = 2
    hyper_params: Tuple[str] = (EMBEDDING_DIM,)

    def __init__(
            self,
            triples_factory: TriplesFactory,
            embedding_dim: int = 50,
            entity_embeddings: nn.Embedding = None,
            criterion: OptionalLoss = None,
            preferred_device: Optional[str] = None,
            random_seed: Optional[int] = None,
    ) -> None:
        super().__init__()

        # Initialize the device
        self._set_device(preferred_device)

        self.random_seed = random_seed

        # Random seeds have to set before the embeddings are initialized
        if self.random_seed is not None:
            np.random.seed(seed=self.random_seed)
            torch.manual_seed(seed=self.random_seed)
            random.seed(self.random_seed)

        # Loss
        if criterion is None:
            self.criterion = nn.MarginRankingLoss()
        else:
            self.criterion = criterion

        # TODO: Check loss functions that require 1 and -1 as label but only
        self.is_mr_loss = isinstance(criterion, nn.MarginRankingLoss)

        self.triples_factory = triples_factory

        #: The dimension of the embeddings to generate
        self.embedding_dim = embedding_dim

        # The embeddings are first initiated when calling the fit function
        self.entity_embeddings = entity_embeddings

        # Marker to check whether the forward constraints of a models has been applied before starting loss calculation
        self.forward_constraint_applied = False

    def __init_subclass__(cls, **kwargs):
        super().__init_subclass__(**kwargs)

        # Keep track of the hyper-parameters that are used across all
        # subclasses of BaseModule
        for k, v in cls.__init__.__annotations__.items():
            if k not in {'return', 'triples_factory'}:
                BaseModule._hyperparameter_usage[k].add(cls.__name__)

    @property
    def num_entities(self):
        """The number of entities in the knowledge graph."""
        return self.triples_factory.num_entities

    @property
    def num_relations(self):
        """The number of unique relation types in the knowledge graph."""
        return self.triples_factory.num_relations

    def _init_embeddings(self):
        self.entity_embeddings = nn.Embedding(
            self.num_entities,
            self.embedding_dim,
            max_norm=self.entity_embedding_max_norm,
            norm_type=self.entity_embedding_norm_type,
        )

    def _set_device(self, device: Optional[str] = None) -> None:
        """Get the Torch device to use."""
        if device is None or device == 'gpu':
            if torch.cuda.is_available():
                self.device = torch.device('cuda')
            else:
                self.device = torch.device('cpu')
                log.info('No cuda devices were available. The model runs on CPU')
        else:
            self.device = torch.device('cpu')

    def _to_cpu(self):
        """Transfer the entire model to CPU"""
        self._set_device('cpu')
        self.to(self.device)
        torch.cuda.empty_cache()

    def _to_gpu(self):
        """Transfer the entire model to GPU"""
        self._set_device('gpu')
        self.to(self.device)
        torch.cuda.empty_cache()

    def predict_scores(
            self,
            triples: torch.tensor
    ):
        """
        Calculate the scores for triples.
        This method takes subject, relation and object of each triple and calculates the corresponding score.

        :param triples: torch.tensor, shape: (number of triples, 3)
            The indices of (subject, relation, object) triples.
        :return: numpy.ndarray, shape: (number of triples, 1)
            The score for each triple.
        """
        scores = self.forward_owa(triples)
        return scores

    def predict_scores_all_objects(
            self,
            batch: torch.tensor,
    ) -> torch.tensor:
        """
        Forward pass using right side (object) prediction for obtaining scores of all possible objects.
        This method calculates the score for all possible objects for each (subject, relation) pair.

        :param batch: torch.tensor, shape: (batch_size, 2)
            The indices of (subject, relation) pairs.
        :return: torch.tensor, shape: (batch_size, num_entities)
            For each s-p pair, the scores for all possible objects.
        """
        scores = self.forward_cwa(batch)
        return scores

    def predict_scores_all_subjects(
            self,
            batch: torch.tensor,
    ) -> torch.tensor:
        """
        Forward pass using left side (subject) prediction for obtaining scores of all possible subjects.
        This method calculates the score for all possible subjects for each (relation, object) pair.

        :param batch: torch.tensor, shape: (batch_size, 2)
            The indices of (relation, object) pairs.
        :return: torch.tensor, shape: (batch_size, num_entities)
            For each p-o pair, the scores for all possible subjects.
        """
        '''
        In case the model was trained using inverse triples, the scoring of all subjects is not handled by calculating
        the scores for all subjects based on a (relation, object) pair, but instead all possible objects are calculated
        for a (object, inverse_relation) pair.
        '''
        if self.triples_factory.create_inverse_triples:
            '''
            The POEM package handles _inverse relations_ by adding the number of relations to the index of the
            _native relation_.
            Example:
            The triples/knowledge graph used to train the model contained 100 relations. Due to using inverse relations,
            the model now has an additional 100 inverse relations. If the _native relation_ has the index 3, the index
            of the _inverse relation_ is 103.  
            '''
            # The number of relations stored in the triples factory includes the number of inverse relations
            num_relations = self.triples_factory.num_relations // 2
            batch.view(-1, 2)[:, 0] = batch.view(-1, 2)[:, 0] + num_relations
            # The forward cwa function requires (entity, relation) pairs instead of (relation, entity)
            batch = batch.view(-1, 2).flip(1)
            scores = self.forward_cwa(batch)
        else:
            scores = self.forward_inverse_cwa(batch)
        return scores

    @staticmethod
    def _get_embeddings(elements, embedding_module, embedding_dim):
        return embedding_module(elements).view(-1, embedding_dim)

    # FIXME this will be used in a later commit for the restructuring
    def compute_probabilities(self, scores):
        return self.sigmoid(scores)

    def compute_mr_loss(
            self,
            positive_scores: torch.Tensor,
            negative_scores: torch.Tensor,
    ) -> torch.Tensor:
        assert self.is_mr_loss, 'The chosen criterion does not allow the calculation of label losses. ' \
                                'Please use the compute_mr_loss method instead'
        y = torch.ones_like(negative_scores, device=self.device)
        loss = self.criterion(positive_scores, negative_scores, y)
        return loss

    def compute_label_loss(
            self,
            predictions: torch.Tensor,
            labels: torch.Tensor,
    ) -> torch.Tensor:
        assert not self.is_mr_loss, 'The chosen criterion does not allow the calculation of margin ranking losses. ' \
                                    'Please use the compute_label_loss method instead'
        loss = self.criterion(predictions, labels)
        return loss

    @abstractmethod
    def forward_owa(
            self,
            batch: torch.tensor,
    ) -> torch.tensor:
        """
        Forward pass for training with the OWA.
        This method takes subject, relation and object of each triple and calculates the corresponding score.

        :param batch: torch.tensor, shape: (batch_size, 3)
            The indices of (subject, relation, object) triples.
        :return: torch.tensor, shape: (batch_size, 1)
            The score for each triple.

        """
        raise NotImplementedError()

    @abstractmethod
    def forward_cwa(
            self,
            batch: torch.tensor,
    ) -> torch.tensor:
        """
        Forward pass using right side (object) prediction for training with the CWA.
        This method calculates the score for all possible objects for each (subject, relation) pair.

        :param batch: torch.tensor, shape: (batch_size, 2)
            The indices of (subject, relation) pairs.
        :return: torch.tensor, shape: (batch_size, num_entities)
            For each s-p pair, the scores for all possible objects.
        """
        raise NotImplementedError()

    @abstractmethod
    def forward_inverse_cwa(
            self,
            batch: torch.tensor,
    ) -> torch.tensor:
        """
        Forward pass using left side (subject) prediction for training with the CWA.
        This method calculates the score for all possible subjects for each (relation, object) pair.

        :param batch: torch.tensor, shape: (batch_size, 2)
            The indices of (relation, object) pairs.
        :return: torch.tensor, shape: (batch_size, num_entities)
            For each p-o pair, the scores for all possible subjects.
        """
        raise NotImplementedError()

    def get_grad_params(self) -> Iterable[nn.Parameter]:
        """Get the parameters that require gradients."""
        return get_params_requiring_grad(self)

    @classmethod
    def get_model_params(cls) -> List:
        """Returns the model parameters."""
        return ['num_entities', 'num_relations', 'embedding_dim', 'criterion', 'preferred_device', 'random_seed']