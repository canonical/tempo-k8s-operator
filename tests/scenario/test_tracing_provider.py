import pytest
from charms.tempo_k8s.v1.charm_tracing import charm_tracing_disabled
from charms.tempo_k8s.v2.tracing import ProtocolType, TracingProviderAppData
from scenario import Container, Context, Relation, State


@pytest.fixture
def context(tempo_charm):
    return Context(
        charm_type=tempo_charm,
    )


def test_receivers_removed_on_relation_broken(context):
    tracing_grpc = Relation(
        "tracing",
        remote_app_data={"receivers": '["otlp_grpc"]'},
        local_app_data={
            "receivers": '[{"protocol": {"name": "otlp_grpc", "type": "grpc"} , "url": "foo.com:10"}, '
            '{"protocol": {"name": "otlp_http", "type": "http"}, "url": "http://foo.com:11"}, '
        },
    )
    tracing_http = Relation(
        "tracing",
        remote_app_data={"receivers": '["otlp_http"]'},
        local_app_data={
            "receivers": '[{"protocol": {"name": "otlp_grpc", "type": "grpc"} , "url": "foo.com:10"}, '
            '{"protocol": {"name": "otlp_http", "type": "http"}, "url": "http://foo.com:11"}, '
        },
    )

    state = State(
        leader=True,
        relations=[tracing_grpc, tracing_http],
        containers=[Container("tempo", can_connect=False)],
    )

    with charm_tracing_disabled():
        with context.manager(tracing_grpc.broken_event, state) as mgr:
            charm = mgr.charm
            assert charm._requested_receivers() == ("otlp_http",)

    state_out = mgr.output
    r_out = [r for r in state_out.relations if r.relation_id == tracing_http.relation_id][0]
    # "otlp_grpc" is gone from the databag
    assert [r.protocol for r in TracingProviderAppData.load(r_out.local_app_data).receivers] == [
        ProtocolType(name="otlp_http", type="http")
    ]
