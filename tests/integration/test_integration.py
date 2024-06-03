import asyncio
import json
import logging
from pathlib import Path

import pytest
import yaml
from pytest_operator.plugin import OpsTest

METADATA = yaml.safe_load(Path("./charmcraft.yaml").read_text())
APP_NAME = METADATA["name"]
TESTER_METADATA = yaml.safe_load(Path("./tests/integration/tester/metadata.yaml").read_text())
TESTER_APP_NAME = TESTER_METADATA["name"]
TESTER_GRPC_METADATA = yaml.safe_load(
    Path("./tests/integration/tester-grpc/metadata.yaml").read_text()
)
TESTER_GRPC_APP_NAME = TESTER_GRPC_METADATA["name"]

logger = logging.getLogger(__name__)


@pytest.mark.setup
@pytest.mark.abort_on_fail
async def test_build_and_deploy(ops_test: OpsTest):
    # Given a fresh build of the charm
    # When deploying it together with testers
    # Then applications should eventually be created
    tempo_charm = await ops_test.build_charm(".")
    tester_charm = await ops_test.build_charm("./tests/integration/tester/")
    tester_grpc_charm = await ops_test.build_charm("./tests/integration/tester-grpc/")
    resources = {
        "tempo-image": METADATA["resources"]["tempo-image"]["upstream-source"],
    }
    resources_tester = {"workload": TESTER_METADATA["resources"]["workload"]["upstream-source"]}
    resources_tester_grpc = {
        "workload": TESTER_GRPC_METADATA["resources"]["workload"]["upstream-source"]
    }

    await asyncio.gather(
        ops_test.model.deploy(tempo_charm, resources=resources, application_name=APP_NAME),
        ops_test.model.deploy(
            tester_charm,
            resources=resources_tester,
            application_name=TESTER_APP_NAME,
            num_units=3,
        ),
        ops_test.model.deploy(
            tester_grpc_charm,
            resources=resources_tester_grpc,
            application_name=TESTER_GRPC_APP_NAME,
            num_units=3,
        ),
    )

    await asyncio.gather(
        ops_test.model.wait_for_idle(
            apps=[APP_NAME],
            status="active",
            raise_on_blocked=True,
            timeout=10000,
            raise_on_error=False,
        ),
        # for tester, depending on the result of race with tempo it's either waiting or active
        ops_test.model.wait_for_idle(
            apps=[TESTER_APP_NAME], raise_on_blocked=True, timeout=1000, raise_on_error=False
        ),
        ops_test.model.wait_for_idle(
            apps=[TESTER_GRPC_APP_NAME], raise_on_blocked=True, timeout=1000, raise_on_error=False
        ),
    )

    assert ops_test.model.applications[APP_NAME].units[0].workload_status == "active"


@pytest.mark.setup
@pytest.mark.abort_on_fail
async def test_relate(ops_test: OpsTest):
    # given a deployed charm
    # when relating it together with the tester
    # then relation should appear
    await ops_test.model.add_relation(APP_NAME + ":tracing", TESTER_APP_NAME + ":tracing")
    await ops_test.model.add_relation(APP_NAME + ":tracing", TESTER_GRPC_APP_NAME + ":tracing")
    await ops_test.model.wait_for_idle(
        apps=[APP_NAME, TESTER_APP_NAME, TESTER_GRPC_APP_NAME],
        status="active",
        timeout=1000,
    )


async def test_verify_traces_http(ops_test: OpsTest):
    # given a relation between charms
    # when traces endpoint is queried
    # then it should contain traces from tester charm
    status = await ops_test.model.get_status()
    app = status["applications"][APP_NAME]
    logger.info(app.public_address)
    endpoint = app.public_address + ":3200/api/search"
    cmd = [
        "curl",
        endpoint,
    ]
    rc, stdout, stderr = await ops_test.run(*cmd)
    logger.info("%s: %s", endpoint, (rc, stdout, stderr))
    assert rc == 0, (
        f"curl exited with rc={rc} for {endpoint}; "
        f"non-zero return code means curl encountered a >= 400 HTTP code; "
        f"cmd={cmd}"
    )
    traces = json.loads(stdout)["traces"]

    found = False
    for trace in traces:
        if trace["rootServiceName"] == APP_NAME and trace["rootTraceName"] == "charm exec":
            found = True

    assert found, f"There's no trace of charm exec traces in tempo. {json.dumps(traces, indent=2)}"


async def test_verify_traces_grpc(ops_test: OpsTest):
    # the tester-grpc charm emits a single grpc trace in its common exit hook
    # we verify it's there
    status = await ops_test.model.get_status()
    app = status["applications"][APP_NAME]
    logger.info(app.public_address)
    endpoint = app.public_address + ":3200/api/search"
    cmd = [
        "curl",
        endpoint,
    ]
    rc, stdout, stderr = await ops_test.run(*cmd)
    logger.info("%s: %s", endpoint, (rc, stdout, stderr))
    assert rc == 0, (
        f"curl exited with rc={rc} for {endpoint}; "
        f"non-zero return code means curl encountered a >= 400 HTTP code; "
        f"cmd={cmd}"
    )
    traces = json.loads(stdout)["traces"]

    found = False
    for trace in traces:
        if trace["rootServiceName"] == "TempoTesterGrpcCharm":
            found = True

    assert (
        found
    ), f"There's no trace of generated grpc traces in tempo. {json.dumps(traces, indent=2)}"


@pytest.mark.teardown
@pytest.mark.abort_on_fail
async def test_remove_relation(ops_test: OpsTest):
    # given related charms
    # when relation is removed
    # then both charms should become active again
    await ops_test.juju("remove-relation", APP_NAME + ":tracing", TESTER_APP_NAME + ":tracing")
    await ops_test.juju(
        "remove-relation", APP_NAME + ":tracing", TESTER_GRPC_APP_NAME + ":tracing"
    )
    await asyncio.gather(
        ops_test.model.wait_for_idle(
            apps=[APP_NAME], status="active", raise_on_blocked=True, timeout=1000
        ),
        # for tester, depending on the result of race with tempo it's either waiting or active
        ops_test.model.wait_for_idle(apps=[TESTER_APP_NAME], raise_on_blocked=True, timeout=1000),
        ops_test.model.wait_for_idle(
            apps=[TESTER_GRPC_APP_NAME], raise_on_blocked=True, timeout=1000
        ),
    )
