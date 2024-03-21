"""Snapshot charm library.

This library acts as a bridge to scenario's
[State](https://github.com/canonical/ops-scenario/blob/main/scenario/state.py#L861) data structure,
allowing you to capture snapshots of a live charm's current state and serialize them as json.
"""
import logging
from dataclasses import asdict, fields, is_dataclass
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict, List, cast

import ops
import scenario
import scenario.runtime
from ops import SecretRotate, pebble
from scenario.state import _EntityStatus

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
        # FIXME: fails to json-serialize IPV4 object!
        # networks=backend.get_networks(),
        # FIXME: this just hangs and OOMs within minutes
        # storage=backend.get_storage(),
    )


def _relation_to_dict(value: "AnyRelation") -> Dict:
    dct = asdict(value)
    dct["relation_type"] = type(value).__name__
    return dct


def state_to_dict(state: scenario.State) -> Dict:
    """Serialize ``scenario.State`` to a jsonifiable dict."""
    out = {}
    for f in fields(state):
        key = f.name
        raw_value = getattr(state, f.name)
        if key == "networks":
            serialized_value = {name: asdict(network) for name, network in raw_value.items()}
        elif key == "relations":
            serialized_value = [_relation_to_dict(r) for r in raw_value]
        else:
            if isinstance(raw_value, list):
                serialized_value = [asdict(raw_obj) for raw_obj in raw_value]
            elif is_dataclass(raw_value):
                serialized_value = asdict(raw_value)
            else:
                serialized_value = raw_value

        out[key] = serialized_value
    return out


def _dict_to_status(value: Dict) -> _EntityStatus:
    return _EntityStatus(**value)


def _dict_to_model(value: Dict) -> scenario.Model:
    return scenario.Model(**value)


def _dict_to_relation(value: Dict) -> "AnyRelation":
    relation_type = value.pop("relation_type")
    if relation_type == "Relation":
        return scenario.Relation(**value)
    if relation_type == "PeerRelation":
        return scenario.PeerRelation(**value)
    if relation_type == "SubordinateRelation":
        return scenario.SubordinateRelation(**value)
    raise TypeError(value)


def _dict_to_address(value: Dict) -> scenario.Address:
    return scenario.Address(**value)


def _dict_to_bindaddress(value: Dict) -> scenario.BindAddress:
    if addrs := value.get("addresses"):
        value["addresses"] = [_dict_to_address(addr) for addr in addrs]
    return scenario.BindAddress(**value)


def _dict_to_network(value: Dict) -> scenario.Network:
    if addrs := value.get("bind_addresses"):
        value["bind_addresses"] = [_dict_to_bindaddress(addr) for addr in addrs]
    return scenario.Network(**value)


def _dict_to_container(value: Dict) -> scenario.Container:
    if layers := value.get("layers"):
        value["layers"] = {l_name: pebble.Layer(l_raw) for l_name, l_raw in layers.items()}
    return scenario.Container(**value)


def _dict_to_opened_port(value: Dict) -> scenario.Port:
    return scenario.Port(**value)


def _dict_to_secret(value: Dict) -> scenario.Secret:
    if rotate := value.get("rotate"):
        value["rotate"] = SecretRotate(rotate)
    if expire := value.get("expire"):
        value["expire"] = datetime.fromisoformat(expire)
    return scenario.Secret(**value)


def _dict_to_stored_state(value: Dict) -> scenario.StoredState:
    return scenario.StoredState(**value)


def _dict_to_deferred(value: Dict) -> scenario.DeferredEvent:
    return scenario.DeferredEvent(**value)


def _dict_to_storage(value: Dict) -> scenario.Storage:
    return scenario.Storage(**value)


def dict_to_state(state_json: Dict) -> scenario.State:  # noqa: C901
    """Deserialize a jsonified state back to a scenario.State."""
    overrides = {}
    for key, value in state_json.items():
        if key in [
            "leader",
            "config",
            "planned_units",
            "unit_id",
            "workload_version",
        ]:  # all state components that can be used as-is
            overrides[key] = value
        elif key in [
            "app_status",
            "unit_status",
        ]:  # all state components that can be used as-is
            overrides[key] = _dict_to_status(value)
        elif key == "model":
            overrides[key] = _dict_to_model(value)
        elif key == "relations":
            overrides[key] = [_dict_to_relation(obj) for obj in value]
        elif key == "networks":
            overrides[key] = {name: _dict_to_network(obj) for name, obj in value.items()}
        elif key == "resources":
            overrides[key] = {name: Path(obj) for name, obj in value.items()}
        elif key == "containers":
            overrides[key] = [_dict_to_container(obj) for obj in value]
        elif key == "storage":
            overrides[key] = [_dict_to_storage(obj) for obj in value]
        elif key == "opened_ports":
            overrides[key] = [_dict_to_opened_port(obj) for obj in value]
        elif key == "secrets":
            overrides[key] = [_dict_to_secret(obj) for obj in value]
        elif key == "stored_state":
            overrides[key] = [_dict_to_stored_state(obj) for obj in value]
        elif key == "deferred":
            overrides[key] = [_dict_to_deferred(obj) for obj in value]
        else:
            raise KeyError(key)

    return scenario.State().replace(**overrides)
