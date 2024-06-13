import json
import logging
import shlex
import tempfile
from pathlib import Path
from subprocess import run
from typing import Dict, Literal

import pytest
import yaml
from juju.application import Application
from pytest_operator.plugin import OpsTest

METADATA = yaml.safe_load(Path("./charmcraft.yaml").read_text())
APP_NAME = "tempo"
FACADE = "facade"
TRACEGEN_SCRIPT_PATH = Path() / "scripts" / "tracegen.py"

logger = logging.getLogger(__name__)


@pytest.mark.setup
@pytest.mark.abort_on_fail
async def test_deploy_tempo(ops_test: OpsTest):
    tempo_charm = await ops_test.build_charm(".")
    resources = {"tempo-image": METADATA["resources"]["tempo-image"]["upstream-source"]}
    await ops_test.model.deploy(tempo_charm, resources=resources, application_name=APP_NAME),

    await ops_test.model.wait_for_idle(
        apps=[APP_NAME],
        # tempo might be in waiting as it waits for tempo workload ready
        raise_on_blocked=True,
        timeout=10000,
        raise_on_error=False,
    )


@pytest.mark.abort_on_fail
async def test_scale_tempo_up_without_s3_blocks(ops_test: OpsTest):
    app: Application = ops_test.model.applications[APP_NAME]
    await app.add_unit(1)

    await ops_test.model.wait_for_idle(
        apps=[APP_NAME],
        status="blocked",
        raise_on_blocked=False,
        timeout=1000,
    )


def present_facade(
    interface: str,
    app_data: Dict = None,
    unit_data: Dict = None,
    role: Literal["provide", "require"] = "provide",
    model: str = None,
    app: str = "facade",
):
    """Set up the facade charm to present this data over the interface ``interface``."""
    data = {
        "endpoint": f"{role}-{interface}",
    }
    if app_data:
        data["app_data"] = json.dumps(app_data)
    if unit_data:
        data["unit_data"] = json.dumps(unit_data)

    with tempfile.NamedTemporaryFile() as f:
        fpath = Path(f.name)
        fpath.write_text(yaml.safe_dump(data))

        _model = f" --model {model}" if model else ""
        run(shlex.split(f"juju run {app}/0{_model} --params {fpath.absolute()}"))


@pytest.mark.setup
@pytest.mark.abort_on_fail
async def test_tempo_active_when_deploy_s3_facade(ops_test: OpsTest):
    await ops_test.model.deploy(FACADE, channel="edge")
    await ops_test.model.integrate(APP_NAME + ":s3", FACADE + ":provide-s3")

    present_facade(
        "s3",
        model=ops_test.model_name,
        app_data={
            "access-key": "key",
            "bucket": "tempo",
            "endpoint": "http://1.2.3.4:9000",
            "secret-key": "soverysecret",
        },
    )

    await ops_test.model.wait_for_idle(
        apps=[FACADE],
        raise_on_blocked=True,
        status="active",
        timeout=2000,
    )

    await ops_test.model.wait_for_idle(
        apps=[APP_NAME],
        # we can't raise on blocked as tempo will likely start at blocked
        raise_on_blocked=False,
        # we can't wait for a specific status as tempo
        # might quickly go from waiting to active depending on when the notice comes in
        timeout=1000,
    )


@pytest.mark.teardown
async def test_tempo_blocks_if_s3_goes_away(ops_test: OpsTest):
    app: Application = ops_test.model.applications[FACADE]
    await app.destroy(destroy_storage=True)
    await ops_test.model.wait_for_idle(
        apps=[APP_NAME],
        raise_on_blocked=False,
        status="blocked",
        timeout=1000,
    )
