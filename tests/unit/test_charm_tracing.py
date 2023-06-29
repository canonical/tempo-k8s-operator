from unittest.mock import patch

import pytest
from ops import EventBase, EventSource
from ops.charm import CharmBase, CharmEvents
from scenario import State, Context

from charms.tempo_k8s.v0.charm_instrumentation import get_current_span, trace, trace_charm


@pytest.fixture(autouse=True)
def cleanup():
    def patched_set_tracer_provider(tracer_provider, log):
        import opentelemetry

        opentelemetry.trace._TRACER_PROVIDER = tracer_provider

    with patch("opentelemetry.trace._set_tracer_provider", new=patched_set_tracer_provider):
        yield


@trace_charm("tempo")
class MyCharmSimple(CharmBase):
    META = {"name": "frank"}

    @property
    def tempo(self):
        return "foo.bar:80"


def test_base_tracer_endpoint(caplog):
    import opentelemetry

    with patch("opentelemetry.exporter.otlp.proto.grpc.exporter.OTLPExporterMixin._export") as f:
        f.return_value = opentelemetry.sdk.metrics._internal.export.MetricExportResult.SUCCESS
        ctx = Context(MyCharmSimple, meta=MyCharmSimple.META)
        ctx.run("start", State())
        assert "Setting up span exporter to endpoint: foo.bar:80" in caplog.text
        span = f.call_args_list[0].args[0][0]
        assert span.resource.attributes["service.name"] == "MyCharmSimple"
        assert span.resource.attributes["compose_service"] == "MyCharmSimple"


@trace_charm("tempo")
class MyCharmSimpleDisabled(CharmBase):
    META = {"name": "frank"}

    @property
    def tempo(self):
        return None


def test_base_tracer_endpoint_disabled(caplog):
    import opentelemetry

    with patch("opentelemetry.exporter.otlp.proto.grpc.exporter.OTLPExporterMixin._export") as f:
        f.return_value = opentelemetry.sdk.metrics._internal.export.MetricExportResult.SUCCESS
        ctx = Context(MyCharmSimpleDisabled, meta=MyCharmSimpleDisabled.META)
        ctx.run("start", State())

        assert "continuing with tracing DISABLED." in caplog.text
        assert not f.called


@trace
def _my_fn(foo):
    return foo + 1


@trace_charm("tempo")
class MyCharmSimpleEvent(CharmBase):
    META = {"name": "frank"}

    def __init__(self, fw):
        super().__init__(fw)
        fw.observe(self.on.start, self._on_start)
        span = get_current_span()
        span.add_event(
            "log",
            {
                "foo": "bar",
                "baz": "qux",
            },
        )

    def _on_start(self, _):
        _my_fn(2)

    @property
    def tempo(self):
        return "foo.bar:80"


def test_base_tracer_endpoint_event(caplog):
    import opentelemetry

    with patch("opentelemetry.exporter.otlp.proto.grpc.exporter.OTLPExporterMixin._export") as f:
        f.return_value = opentelemetry.sdk.metrics._internal.export.MetricExportResult.SUCCESS
        ctx = Context(MyCharmSimpleEvent, meta=MyCharmSimpleEvent.META)
        ctx.run("start", State())

        spans = f.call_args_list[0].args[0]
        span0, span1, span2, span3 = spans
        assert span0.name == "method call: _my_fn"

        assert span1.name == "method call: _on_start"

        assert span2.name == "event: start"
        evt = span2.events[0]
        assert evt.name == "start"

        assert span3.name == "charm exec"

        for span in spans:
            assert span.resource.attributes["service.name"] == "MyCharmSimpleEvent"


@trace_charm("tempo")
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


def test_base_tracer_endpoint_methods(caplog):
    import opentelemetry

    with patch("opentelemetry.exporter.otlp.proto.grpc.exporter.OTLPExporterMixin._export") as f:
        f.return_value = opentelemetry.sdk.metrics._internal.export.MetricExportResult.SUCCESS
        ctx = Context(MyCharmWithMethods, meta=MyCharmWithMethods.META)
        ctx.run("start", State())

        spans = f.call_args_list[0].args[0]
        span_names = [span.name for span in spans]
        assert span_names == [
            "method call: a",
            "method call: b",
            "method call: c",
            "method call: _on_start",
            "event: start",
            "charm exec",
        ]


class Foo(EventBase):
    pass


class MyEvents(CharmEvents):
    foo = EventSource(Foo)


@trace_charm("tempo")
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


def test_base_tracer_endpoint_custom_event(caplog):
    import opentelemetry

    with patch("opentelemetry.exporter.otlp.proto.grpc.exporter.OTLPExporterMixin._export") as f:
        f.return_value = opentelemetry.sdk.metrics._internal.export.MetricExportResult.SUCCESS
        ctx = Context(MyCharmWithCustomEvents, meta=MyCharmWithCustomEvents.META)
        ctx.run("start", State())

        spans = f.call_args_list[0].args[0]
        span_names = [span.name for span in spans]
        assert span_names == [
            "method call: _on_foo",
            "event: foo",
            "method call: _on_start",
            "event: start",
            "charm exec",
        ]
        # only the charm exec span is a root
        assert not spans[-1].parent
        for span in spans[:-1]:
            assert span.parent
            assert span.parent.trace_id
        assert len(set((span.parent.trace_id if span.parent else 0) for span in spans)) == 2
