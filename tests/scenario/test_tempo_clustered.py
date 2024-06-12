import datetime
from unittest.mock import patch

import pytest
import scenario

from charm import TempoCharm
from charms.tls_certificates_interface.v3.tls_certificates import ProviderCertificate
from tempo_cluster import TempoClusterProviderAppData


@pytest.fixture
def cluster():
    return scenario.Relation("tempo-cluster")


@pytest.fixture
def s3():
    return scenario.Relation(
        "s3",
        remote_app_data={
            "access-key": "key",
            "bucket": "tempo",
            "endpoint": "http://1.2.3.4:9000",
            "secret-key": "soverysecret",
        },
        local_unit_data={"bucket": "tempo"},
    )


@pytest.fixture
def certs_relation():
    return scenario.Relation("certificates")


#
# @pytest.fixture
# def certs_secret():
#     return scenario.Secret(
#         "certificates-secret-id",
#         owner="app",
#         contents={0:
#             {
#                 "private-key": "supersecretkey",
#                 "ca-cert": "CA_CERT-foo",
#                 "server-cert": "SERVER_CERT-foo",
#             }},
#     )


@pytest.fixture
def mock_tempo_workload_ready():
    with patch("tempo.Tempo.is_ready", new=lambda _: True):
        yield


@pytest.fixture
def tempo_container_ready(context, mock_tempo_workload_ready):
    tempo = scenario.Container("tempo", can_connect=True)
    return context.run(
        tempo.pebble_ready_event,
        scenario.State(
            leader=True,
            containers=[tempo]
        )).get_container("tempo")


MOCK_SERVER_CERT = "SERVER_CERT-foo"
MOCK_CA_CERT = "CA_CERT-foo"


@pytest.fixture(autouse=True)
def patch_certs():
    cert = ProviderCertificate(
        relation_id=42,
        application_name="tempo",
        csr="CSR",
        certificate=MOCK_SERVER_CERT,
        ca=MOCK_CA_CERT,
        chain=[],
        revoked=False,
        expiry_time=datetime.datetime(2050, 4, 1),
    )
    with patch("charms.observability_libs.v1.cert_handler.CertHandler.get_cert", new=lambda _: cert):
        yield

@pytest.fixture
def state_with_certs(context, tempo_container_ready, s3, certs_relation):
    return context.run(certs_relation.joined_event, scenario.State(
        leader=True,
        containers=[tempo_container_ready],
        relations=[s3, certs_relation]
    ))


def test_certs_ready(context, state_with_certs):
    with context.manager("update-status", state_with_certs) as mgr:
        charm: TempoCharm = mgr.charm
        assert charm.cert_handler.server_cert == MOCK_SERVER_CERT
        assert charm.cert_handler.ca_cert == MOCK_CA_CERT
        assert charm.cert_handler.private_key


def test_cluster_relation(context, state_with_certs, cluster):
    clustered_state = state_with_certs.replace(
        relations=state_with_certs.relations + [cluster]
    )

    state_out = context.run(cluster.joined_event, clustered_state)
    cluster_out = state_out.get_relations(cluster.endpoint)[0]
    local_app_data = TempoClusterProviderAppData.load(cluster_out.local_app_data)

    assert local_app_data.ca_cert == MOCK_CA_CERT
    assert local_app_data.server_cert == MOCK_SERVER_CERT
    secret = [s for s in state_out.secrets if s.id == local_app_data.privkey_secret_id][0]

    # certhandler's vault uses revision 0 to store an uninitialized-vault marker
    assert secret.contents[1]['private-key']

    assert local_app_data.tempo_config