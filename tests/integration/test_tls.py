import asyncio
import json
import logging
import random
import tempfile
from pathlib import Path

import pytest
import requests
import yaml
from pytest_operator.plugin import OpsTest

from scripts.tracegen import emit_trace
from tests.integration.helpers import get_relation_data

METADATA = yaml.safe_load(Path("./charmcraft.yaml").read_text())
APP_NAME = "tempo"
SSC = "self-signed-certificates"
SSC_APP_NAME = "ssc"

logger = logging.getLogger(__name__)


@pytest.fixture(scope="function")
def nonce():
    """Generate an integer nonce for easier trace querying."""
    return str(random.random())[2:]


def get_traces(tempo_host: str, nonce, service_name="tracegen"):
    req = requests.get(tempo_host + ":3200/api/search", params={"service.name": service_name, "nonce": nonce})
    assert req.status_code == 200
    return json.loads(req.text)["traces"]


async def get_tempo_host(ops_test: OpsTest):
    status = await ops_test.model.get_status()
    app = status["applications"][APP_NAME]
    return app.public_address


@pytest.fixture(scope="function")
def server_cert():
    data = get_relation_data(requirer_endpoint=f"{APP_NAME}:certificates",
                             provider_endpoint=f"{SSC_APP_NAME}:certificates")
    cert = json.loads(data.provider.application_data['certificates'])['certificate']

    with tempfile.NamedTemporaryFile() as f:
        p = Path(f.name)
        p.write_text(cert)
        yield p


@pytest.mark.setup
@pytest.mark.abort_on_fail
async def test_build_and_deploy(ops_test: OpsTest):
    tempo_charm = await ops_test.build_charm(".")
    resources = {
        "tempo-image": METADATA["resources"]["tempo-image"]["upstream-source"],
    }
    await asyncio.gather(
        ops_test.model.deploy(tempo_charm, resources=resources, application_name=APP_NAME),
        ops_test.model.deploy(SSC, application_name=SSC_APP_NAME),
    )

    await asyncio.gather(
        ops_test.model.wait_for_idle(
            apps=[APP_NAME, SSC_APP_NAME],
            status="active",
            raise_on_blocked=True,
            timeout=10000,
            raise_on_error=False,
        ),
    )


@pytest.mark.setup
@pytest.mark.abort_on_fail
async def test_relate(ops_test: OpsTest):
    await ops_test.model.integrate(APP_NAME + ":certificates", SSC_APP_NAME + ":certificates")
    await ops_test.model.wait_for_idle(
        apps=[APP_NAME, SSC_APP_NAME],
        status="active",
        timeout=1000,
    )


async def test_verify_trace_http_no_tls_fails(ops_test: OpsTest, nonce):
    tempo_host = await get_tempo_host(ops_test)
    # IF tempo is related to SSC
    # WHEN we emit an http trace, **unsecured**
    emit_trace(tempo_host, nonce=nonce)  # this should fail
    # THEN we can verify it's not been ingested
    assert not get_traces(tempo_host, nonce=nonce)


async def test_verify_trace_http_tls(ops_test: OpsTest, nonce, server_cert):
    tempo_host = await get_tempo_host(ops_test)
    emit_trace(tempo_host, nonce=nonce, cert=server_cert)
    # THEN we can verify it's been ingested
    assert not get_traces(tempo_host, nonce=nonce)


async def test_verify_traces_grpc_tls(ops_test: OpsTest, nonce, server_cert):
    tempo_host = await get_tempo_host(ops_test)
    emit_trace(tempo_host, nonce=nonce, cert=server_cert, protocol="grpc")
    # THEN we can verify it's been ingested
    assert not get_traces(tempo_host, nonce=nonce)


@pytest.mark.teardown
@pytest.mark.abort_on_fail
async def test_remove_relation(ops_test: OpsTest):
    await ops_test.juju("remove-relation", APP_NAME + ":certificates", SSC_APP_NAME + ":certificates")
    await asyncio.gather(
        ops_test.model.wait_for_idle(
            apps=[APP_NAME], status="active", raise_on_blocked=True, timeout=1000
        ),
    )
