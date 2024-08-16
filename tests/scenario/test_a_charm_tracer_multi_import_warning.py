# WARNING ensure that this test module runs before any other scenario test file, else the
# imports from previous tests will pollute the sys.modules and cause this test to fail.
# I know this is horrible but yea, couldn't find a better way to fix the issue. Tried:
# - delete from sys.modules all modules containing 'charms.tempo_k8s'
# - delete from sys.modules all modules containing 'otlp_http'


from unittest.mock import patch

# this test file is intentionally quite broken, don't modify the imports
# import autoinstrument from charms.[...]
from charms.tempo_k8s.v1.charm_tracing import _autoinstrument as autoinstrument
from ops import CharmBase
from scenario import Context, State

# import trace from lib.charms.[...]
from lib.charms.tempo_k8s.v1.charm_tracing import trace


@trace
def _my_fn(foo):
    return foo + 1


class MyCharmSimpleEvent(CharmBase):
    META = {"name": "margherita"}

    def __init__(self, fw):
        super().__init__(fw)
        fw.observe(self.on.start, self._on_start)

    def _on_start(self, _):
        _my_fn(2)

    @property
    def tempo(self):
        return "foo.bar:80"


autoinstrument(MyCharmSimpleEvent, "tempo")


def test_charm_tracer_multi_import_warning(caplog, monkeypatch):
    """Check that we warn the user in case the test is importing tracing symbols from multiple paths.

    See https://python-notes.curiousefficiency.org/en/latest/python_concepts/import_traps.html#the-double-import-trap
    for a great explanation of what's going on.
    """
    import opentelemetry

    with patch(
        "opentelemetry.exporter.otlp.proto.http.trace_exporter.OTLPSpanExporter.export"
    ) as f:
        f.return_value = opentelemetry.sdk.trace.export.SpanExportResult.SUCCESS
        ctx = Context(MyCharmSimpleEvent, meta=MyCharmSimpleEvent.META)
        with caplog.at_level("WARNING"):
            ctx.run("start", State())

        spans = f.call_args_list[0].args[0]
        assert [span.name for span in spans] == [
            # we're only able to capture the _my_fn span because of the fallback behaviour
            "function call: _my_fn",
            "method call: MyCharmSimpleEvent._on_start",
            "event: start",
            "margherita/0: start event",
        ]
        assert "Tracer not found in `tracer` context var." in caplog.records[0].message
