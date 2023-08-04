# Copyright 2022 Canonical Ltd.
# See LICENSE file for licensing details.
from interface_tester import InterfaceTester


def test_tracing_v0_interface(interface_tester: InterfaceTester):
    interface_tester.configure(
        interface_name="tracing",
        interface_version=0,
        repo="https://github.com/canonical/charm-relation-interfaces",
        branch="main",
    )
    interface_tester.run()
