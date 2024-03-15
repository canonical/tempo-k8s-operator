import json
import logging
import os
from unittest.mock import patch

import ops
import pytest
import scenario
from charms.tempo_k8s.v1.charm_tracing import CHARM_TRACING_ENABLED, dict_to_state
from charms.tempo_k8s.v1.charm_tracing import _autoinstrument as autoinstrument
from ops import EventBase, EventSource, Framework
from ops.charm import CharmBase, CharmEvents
from scenario import Context, State

from lib.charms.tempo_k8s.v1.charm_tracing import get_current_span, trace

os.environ[CHARM_TRACING_ENABLED] = "1"

logger = logging.getLogger(__name__)


@pytest.fixture(autouse=True)
def cleanup():
    # if any other test module disabled it...
    os.environ[CHARM_TRACING_ENABLED] = "1"

    def patched_set_tracer_provider(tracer_provider, log):
        import opentelemetry

        opentelemetry.trace._TRACER_PROVIDER = tracer_provider

    with patch("opentelemetry.trace._set_tracer_provider", new=patched_set_tracer_provider):
        yield


class MyCharmSimple(CharmBase):
    META = {"name": "frank"}

    @property
    def tempo(self):
        return "foo.bar:80"


autoinstrument(MyCharmSimple, MyCharmSimple.tempo)


def test_base_tracer_endpoint(caplog):
    import opentelemetry

    with patch(
        "opentelemetry.exporter.otlp.proto.http.trace_exporter.OTLPSpanExporter.export"
    ) as f:
        f.return_value = opentelemetry.sdk.trace.export.SpanExportResult.SUCCESS
        ctx = Context(MyCharmSimple, meta=MyCharmSimple.META)
        ctx.run("start", State())
        assert "Setting up span exporter to endpoint: foo.bar:80" in caplog.text
        assert "Starting root trace with id=" in caplog.text
        span = f.call_args_list[0].args[0][0]
        assert span.resource.attributes["service.name"] == "frank-0"
        assert span.resource.attributes["compose_service"] == "frank-0"
        assert span.resource.attributes["charm_type"] == "MyCharmSimple"


class SubObject:
    def foo(self):
        return "bar"


class MyCharmSubObject(CharmBase):
    META = {"name": "frank"}

    def __init__(self, framework: Framework):
        super().__init__(framework)
        self.subobj = SubObject()
        framework.observe(self.on.start, self._on_start)

    def _on_start(self, _):
        self.subobj.foo()

    @property
    def tempo(self):
        return "foo.bar:80"


autoinstrument(MyCharmSubObject, MyCharmSubObject.tempo, extra_types=[SubObject])


def test_subobj_tracer_endpoint(caplog):
    import opentelemetry

    with patch(
        "opentelemetry.exporter.otlp.proto.http.trace_exporter.OTLPSpanExporter.export"
    ) as f:
        f.return_value = opentelemetry.sdk.trace.export.SpanExportResult.SUCCESS
        ctx = Context(MyCharmSubObject, meta=MyCharmSubObject.META)
        ctx.run("start", State())
        spans = f.call_args_list[0].args[0]
        assert spans[0].name == "method call: SubObject.foo"


class MyCharmInitAttr(CharmBase):
    META = {"name": "frank"}

    def __init__(self, framework: Framework):
        super().__init__(framework)
        self._tempo = "foo.bar:80"

    @property
    def tempo(self):
        return self._tempo


autoinstrument(MyCharmInitAttr, MyCharmInitAttr.tempo)


def test_init_attr(caplog):
    import opentelemetry

    with patch(
        "opentelemetry.exporter.otlp.proto.http.trace_exporter.OTLPSpanExporter.export"
    ) as f:
        f.return_value = opentelemetry.sdk.trace.export.SpanExportResult.SUCCESS
        ctx = Context(MyCharmInitAttr, meta=MyCharmInitAttr.META)
        ctx.run("start", State())
        assert "Setting up span exporter to endpoint: foo.bar:80" in caplog.text
        span = f.call_args_list[0].args[0][0]
        assert span.resource.attributes["service.name"] == "frank-0"
        assert span.resource.attributes["compose_service"] == "frank-0"
        assert span.resource.attributes["charm_type"] == "MyCharmInitAttr"


class MyCharmSimpleDisabled(CharmBase):
    META = {"name": "frank"}

    @property
    def tempo(self):
        return None


autoinstrument(MyCharmSimpleDisabled, MyCharmSimpleDisabled.tempo)


def test_base_tracer_endpoint_disabled(caplog):
    import opentelemetry

    with patch(
        "opentelemetry.exporter.otlp.proto.http.trace_exporter.OTLPSpanExporter.export"
    ) as f:
        f.return_value = opentelemetry.sdk.trace.export.SpanExportResult.SUCCESS
        ctx = Context(MyCharmSimpleDisabled, meta=MyCharmSimpleDisabled.META)
        ctx.run("start", State())

        assert "Charm tracing is disabled." in caplog.text
        assert not f.called


@trace
def _my_fn(foo):
    return foo + 1


class MyCharmSimpleEvent(CharmBase):
    META = {"name": "frank"}

    def __init__(self, fw):
        super().__init__(fw)
        span = get_current_span()
        assert span is None  # can't do that in init.
        fw.observe(self.on.start, self._on_start)

    def _on_start(self, _):
        span = get_current_span()
        span.add_event(
            "log",
            {
                "foo": "bar",
                "baz": "qux",
            },
        )
        _my_fn(2)

    @property
    def tempo(self):
        return "foo.bar:80"


autoinstrument(MyCharmSimpleEvent, MyCharmSimpleEvent.tempo)


def test_base_tracer_endpoint_event(caplog):
    import opentelemetry

    with patch(
        "opentelemetry.exporter.otlp.proto.http.trace_exporter.OTLPSpanExporter.export"
    ) as f:
        f.return_value = opentelemetry.sdk.trace.export.SpanExportResult.SUCCESS
        ctx = Context(MyCharmSimpleEvent, meta=MyCharmSimpleEvent.META)
        ctx.run("start", State())

        spans = f.call_args_list[0].args[0]
        span0, span1, span2, span3 = spans
        assert span0.name == "function call: _my_fn"

        assert span1.name == "method call: MyCharmSimpleEvent._on_start"

        assert span2.name == "event: start"
        evt = span2.events[0]
        assert evt.name == "start"

        assert span3.name == "charm exec"

        for span in spans:
            assert span.resource.attributes["service.name"] == "frank-0"


def test_juju_topology_injection(caplog):
    import opentelemetry

    with patch(
        "opentelemetry.exporter.otlp.proto.http.trace_exporter.OTLPSpanExporter.export"
    ) as f:
        f.return_value = opentelemetry.sdk.trace.export.SpanExportResult.SUCCESS
        ctx = Context(MyCharmSimpleEvent, meta=MyCharmSimpleEvent.META)
        state = ctx.run("start", State())

        spans = f.call_args_list[0].args[0]

        for span in spans:
            # topology
            assert span.resource.attributes["juju_unit"] == "frank/0"
            assert span.resource.attributes["juju_application"] == "frank"
            assert span.resource.attributes["juju_model"] == state.model.name
            assert span.resource.attributes["juju_model_uuid"] == state.model.uuid


class MyCharmWithMethods(CharmBase):
    META = {"name": "frank"}

    def __init__(self, fw):
        super().__init__(fw)
        fw.observe(self.on.start, self._on_start)

    def _on_start(self, _):
        self.a()
        self.b()
        self.c()

    def a(self):
        pass

    def b(self):
        pass

    def c(self):
        pass

    @property
    def tempo(self):
        return "foo.bar:80"


autoinstrument(MyCharmWithMethods, MyCharmWithMethods.tempo)


def test_base_tracer_endpoint_methods(caplog):
    import opentelemetry

    with patch(
        "opentelemetry.exporter.otlp.proto.http.trace_exporter.OTLPSpanExporter.export"
    ) as f:
        f.return_value = opentelemetry.sdk.trace.export.SpanExportResult.SUCCESS
        ctx = Context(MyCharmWithMethods, meta=MyCharmWithMethods.META)
        ctx.run("start", State())

        spans = f.call_args_list[0].args[0]
        span_names = [span.name for span in spans]
        assert span_names == [
            "method call: MyCharmWithMethods.a",
            "method call: MyCharmWithMethods.b",
            "method call: MyCharmWithMethods.c",
            "method call: MyCharmWithMethods._on_start",
            "event: start",
            "charm exec",
        ]


class Foo(EventBase):
    pass


class MyEvents(CharmEvents):
    foo = EventSource(Foo)


class MyCharmWithCustomEvents(CharmBase):
    on = MyEvents()

    META = {"name": "frank"}

    def __init__(self, fw):
        super().__init__(fw)
        fw.observe(self.on.start, self._on_start)
        fw.observe(self.on.foo, self._on_foo)

    def _on_start(self, _):
        self.on.foo.emit()

    def _on_foo(self, _):
        pass

    @property
    def tempo(self):
        return "foo.bar:80"


autoinstrument(MyCharmWithCustomEvents, MyCharmWithCustomEvents.tempo)


def test_base_tracer_endpoint_custom_event(caplog):
    import opentelemetry

    with patch(
        "opentelemetry.exporter.otlp.proto.http.trace_exporter.OTLPSpanExporter.export"
    ) as f:
        f.return_value = opentelemetry.sdk.trace.export.SpanExportResult.SUCCESS
        ctx = Context(MyCharmWithCustomEvents, meta=MyCharmWithCustomEvents.META)
        ctx.run("start", State())

        spans = f.call_args_list[0].args[0]
        span_names = [span.name for span in spans]
        assert span_names == [
            "method call: MyCharmWithCustomEvents._on_foo",
            "event: foo",
            "method call: MyCharmWithCustomEvents._on_start",
            "event: start",
            "charm exec",
        ]
        # only the charm exec span is a root
        assert not spans[-1].parent
        for span in spans[:-1]:
            assert span.parent
            assert span.parent.trace_id
        assert len({(span.parent.trace_id if span.parent else 0) for span in spans}) == 2


class MyCharmSimpleState(CharmBase):
    META = {"name": "frank"}

    def __init__(self, framework: Framework):
        super().__init__(framework)
        framework.observe(self.on.start, self._on_start)

    def _on_start(self, _):
        pass

    @property
    def tempo(self):
        return "foo.bar:80"

    @property
    def state(self):
        return scenario.State(leader=True, unit_status=ops.ActiveStatus("foobar!"))


autoinstrument(MyCharmSimpleState, MyCharmSimpleState.tempo, MyCharmSimpleState.state)


def test_charm_tracing_snapshots():
    import opentelemetry

    with patch(
        "opentelemetry.exporter.otlp.proto.http.trace_exporter.OTLPSpanExporter.export"
    ) as f:
        f.return_value = opentelemetry.sdk.trace.export.SpanExportResult.SUCCESS
        ctx = Context(MyCharmSimpleState, meta=MyCharmSimpleState.META)
        ctx.run("start", State(leader=True))
        event_start_span: opentelemetry.sdk.trace.ReadableSpan = [
            span for span in f.call_args_list[0].args[0] if span.name == "charm exec"
        ][0]
        assert dict_to_state(json.loads(event_start_span.attributes["state"])).leader
