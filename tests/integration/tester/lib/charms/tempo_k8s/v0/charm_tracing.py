#!/usr/bin/env python3
# Copyright 2022 Canonical Ltd.
# See LICENSE file for licensing details.

import functools
import inspect
import logging
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Callable, Optional, Type, TypeVar, Union

import opentelemetry
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import Span, TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.trace import get_current_span as otlp_get_current_span
from ops.charm import CharmBase
from ops.framework import Framework

# The unique Charmhub library identifier, never change it
LIBID = "cb1705dcd1a14ca09b2e60187d1215c7"  # TODO: register new uuid; this is FAKE

# Increment this major API version when introducing breaking changes
LIBAPI = 0

# Increment this PATCH version before using `charmcraft publish-lib` or reset
# to 0 if you are raising the major API version
LIBPATCH = 1


PYDEPS = ["opentelemetry-exporter-otlp-proto-grpc"]

logger = logging.getLogger("tracing")

tracer: ContextVar[opentelemetry.trace.Tracer] = ContextVar("tracer")
# redefine it here to expose it as a toplevel module name
get_current_span = otlp_get_current_span


def _get_tracer() -> Optional[opentelemetry.trace.Tracer]:
    try:
        return tracer.get()
    except LookupError:
        return None


@contextmanager
def _span(name: str) -> Optional[Span]:
    """Context to create a span if there is a tracer, otherwise do nothing."""
    if tracer := _get_tracer():
        with tracer.start_as_current_span(name) as span:
            yield span
    else:
        yield None


_C = TypeVar("_C", bound=Type[CharmBase])
_T = TypeVar("_T", bound=type)
_F = TypeVar("_F", bound=Type[Callable])


class TracingError(RuntimeError):
    """Base class for errors raised by this module."""


class UntraceableObject(TracingError):
    """Raised when an object you're attempting to instrument cannot be autoinstrumented."""


def _setup_root_span_initializer(
    charm: Type[CharmBase], tempo_endpoint: str, app_name: Optional[str] = None
):
    """Patches the charm's initializer."""
    original_init = charm.__init__

    @functools.wraps(original_init)
    def wrap_init(self: CharmBase, framework: Framework, *args, **kwargs):
        original_init(self, framework, *args, **kwargs)

        original_event_context = framework._event_context

        logging.debug("Initializing opentelemetry tracer...")
        service_name = app_name or charm.__name__

        resource = Resource.create(
            attributes={"service.name": service_name, "compose_service": service_name}
        )
        provider = TracerProvider(resource=resource)

        tempo = getattr(self, tempo_endpoint)
        if tempo is None:
            logger.warning(
                f"{charm}.{tempo_endpoint} returned {tempo}; " f"continuing with tracing DISABLED."
            )
            return

        elif not isinstance(tempo, str):
            raise TypeError(
                f"{charm}.{tempo_endpoint} should return a tempo endpoint (string); "
                f"got {tempo} instead."
            )
        else:
            logger.debug(f"Setting up span exporter to endpoint: {tempo}")
            exporter = OTLPSpanExporter(endpoint=tempo)

        processor = BatchSpanProcessor(exporter)
        provider.add_span_processor(processor)
        opentelemetry.trace.set_tracer_provider(provider)
        _tracer = opentelemetry.trace.get_tracer(service_name)

        span = _tracer.start_span("charm exec")

        _tracer_token = tracer.set(_tracer)

        @contextmanager
        def wrap_event_context(event_name: str):
            # when the framework enters an event context, we create a span.
            with _span("event: " + event_name) as span:
                if span:
                    # todo: figure out how to inject event attrs in here
                    span.add_event(event_name)
                yield original_event_context(event_name)

        framework._event_context = wrap_event_context

        original_close = framework.close

        @functools.wraps(original_close)
        def wrap_close():
            span.end()
            tracer.reset(_tracer_token)
            tp: TracerProvider = opentelemetry.trace.get_tracer_provider()
            tp.force_flush()
            tp.shutdown()
            original_close()

        framework.close = wrap_close

        return

    charm.__init__ = wrap_init


def trace_charm(tempo_endpoint: str, app_name: str = None) -> Callable[[_C], _C]:
    """Setup tracing on this charm class."""

    def trace_charm_type(charm: _C) -> _C:
        """Prepares the charm as tracing root: when the charm is run, the root span will open."""

        logger.info(f"instrumenting {charm}")
        # check that it is in dir(charm).
        if not hasattr(charm, tempo_endpoint):
            raise RuntimeError(
                f"you passed tempo_endpoint={tempo_endpoint} to "
                f"@trace_charm; but {charm}.{tempo_endpoint} was not found."
            )

        _setup_root_span_initializer(charm, tempo_endpoint, app_name=app_name)
        trace_type(charm)
        return charm

    return trace_charm_type


def trace_type(cls: _T) -> _T:
    logger.info(f"instrumenting {cls}")
    for name, method in inspect.getmembers(cls, predicate=inspect.isfunction):
        logger.info(f"discovered {method}")

        if method.__name__.startswith("__"):
            logger.info(f"skipping (dunder)")
            continue

        setattr(cls, name, trace_function(method))

    return cls


def trace_function(function: _F) -> _F:
    logger.info(f"instrumenting {function}")

    # sig = inspect.signature(function)
    @functools.wraps(function)
    def wrapped_function(*args, **kwargs):
        with _span("method call: " + function.__name__):
            return function(*args, **kwargs)

    # wrapped_function.__signature__ = sig
    return wrapped_function


def trace(obj: Union[Type, Callable]):
    """Decorator to trace an object and send the resulting spans to Tempo."""
    if isinstance(obj, type):
        return trace_type(obj)
    else:
        try:
            return trace_function(obj)
        except:
            raise UntraceableObject(
                f"cannot create span from {type(obj)}; instrument {obj} manually."
            )
