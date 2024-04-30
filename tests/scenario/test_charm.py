from pathlib import Path
from unittest.mock import MagicMock, patch, PropertyMock

import pytest
import yaml
from charms.tempo_k8s.v1.charm_tracing import charm_tracing_disabled
from charms.tempo_k8s.v2.tracing import TracingRequirerAppData
from ops import pebble
from scenario import Container, Mount, Relation, State
from scenario.sequences import check_builtin_sequences
from tempo import Tempo

TEMPO_CHARM_ROOT = Path(__file__).parent.parent.parent


@pytest.fixture(params=(True, False))
def base_state(request):
    return State(leader=request.param,
                 containers=[Container("tempo", can_connect=True)])


def test_builtin_sequences(tempo_charm, base_state):
    with charm_tracing_disabled():
        check_builtin_sequences(tempo_charm, template_state=base_state)


def test_start(context, base_state):
    # verify the charm runs at all with and without leadership
    with charm_tracing_disabled():
        context.run("start", base_state)


@pytest.mark.parametrize("requested_protocol", ("otlp_grpc", "zipkin"))
def test_tempo_restart_on_ingress_v2_changed(context, tmp_path, requested_protocol):
    # GIVEN
    # an initial configuration with an otlp_http receiver
    tempo_config = tmp_path / "tempo.yaml"

    container = MagicMock()
    container.can_connect = lambda: True
    # prevent tls_ready from reporting True
    container.exists = lambda path: False if path in [Tempo.tls_cert_path, Tempo.tls_key_path, Tempo.tls_ca_path] else True
    initial_config = Tempo(container).generate_config(["otlp_http"])

    tempo_config.write_text(yaml.safe_dump(initial_config))

    tempo = Container(
        "tempo",
        can_connect=True,
        layers={
            "tempo": pebble.Layer(
                {
                    "summary": "tempo layer",
                    "description": "foo",
                    "services": {
                        "tempo": {"startup": "enabled"},
                        "tempo-ready": {"startup": "disabled"},
                    },
                }
            )
        },
        service_status={
            # we don't have a way to check if the service has been restarted: all that scenario does ATM is set it to
            # 'active'.
            # so as a way to check that it's been restarted, we must set it to inactive here.
            "tempo": pebble.ServiceStatus.INACTIVE,
        },
        mounts={
            "data": Mount("/etc/tempo/tempo.yaml", tempo_config),
        },
    )

    # the remote end requests an otlp_grpc endpoint
    ingress = Relation(
        "tracing", remote_app_data=TracingRequirerAppData(receivers=[requested_protocol]).dump()
    )

    # WHEN
    # the charm receives an ingress(v2) relation-changed requesting an otlp_grpc receiver
    state = State(leader=True, containers=[tempo], relations=[ingress])
    context.run(ingress.changed_event, state)

    # THEN
    # Tempo pushes a new config to the container filesystem
    fs = tempo.get_filesystem(context)
    cfg_path = Path(str(fs) + Tempo.config_path)
    new_config = yaml.safe_load(cfg_path.read_text())
    expected_config = Tempo(container).generate_config(["otlp_http", requested_protocol])
    assert new_config == expected_config
    # AND restarts the pebble service.
    assert (
        context.output_state.get_container("tempo").service_status["tempo"]
        is pebble.ServiceStatus.ACTIVE
    )
