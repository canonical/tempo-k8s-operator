import logging
import os
from unittest.mock import patch

import pytest
import scenario
from charms.tempo_k8s.v1.charm_tracing import CHARM_TRACING_ENABLED
from charms.tempo_k8s.v1.charm_tracing import _autoinstrument as autoinstrument
from charms.tempo_k8s.v2.tracing import (
    ProtocolNotRequestedError,
    ProtocolType,
    Receiver,
    TracingEndpointRequirer,
    TracingProviderAppData,
    TracingRequirerAppData,
)
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
        assert span.resource.attributes["service.name"] == "frank"
        assert span.resource.attributes["compose_service"] == "frank"
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
        assert span.resource.attributes["service.name"] == "frank"
        assert span.resource.attributes["compose_service"] == "frank"
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

        assert "quietly disabling charm_tracing for the run." in caplog.text
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
            assert span.resource.attributes["service.name"] == "frank"


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


class MyRemoteCharm(CharmBase):
    META = {"name": "charlie", "requires": {"tracing": {"interface": "tracing", "limit": 1}}}
    _request = True

    def __init__(self, framework: Framework):
        super().__init__(framework)
        self.tracing = TracingEndpointRequirer(
            self, "tracing", protocols=(["otlp_http"] if self._request else [])
        )

    def tempo(self):
        return self.tracing.get_endpoint("otlp_http")


autoinstrument(MyRemoteCharm, MyRemoteCharm.tempo)


@pytest.mark.parametrize("leader", (True, False))
def test_tracing_requirer_remote_charm_request_response(leader):
    # IF the leader unit (whoever it is) did request the endpoint to be activated
    MyRemoteCharm._request = True
    ctx = Context(MyRemoteCharm, meta=MyRemoteCharm.META)
    # WHEN you get any event AND the remote unit has already replied
    tracing = scenario.Relation(
        "tracing",
        # if we're not leader, assume the leader did its part already
        local_app_data=(
            TracingRequirerAppData(receivers=["otlp_http"]).dump() if not leader else {}
        ),
        remote_app_data=TracingProviderAppData(
            host="foo.com",
            receivers=[
                Receiver(
                    url="http://foo.com:80", protocol=ProtocolType(name="otlp_http", type="http")
                )
            ],
        ).dump(),
    )
    with ctx.manager("start", State(leader=leader, relations=[tracing])) as mgr:
        # THEN you're good
        assert mgr.charm.tempo() == "http://foo.com:80"


@pytest.mark.parametrize("leader", (True, False))
def test_tracing_requirer_remote_charm_no_request_but_response(leader):
    # IF the leader did NOT request the endpoint to be activated
    MyRemoteCharm._request = False
    ctx = Context(MyRemoteCharm, meta=MyRemoteCharm.META)
    # WHEN you get any event AND the remote unit has already replied
    tracing = scenario.Relation(
        "tracing",
        # empty local app data
        remote_app_data=TracingProviderAppData(
            # but the remote end has sent the data you need
            receivers=[
                Receiver(
                    url="http://foo.com:80", protocol=ProtocolType(name="otlp_http", type="http")
                )
            ],
        ).dump(),
    )
    with ctx.manager("start", State(leader=leader, relations=[tracing])) as mgr:
        # THEN you're lucky, but you're good
        assert mgr.charm.tempo() == "http://foo.com:80"


@pytest.mark.parametrize("relation", (True, False))
@pytest.mark.parametrize("leader", (True, False))
def test_tracing_requirer_remote_charm_no_request_no_response(leader, relation):
    """Verify that the charm successfully executes (with charm_tracing disabled) if the tempo() call raises."""
    # IF the leader did NOT request the endpoint to be activated
    MyRemoteCharm._request = False
    ctx = Context(MyRemoteCharm, meta=MyRemoteCharm.META)
    # WHEN you get any event
    if relation:
        # AND you have an empty relation
        tracing = scenario.Relation(
            "tracing",
            # empty local and remote app data
        )
        relations = [tracing]
    else:
        # OR no relation at all
        relations = []

    # THEN you're not totally good: self.tempo() will raise, but charm exec will still exit 0
    with ctx.manager("start", State(leader=leader, relations=relations)) as mgr:
        with pytest.raises(ProtocolNotRequestedError):
            assert mgr.charm.tempo() is None


class MyRemoteBorkyCharm(CharmBase):
    META = {"name": "charlie", "requires": {"tracing": {"interface": "tracing", "limit": 1}}}
    _borky_return_value = None

    def tempo(self):
        return self._borky_return_value


autoinstrument(MyRemoteBorkyCharm, MyRemoteBorkyCharm.tempo)


@pytest.mark.parametrize("borky_return_value", (True, 42, object(), 0.2, [], (), {}))
def test_borky_tempo_return_value(borky_return_value, caplog):
    """Verify that the charm exits 0 (with charm_tracing disabled) if the tempo() call returns bad values."""
    # IF the charm's tempo endpoint getter returns anything but None or str
    MyRemoteBorkyCharm._borky_return_value = borky_return_value
    ctx = Context(MyRemoteBorkyCharm, meta=MyRemoteBorkyCharm.META)
    # WHEN you get any event
    # THEN you're not totally good: self.tempo() will raise, but charm exec will still exit 0

    ctx.run("start", State())
    # traceback from the TypeError raised by _get_tracing_endpoint
    assert "should return a tempo endpoint" in caplog.text
    # logger.exception in _setup_root_span_initializer
    assert "exception retrieving the tracing endpoint from" in caplog.text
    assert "proceeding with charm_tracing DISABLED." in caplog.text


class MyCharmStaticMethods(CharmBase):
    META = {"name": "jolene"}

    def __init__(self, fw):
        super().__init__(fw)
        fw.observe(self.on.start, self._on_start)
        fw.observe(self.on.update_status, self._on_update_status)

    def _on_start(self, _):
        for o in (OtherObj(), OtherObj):
            for meth in ("_staticmeth", "_staticmeth1", "_staticmeth2"):
                assert getattr(o, meth)(1) == 2

    def _on_update_status(self, _):
        # super-ugly edge cases
        OtherObj()._staticmeth3(OtherObj())
        OtherObj()._staticmeth4(OtherObj())
        OtherObj._staticmeth3(OtherObj())
        OtherObj._staticmeth4(OtherObj(), foo=2)

    @property
    def tempo(self):
        return "foo.bar:80"


class OtherObj:
    @staticmethod
    def _staticmeth(i: int, *args, **kwargs):
        return 1 + i

    @staticmethod
    def _staticmeth1(i: int):
        return 1 + i

    @staticmethod
    def _staticmeth2(i: int, foo="bar"):
        return 1 + i

    @staticmethod
    def _staticmeth3(abc: "OtherObj", foo="bar"):
        return 1 + 1

    @staticmethod
    def _staticmeth4(abc: int, foo="bar"):
        return 1 + 1


autoinstrument(MyCharmStaticMethods, MyCharmStaticMethods.tempo, extra_types=[OtherObj])


def test_trace_staticmethods(caplog):
    import opentelemetry

    with patch(
        "opentelemetry.exporter.otlp.proto.http.trace_exporter.OTLPSpanExporter.export"
    ) as f:
        f.return_value = opentelemetry.sdk.trace.export.SpanExportResult.SUCCESS
        ctx = Context(MyCharmStaticMethods, meta=MyCharmStaticMethods.META)
        ctx.run("start", State())

        spans = f.call_args_list[0].args[0]

        span_names = [span.name for span in spans]
        assert span_names == [
            "method call: OtherObj._staticmeth",
            "method call: OtherObj._staticmeth1",
            "method call: OtherObj._staticmeth2",
            "method call: OtherObj._staticmeth",
            "method call: OtherObj._staticmeth1",
            "method call: OtherObj._staticmeth2",
            "method call: MyCharmStaticMethods._on_start",
            "event: start",
            "charm exec",
        ]

        for span in spans:
            assert span.resource.attributes["service.name"] == "jolene"


def test_trace_staticmethods_bork(caplog):
    import opentelemetry

    with patch(
        "opentelemetry.exporter.otlp.proto.http.trace_exporter.OTLPSpanExporter.export"
    ) as f:
        f.return_value = opentelemetry.sdk.trace.export.SpanExportResult.SUCCESS
        ctx = Context(MyCharmStaticMethods, meta=MyCharmStaticMethods.META)
        ctx.run("update-status", State())
