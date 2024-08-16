import asyncio
import json
import logging
import random
import tempfile
from pathlib import Path
from subprocess import getoutput

import pytest
import requests
import yaml
from pytest_operator.plugin import OpsTest
from tenacity import retry, stop_after_attempt, wait_exponential

from tempo import Tempo
from tests.integration.helpers import get_relation_data

METADATA = yaml.safe_load(Path("./charmcraft.yaml").read_text())
APP_NAME = "tempo"
SSC = "self-signed-certificates"
SSC_APP_NAME = "ssc"
TRACEGEN_SCRIPT_PATH = Path() / "scripts" / "tracegen.py"
logger = logging.getLogger(__name__)


@pytest.fixture(scope="function")
def nonce():
    """Generate an integer nonce for easier trace querying."""
    return str(random.random())[2:]


def get_traces(tempo_host: str, nonce):
    url = "https://" + tempo_host + ":3200/api/search"
    req = requests.get(
        url,
        params={"q": f'{{ .nonce = "{nonce}" }}'},
        # it would fail to verify as the cert was issued for fqdn, not IP.
        verify=False,
    )
    assert req.status_code == 200
    return json.loads(req.text)["traces"]


@retry(stop=stop_after_attempt(5), wait=wait_exponential(multiplier=1, min=4, max=10))
async def get_traces_patiently(ops_test, nonce):
    assert get_traces(await get_tempo_ip(ops_test), nonce=nonce)


async def get_tempo_ip(ops_test: OpsTest):
    status = await ops_test.model.get_status()
    app = status["applications"][APP_NAME]
    return app.public_address


async def get_tempo_internal_host(ops_test: OpsTest):
    return f"https://{APP_NAME}-0.{APP_NAME}-endpoints.{ops_test.model.name}.svc.cluster.local"


@pytest.fixture(scope="function")
def server_cert(ops_test: OpsTest):
    data = get_relation_data(
        requirer_endpoint=f"{APP_NAME}/0:certificates",
        provider_endpoint=f"{SSC_APP_NAME}/0:certificates",
        model=ops_test.model.name,
    )
    cert = json.loads(data.provider.application_data["certificates"])[0]["certificate"]

    with tempfile.NamedTemporaryFile() as f:
        p = Path(f.name)
        p.write_text(cert)
        yield p


async def emit_trace(ops_test: OpsTest, nonce, proto: str = "http", verbose=0, use_cert=False):
    """Use juju ssh to run tracegen from the tempo charm; to avoid any DNS issues."""
    hostname = await get_tempo_internal_host(ops_test)
    cmd = (
        f"juju ssh -m {ops_test.model_name} {APP_NAME}/0 "
        f"TRACEGEN_ENDPOINT={hostname}:4318/v1/traces "
        f"TRACEGEN_VERBOSE={verbose} "
        f"TRACEGEN_PROTOCOL={proto} "
        f"TRACEGEN_CERT={Tempo.server_cert_path if use_cert else ''} "
        f"TRACEGEN_NONCE={nonce} "
        "python3 tracegen.py"
    )

    return getoutput(cmd)


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


@pytest.mark.setup
@pytest.mark.abort_on_fail
async def test_push_tracegen_script_and_deps(ops_test: OpsTest):
    await ops_test.juju("scp", TRACEGEN_SCRIPT_PATH, f"{APP_NAME}/0:tracegen.py")
    await ops_test.juju(
        "ssh",
        f"{APP_NAME}/0",
        "python3 -m pip install opentelemetry-exporter-otlp-proto-grpc opentelemetry-exporter-otlp-proto-http",
    )


async def test_verify_trace_http_no_tls_fails(ops_test: OpsTest, server_cert, nonce):
    # IF tempo is related to SSC
    # WHEN we emit an http trace, **unsecured**
    await emit_trace(ops_test, nonce=nonce)  # this should fail
    # THEN we can verify it's not been ingested
    tempo_ip = await get_tempo_ip(ops_test)
    traces = get_traces(tempo_ip, nonce=nonce)
    assert not traces


async def test_verify_trace_http_tls(ops_test: OpsTest, nonce, server_cert):
    # WHEN we emit a trace secured with TLS
    await emit_trace(ops_test, nonce=nonce, use_cert=True)
    # THEN we can verify it's eventually ingested
    await get_traces_patiently(ops_test, nonce)


@pytest.mark.xfail  # expected to fail because in this context the grpc receiver is not enabled
async def test_verify_traces_grpc_tls(ops_test: OpsTest, nonce, server_cert):
    # WHEN we emit a trace secured with TLS
    await emit_trace(ops_test, nonce=nonce, verbose=1, proto="grpc", use_cert=True)
    # THEN we can verify it's been ingested
    await get_traces_patiently(ops_test, nonce)


@pytest.mark.teardown
@pytest.mark.abort_on_fail
async def test_remove_relation(ops_test: OpsTest):
    await ops_test.juju(
        "remove-relation", APP_NAME + ":certificates", SSC_APP_NAME + ":certificates"
    )
    await asyncio.gather(
        ops_test.model.wait_for_idle(
            apps=[APP_NAME], status="active", raise_on_blocked=True, timeout=1000
        ),
    )
