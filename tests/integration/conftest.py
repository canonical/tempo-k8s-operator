# Copyright 2021 Canonical Ltd.
# See LICENSE file for licensing details.
import logging
import os
import shutil
from pathlib import Path

import yaml
from pytest import fixture
from pytest_operator.plugin import OpsTest

logger = logging.getLogger(__name__)


@fixture(scope="module")
async def tempo_charm(ops_test: OpsTest):
    """Zinc charm used for integration testing."""
    charm = await ops_test.build_charm(".")
    return charm


@fixture(scope="module")
def tempo_metadata(ops_test: OpsTest):
    return yaml.safe_load(Path("./metadata.yaml").read_text())


@fixture(scope="module")
def tempo_oci_image(ops_test: OpsTest, tempo_metadata):
    return tempo_metadata["resources"]["tempo-image"]["upstream-source"]


@fixture(scope="module", autouse=True)
def copy_charm_libs_into_tester_charm(ops_test):
    """Ensure the tester charm has the libraries it uses."""
    libraries = [
        "observability_libs/v1/cert_handler.py",
        "tls_certificates_interface/v3/tls_certificates.py",
        "tempo_k8s/v1/charm_tracing.py",
        "tempo_k8s/v2/tracing.py",
    ]

    copies = []

    for lib in libraries:
        install_path = f"tests/integration/tester/lib/charms/{lib}"
        os.makedirs(os.path.dirname(install_path), exist_ok=True)
        shutil.copyfile(f"lib/charms/{lib}", install_path)
        copies.append(install_path)

    yield

    # cleanup: remove all libs
    for path in copies:
        Path(path).unlink()


@fixture(scope="module", autouse=True)
def copy_charm_libs_into_tester_grpc_charm(ops_test):
    """Ensure the tester GRPC charm has the libraries it uses."""
    libraries = [
        "tempo_k8s/v2/tracing.py",
    ]

    copies = []

    for lib in libraries:
        install_path = f"tests/integration/tester-grpc/lib/charms/{lib}"
        os.makedirs(os.path.dirname(install_path), exist_ok=True)
        shutil.copyfile(f"lib/charms/{lib}", install_path)
        copies.append(install_path)

    yield

    # cleanup: remove all libs
    for path in copies:
        Path(path).unlink()
