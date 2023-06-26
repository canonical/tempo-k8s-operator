# Copyright 2022 Canonical Ltd.
# See LICENSE file for licensing details.
from interface_tester import InterfaceTester


def test_ingress_v1_interface(interface_tester: InterfaceTester):
    interface_tester.configure(
        interface_name="tracing",
        interface_version=0,
        repo="https://github.com/PietroPasotti/charm-relation-interfaces",
        branch="tracing",
    )
    interface_tester.run()
