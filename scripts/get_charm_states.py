"""Script to download from a tempo backend the charm traces associated with a specific unit."""
import json
from pathlib import Path
from typing import Optional

import requests
import typer

app = typer.Typer()


def _get_charm_states(target: str, tempo: str, output: Optional[Path]=None):
    # get trace IDs for this target
    target_name = target.replace("/", "-")
    req = requests.get(
        f"{tempo}/api/search",
        params={
            "q": f'{{rootServiceName = "{target_name}"}}'
        })
    jsn = req.json()
    trace_ids = [obj['traceID'] for obj in jsn.get('traces', ())]

    if not trace_ids:
        exit(f"no traces (yet) for {target!r}, or invalid target.")

    data = []
    for trace_id in trace_ids:
        req = requests.get(f"{tempo}/api/traces/{trace_id}")
        jsn = req.json()
        for batch in jsn['batches']:
            root_span = batch['scopeSpans'][0]['spans'][0]
            root_span_attrs = {attr['key']: attr['value'].get('stringValue') for attr in root_span['attributes']}
            dispatch_path = root_span_attrs['juju.dispatch_path']
            state = root_span_attrs['state']
            data.append((dispatch_path, json.loads(state)))

    data_json = json.dumps(data, indent=2)
    if output:
        output.write_text(data_json)
    else:
        print(data_json)


@app.command
def get_charm_states(
        target: str = typer.Argument(
            ...,
            help="Target unit."
        ),
        tempo: str = typer.Argument(
            ...,
            help="Tempo backend url, such as `http://10.0.0.10:4330`.",
        ),
        output: Optional[Path] = typer.Option(
            None, "-o", "--output",
            help="File in which to dump the event, state pairs. "
                 "If omitted, all will be printed to stdout."
        )
):
    """Download from a tracing backend the charm exec traces associated with a juju unit.

    Usage:
        ``get_charm_states myapp/0 http://10.0.0.10:3200``
    """
    _get_charm_states(target=target, tempo=tempo, output=output)


if __name__ == '__main__':
    # _get_charm_states("tempo3/0", tempo="http://10.1.232.152:3200")
    app()
