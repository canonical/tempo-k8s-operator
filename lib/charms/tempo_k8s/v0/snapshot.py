"""Snapshot charm library.

This library acts as a bridge to scenario's
[State](https://github.com/canonical/ops-scenario/blob/main/scenario/state.py#L861) data structure,
allowing you to capture snapshots of a live charm's current state and serialize them as json.
"""
from typing import Dict, Any, List, cast

import ops
import scenario
import scenario.runtime

# The unique Charmhub library identifier, never change it
LIBID = "012181337a3a4717861b5fb4d8bdfc2b"

# Increment this major API version when introducing breaking changes
LIBAPI = 0

# Increment this PATCH version before using `charmcraft publish-lib` or reset
# to 0 if you are raising the major API version
LIBPATCH = 1

PYDEPS = ["ops-scenario>=6"]


class SnapshotBackend:
    def __init__(self, charm: ops.CharmBase):
        self.charm = charm
        self._unit_state_db = scenario.runtime.UnitStateDB(charm.charm_dir / '.unit-state.db')

    def get_config(self):
        return dict(self.charm.config)

    def get_opened_ports(self):
        return [scenario.Port(p.protocol, p.port) for p in self.charm.unit.opened_ports()]

    def get_leader(self):
        return self.charm.unit.is_leader()

    def get_model(self):
        return scenario.Model(
            name=self.charm.model.name,
            uuid=self.charm.model.uuid,
            # fixme: how do we determine if we're on a kubernetes or machine model?
        )

    def get_planned_units(self):
        return self.charm.app.planned_units()

    def get_app_status(self):
        return self.charm.app.status

    def get_unit_status(self):
        return self.charm.unit.status

    def get_workload_version(self):
        # todo verify this works
        return self.charm.unit._backend._run('application-version-get', return_output=True)

    def get_networks(self):
        networks = []
        for endpoint, relations in self.charm.model.relations.items():
            for relation in relations:
                binding = self.charm.model.get_binding(relation)
                networks.append(scenario.Network.default(
                    interface_name=endpoint,
                    hostname='localhost',  # FIXME ???
                    egress_subnets=binding.network.egress_subnets,
                    ingress_addresses=binding.network.ingress_addresses,
                    # todo bind addrs
                )
                )

    def get_containers(self):
        # todo allow extracting specific parts of the filesystem?
        return [scenario.Container(name, can_connect=c.can_connect()) for name, c in self.charm.unit.containers.items()]

    def get_deferred(self):
        return self._unit_state_db.get_deferred_events()

    def get_stored_state(self):
        return self._unit_state_db.get_stored_state()

    def get_secrets(self):
        secrets = []
        be = self.charm.unit._backend
        for secret_id in cast(List[str], be._run("secret-ids", return_output=True, use_json=True)):
            info = cast(Dict[str, Any],
                        be._run("secret-info-get", secret_id, return_output=True, use_json=True)[secret_id])
            contents = cast(Dict[str, str], be._run("secret-get", secret_id, return_output=True, use_json=True))
            revision = info['revision']
            owner = info['owner']
            rotation = info['rotation']
            secrets.append(
                scenario.Secret(
                    secret_id,
                    contents={revision: contents},
                    owner="app" if owner == "application" else "unit",
                    rotate=rotation)
            )
        return secrets

    def get_storage(self):
        storages = []
        for storage_name, storages in self.charm.model.storages.items():
            for storage in storages:
                storages.append(scenario.Storage(
                    name=storage_name,
                    index=storage.index
                ))
        return storages

    def get_resources(self):
        return self.charm.model.resources._paths


def get_state(charm: ops.CharmBase,
              show_secrets_insecure: bool = False) -> scenario.State:
    backend = SnapshotBackend(charm)
    return scenario.State(
        config=backend.get_config(),
        relations=backend.get_relations(),
        networks=backend.get_networks(),
        containers=backend.get_containers(),
        storage=backend.get_storage(),
        opened_ports=backend.get_opened_ports(),
        leader=backend.get_leader(),
        model=backend.get_model(),
        secrets=backend.get_secrets() if show_secrets_insecure else [],
        resources=backend.get_resources(),
        planned_units=backend.get_planned_units(),
        deferred=backend.get_deferred(),
        stored_state=backend.get_stored_state(),
        app_status=backend.get_app_status(),
        unit_status=backend.get_unit_status(),
        workload_version=backend.get_workload_version(),
    )
