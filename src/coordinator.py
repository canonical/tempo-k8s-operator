import collections
import logging
from collections import Counter
from typing import Set, Dict, List

from tempo_cluster import TempoRole, TempoClusterProvider

logger = logging.getLogger(__name__)

MINIMAL_DEPLOYMENT = {
    TempoRole.querier: 1,
    TempoRole.query_frontend: 1,
    TempoRole.ingester: 3,
    TempoRole.distributor: 1,
    TempoRole.compactor: 1,
    TempoRole.metrics_generator: 1
}
"""The minimal set of roles that need to be allocated for the
deployment to be considered consistent (otherwise we set blocked)."""

# TODO: find out what the actual recommended deployment is
RECOMMENDED_DEPLOYMENT = Counter(
    {
        TempoRole.querier: 1,
        TempoRole.query_frontend: 1,
        TempoRole.ingester: 3,
        TempoRole.distributor: 1,
        TempoRole.compactor: 1,
        TempoRole.metrics_generator: 1
    }
)
"""The set of roles that need to be allocated for the
deployment to be considered robust according to the official 
recommendations/guidelines."""


class TempoCoordinator:
    """Tempo coordinator."""

    def __init__(
            self,
            cluster_provider: TempoClusterProvider,
            is_worker: bool = False
    ):
        self._is_worker = is_worker

        if is_worker:
            # roles fulfilled by the local node, if the coordinator is also running a worker.
            local_roles = collections.Counter(r for r in TempoRole if r is not TempoRole.monolithic)
        else:
            local_roles = None

        self._cluster_provider = cluster_provider
        self._roles: Dict[TempoRole, int] = self._cluster_provider.gather_roles(base=local_roles)

        # Whether the roles list makes up a coherent mimir deployment.
        self.is_coherent = set(self._roles.keys()).issuperset(MINIMAL_DEPLOYMENT)
        self.missing_roles: Set[TempoRole]=set(MINIMAL_DEPLOYMENT).difference(self._roles.keys())
        # If the coordinator is incoherent, return the roles that are missing for it to become so.

        def _is_recommended():
            for role, min_n in RECOMMENDED_DEPLOYMENT.items():
                if self._roles.get(role, 0) < min_n:
                    return False
            return True

        self.is_recommended: bool = _is_recommended()
        # Whether the present roles are a superset of the minimal deployment.
        # I.E. If all required roles are assigned, and each role has the recommended amount of units.
        # python>=3.11 would support roles >= RECOMMENDED_DEPLOYMENT

    def get_deployment_inconsistencies(self,
                                       clustered: bool,
                                       scaled: bool,
                                       has_workers: bool,
                                       is_worker_node: bool,
                                       has_s3: bool) -> List[str]:
        """Determine whether the deployment as a whole is consistent.

        Return a list of failed consistency checks.
        """
        return self._get_deployment_inconsistencies(
            clustered=clustered,
            scaled=scaled,
            has_workers=has_workers,
            is_worker_node=is_worker_node,
            has_s3=has_s3,
            coherent=self.is_coherent,
            missing_roles=self.missing_roles
        )

    @staticmethod
    def _get_deployment_inconsistencies(clustered: bool,
                                        scaled: bool,
                                        has_workers: bool,
                                        is_worker_node: bool,
                                        has_s3: bool,
                                        coherent: bool,
                                        missing_roles: Set[TempoRole] = None
                                        ) -> List[str]:
        """Determine whether the deployment as a whole is consistent.

        Return a list of failed consistency checks.
        """

        failures = []
        # is_monolith = not (scaled or clustered or has_workers) and is_worker_node

        if not is_worker_node and not has_workers:
            failures.append("Tempo must either be a worker node or have some workers.")
        if not has_s3:
            if scaled:
                failures.append("Tempo is scaled but has no s3 integration.")
            if clustered:
                failures.append("Tempo is clustered but has no s3 integration.")
        elif not coherent:
            failures.append(f"Incoherent coordinator: missing roles: {missing_roles}.")
        return failures
