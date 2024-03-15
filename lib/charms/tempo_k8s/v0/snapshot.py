"""Snapshot charm library.

This library acts as a bridge to scenario's
[State](https://github.com/canonical/ops-scenario/blob/main/scenario/state.py#L861) data structure,
allowing you to capture snapshots of a live charm's current state and serialize them as json.
"""
import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict, List, Optional, cast

import ops
import scenario
import scenario.runtime

if TYPE_CHECKING:
    from scenario.state import AnyRelation

logger = logging.getLogger("snapshot")

# The unique Charmhub library identifier, never change it
LIBID = "012181337a3a4717861b5fb4d8bdfc2b"

# Increment this major API version when introducing breaking changes
LIBAPI = 0

# Increment this PATCH version before using `charmcraft publish-lib` or reset
# to 0 if you are raising the major API version
LIBPATCH = 1

PYDEPS = ["ops-scenario>=6"]


class SnapshotBackend:
    """Snapshot backend."""

    def __init__(self, charm: ops.CharmBase):
        self.charm = charm
        self._unit_state_db = scenario.runtime.UnitStateDB(charm.charm_dir / ".unit-state.db")

    def get_config(self) -> Dict:  # noqa: D102
        return dict(self.charm.config)

    def get_opened_ports(self) -> List[scenario.Port]:  # noqa: D102
        return [scenario.Port(p.protocol, p.port) for p in self.charm.unit.opened_ports()]

    def get_leader(self) -> bool:  # noqa: D102
        return self.charm.unit.is_leader()

    def get_model(self) -> scenario.Model:  # noqa: D102
        return scenario.Model(
            name=self.charm.model.name,
            uuid=self.charm.model.uuid,
            # fixme: how do we determine if we're on a kubernetes or machine model?
        )

    def get_planned_units(self) -> int:  # noqa: D102
        return self.charm.app.planned_units()

    def get_app_status(self) -> ops.StatusBase:  # noqa: D102
        if not self.charm.unit.is_leader():
            return ops.UnknownStatus()
        return self.charm.app.status

    def get_unit_status(self) -> ops.StatusBase:  # noqa: D102
        return self.charm.unit.status

    def get_workload_version(self) -> str:  # noqa: D102
        return "42.todo"
        # todo verify this works
        return self.charm.unit._backend._run("application-version-get", return_output=True)

    def get_networks(self) -> Dict[str, scenario.Network]:  # noqa: D102
        networks = {}
        for endpoint, relations in self.charm.model.relations.items():
            for relation in relations:
                binding = self.charm.model.get_binding(relation)
                if not binding:
                    continue

                networks[endpoint] = scenario.Network.default(
                    interface_name=endpoint,
                    hostname="localhost",  # FIXME ???
                    egress_subnets=binding.network.egress_subnets,
                    ingress_addresses=binding.network.ingress_addresses,
                    # todo bind addrs
                )
        return networks

    def get_containers(self) -> List[scenario.Container]:  # noqa: D102
        # todo allow extracting specific parts of the filesystem?
        return [
            scenario.Container(name, can_connect=c.can_connect())
            for name, c in self.charm.unit.containers.items()
        ]

    def get_deferred(self) -> List[scenario.DeferredEvent]:  # noqa: D102
        return self._unit_state_db.get_deferred_events()

    def get_stored_state(self) -> List[scenario.StoredState]:  # noqa: D102
        return self._unit_state_db.get_stored_state()

    def get_secrets(self) -> List[scenario.Secret]:  # noqa: D102
        secrets = []
        be = self.charm.unit._backend
        for secret_id in cast(List[str], be._run("secret-ids", return_output=True, use_json=True)):
            info = cast(
                Dict[str, Any],
                be._run("secret-info-get", secret_id, return_output=True, use_json=True),
            )[secret_id]

            contents = cast(
                Dict[str, str], be._run("secret-get", secret_id, return_output=True, use_json=True)
            )
            revision = info["revision"]
            owner = info["owner"]
            rotation = info["rotation"]
            secrets.append(
                scenario.Secret(
                    secret_id,
                    contents={revision: contents},
                    owner="app" if owner == "application" else "unit",
                    rotate=rotation,
                )
            )
        return secrets

    def get_storage(self) -> List[scenario.Storage]:  # noqa: D102
        storages = []
        for storage_name, ops_storages in self.charm.model.storages.items():
            for storage in ops_storages:
                storages.append(scenario.Storage(name=storage_name, index=storage.index))
        return storages

    def get_resources(self) -> Dict[str, Path]:  # noqa: D102
        return {name: path for name, path in self.charm.model.resources._paths.items() if path}

    def get_relations(self) -> List["AnyRelation"]:  # noqa: D102
        relations = []
        for endpoint, ops_relations in self.charm.model.relations.items():
            for ops_relation in ops_relations:
                try:
                    relations.append(
                        scenario.Relation(
                            endpoint=endpoint,
                            relation_id=ops_relation.id,
                            remote_app_name=getattr(ops_relation.app, "name", "<unknown remote>"),
                            local_app_data=dict(ops_relation.data[self.charm.app]),
                            local_unit_data=dict(ops_relation.data[self.charm.unit]),
                            remote_app_data=dict(ops_relation.data[ops_relation.app]),  # type: ignore
                            remote_units_data={
                                int(remote_unit.name.split("/")[1]): dict(
                                    ops_relation.data[remote_unit]
                                )
                                for remote_unit in ops_relation.units
                            },
                        )
                    )
                except Exception as e:
                    logger.error(f"failed to snapshot relation {ops_relation}: {e}")
                # TODO implement subordinate, peers, and relations where you don't have access to all data.

        return relations


def get_state(charm: ops.CharmBase, show_secrets_insecure: bool = False) -> scenario.State:
    """Retrieve an as complete as possible scenario.State object from this charm."""
    backend = SnapshotBackend(charm)
    return scenario.State(
        config=backend.get_config(),
        relations=backend.get_relations(),
        containers=backend.get_containers(),
        opened_ports=backend.get_opened_ports(),
        leader=backend.get_leader(),
        model=backend.get_model(),
        secrets=backend.get_secrets() if show_secrets_insecure else [],
        resources=backend.get_resources(),  # type: ignore
        planned_units=backend.get_planned_units(),
        deferred=backend.get_deferred(),
        stored_state=backend.get_stored_state(),
        app_status=backend.get_app_status(),
        unit_status=backend.get_unit_status(),
        workload_version=backend.get_workload_version(),
        # FIXME: fails to json-serialize IPV4 object!
        # networks=backend.get_networks(),
        # FIXME: this just hangs and OOMs within minutes
        # storage=backend.get_storage(),
    )
