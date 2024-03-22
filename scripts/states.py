#!/usr/bin/env python3

"""Script to download from a tempo backend the charm traces associated with a specific unit."""
import datetime
import json
import logging
from enum import Enum
from pathlib import Path
from typing import Optional, List
from urllib.parse import urlparse

import requests
import typer


logger = logging.getLogger(__name__)


class Format(Enum):
    state = 'state'  # scenario.State repr
    json = 'json'    # jsonified scenario.State


def _get_url(tempo: str):
    tempo_url = urlparse(tempo)
    if not tempo_url.scheme:
        tempo = "http://" + tempo
    if not tempo_url.port:
        tempo = tempo + ":3200"
    return tempo


def _list_charm_trace_ids(target: str, tempo: str,
                          limit: int = 20,
                          start: datetime.datetime = None,
                          end: datetime.datetime = None
                          ) -> List[str]:
    """Output a list of trace IDs associated with a specific charm."""
    tempo_url = _get_url(tempo)
    # get trace IDs for this target
    app_name = target.split("/")[0]
    params = {
        "q": f'{{rootServiceName = "{app_name}" && resource.juju.unit = "{target}"}}'
    }
    if limit:
        params['limit'] = limit
    if start:
        params['start'] = int(start.timestamp())
        if not end:
            end = datetime.datetime.now()
    if end:
        params['end'] = int(end.timestamp())

    req = requests.get(
        f"{tempo_url}/api/search",
        params=params)
    try:
        jsn = req.json()
    except json.JSONDecodeError:
        logger.exception(f"invalid response from {req.url!r} with params={params}")
        exit("invalid request")

    trace_ids = [obj['traceID'] for obj in jsn.get('traces', ())]

    if not trace_ids:
        exit(f"no traces (yet) for {target!r}, or invalid target.")
    return trace_ids


def _get_charm_states(tempo: str,
                      trace_ids: List[str],
                      format: Format = Format.state):
    tempo_url = _get_url(tempo)

    data = []
    for trace_id in trace_ids:
        api_url = f"{tempo_url}/api/traces/{trace_id}"
        req = requests.get(api_url)
        try:
            jsn = req.json()
        except json.JSONDecodeError as e:
            if req.text == "trace not found":
                logger.error(f"trace {trace_id} not found")
            else:
                logger.exception(f"failed to retrieve trace metadata for {trace_id!r} from {api_url}")
            continue

        for batch in jsn['batches']:
            root_span = batch['scopeSpans'][0]['spans'][0]
            root_span_attrs = {attr['key']: attr['value'].get('stringValue')
                               for attr in root_span.get('attributes', ())}
            dispatch_path = root_span_attrs['juju.dispatch_path']
            state = root_span_attrs.get('state')
            if not state:
                logger.warning(f"trace {trace_id} has no attached state. skipping...")
                continue
            env = root_span_attrs.get('env')
            data.append((trace_id, dispatch_path, json.loads(state), json.loads(env)))

    if not data:
        exit(f"no states could be collected from traces {trace_ids}")

    if format is Format.json:
        data_json = json.dumps(data, indent=2)
        print(data_json)

    elif format is Format.state:
        try:
            import sys
            sys.path.append(str(Path().parent.parent.absolute()))
            from lib.charms.tempo_k8s.v0.snapshot import dict_to_state, reconstruct_event
        except ModuleNotFoundError:
            logger.exception("failed to import dict_to_state")
            exit("failed to serialize json to scenario.State, see logs above for details")

        for trace_id, _, state_dict, env_dict in data:
            print(f"\n trace id: {trace_id}")

            # strip the 'hooks/' prefix
            state = dict_to_state(state_dict)
            event = reconstruct_event(env_dict, state)
            print("event = " + repr(event))
            print("state = " + repr(state))

    else:
        exit(f"invalid format {format}")


def list_charm_trace_ids(
        tempo: str = typer.Argument(
            ...,
            help="Tempo backend url, such as `10.0.0.10`.",
        ),
        target: str = typer.Argument(
            ...,
            help="Target Juju unit name, such as ``ubuntu/0`` or ``traefik/1``."
        ),
        start: datetime.datetime = typer.Option(
            None,
            help="Initial limit for search. "
                 "If not provided, only 'recent' results will be returned.",
            formats=["%Y-%m-%d", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S"]
        ),
        end: datetime.datetime = typer.Option(
            None,
            help="Final limit for search. Defaults to 'now'.",
            formats=["%Y-%m-%d", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S"]
        ),
        max: bool=typer.Option(
            False,
            help="""Return the max range""",
            is_flag=True),
        limit: int = typer.Option(
            None,
            help="Maximum number of results to be returned."
        ),
):
    """Print a list of charm execution trace IDs.

    If passing `start` and/or `end`, the total range mustn't exceed 168h.
    Pass `max` to get the trace ids from the maximum range allowed, up until now.
    """
    if max:
        if start or end:
            logger.warning("`max` argument provided: ignoring `start` and `end`")
        start = datetime.datetime.now() - datetime.timedelta(hours=168)
        end = None
    if start:
        _end = end or datetime.datetime.now()
        if (_end - start).seconds > (168 * 60):
            exit(f"invalid time range: {start} and {end or '`now`'} are more than 168h apart.")

    trace_ids = _list_charm_trace_ids(target=target, tempo=tempo,
                                      start=start, end=end, limit=limit)
    print('\n'.join(trace_ids))


def get_charm_states(
        tempo: str = typer.Argument(
            ...,
            help="Tempo backend url. If port is omitted, defaults to 3200. If scheme is omitted, defaults to `http://`.",
        ),
        target: str = typer.Argument(
            None,
            help="Target unit. Can be omitted if you are passing some specific trace IDs."
        ),
        trace_id: Optional[List[str]] = typer.Option(
            None,
            "-t", "--trace-id",
            help="One or more trace IDs whose states you want to fetch. "
                 "If omitted, will use all traces registered in the past 168h."),
        format: Format = typer.Option(
            "state",
            "-f",
            "--format",
            help="""Output formatting. Defaults to `scenario.State` repr."""
        )
):
    """Download from a tracing backend the registered states of a charm.

    Usage:
        ``states get http://10.0.0.10:3200 myapp/0``
    """
    if not target and not trace_id:
        exit("you need to specify a target or some trace IDs.")

    trace_ids = trace_id or _list_charm_trace_ids(
        target, tempo, start=datetime.datetime.now() - datetime.timedelta(hours=167))
    _get_charm_states(tempo=tempo, trace_ids=trace_ids, format=format)


if __name__ == '__main__':
    # _get_charm_states("tempo3/0", tempo="http://10.1.232.152:3200")
    app = typer.Typer(
        name="states",
    help="""Utility CLI tool to interact with the charm traces registered on a specific Tempo backend. 
    """, no_args_is_help=True)
    app.command("list", no_args_is_help=True)(list_charm_trace_ids)
    app.command("get", no_args_is_help=True)(get_charm_states)
    app()
