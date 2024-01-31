# Copyright 2022 Canonical Ltd.
# See LICENSE file for licensing details.
from interface_tester import InterfaceTester


def test_tracing_v0_interface(interface_tester: InterfaceTester):
    interface_tester.configure(
        interface_name="tracing",
        interface_version=0,
        branch="tracing-v2",  # todo remove when CRI:tracing-v2 is merged
    )
    interface_tester.run()


def test_tracing_v2_interface(interface_tester: InterfaceTester):
    interface_tester.configure(
        interface_name="tracing",
        interface_version=2,
        branch="tracing-v2",  # todo remove when CRI:tracing-v2 is merged
    )
    interface_tester.run()
