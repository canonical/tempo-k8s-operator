import asyncio
import logging
from pathlib import Path

import pytest
import yaml

from pytest_operator.plugin import OpsTest

METADATA = yaml.safe_load(Path("./metadata.yaml").read_text())
APP_NAME = METADATA["name"]
TESTER_METADATA = yaml.safe_load(Path("./tests/integration/tester/metadata.yaml").read_text())
TESTER_APP_NAME = TESTER_METADATA["name"]

logger = logging.getLogger(__name__)

@pytest.mark.abort_on_fail
async def test_build_and_deploy(ops_test: OpsTest):
    # Given a fresh build of the charm
    # When deploying it together with the tester and relating both charms
    # Then relation should eventually be created
    charms = await ops_test.build_charms(".", "./tests/integration/tester/")
    resources = {"tempo-image": METADATA["resources"]["tempo-image"]["upstream-source"]}
    resources_tester = {"workload": TESTER_METADATA["resources"]["workload"]["upstream-source"]}

    await asyncio.gather(
        ops_test.model.deploy(
            charms[APP_NAME], resources=resources, application_name=APP_NAME
        ),
        ops_test.model.deploy(
            charms[TESTER_APP_NAME], resources=resources_tester, application_name=TESTER_APP_NAME, num_units=3
        ),
    )

    await ops_test.model.wait_for_idle(
        apps=[APP_NAME, TESTER_APP_NAME],
        status="active",
        raise_on_blocked=True,
        timeout=1000
    )

    assert ops_test.model.applications[TESTER_APP_NAME].units[0].workload_status == "active"
    assert ops_test.model.applications[APP_NAME].units[0].workload_status == "active"

@pytest.mark.abort_on_fail
async def test_relate(ops_test: OpsTest):
    await ops_test.model.add_relation(APP_NAME + ":tracing", TESTER_APP_NAME + ":tracing")
    await ops_test.model.wait_for_idle([APP_NAME, TESTER_APP_NAME])

# TODO validate traces appear after relating

@pytest.mark.abort_on_fail
async def test_remove_relation(ops_test: OpsTest):
    await ops_test.juju("remove-relation", APP_NAME + ":tracing", TESTER_APP_NAME + ":tracing")
    await ops_test.model.wait_for_idle([APP_NAME, TESTER_APP_NAME], status="active")

