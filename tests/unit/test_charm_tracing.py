from charms.tempo_k8s.v0.tracing import Ingester, TracingRequirerAppData


def test_tracing_requirer_app_data():
    host = "tempo-k8s-0.tempo-k8s-endpoints.testing.svc.cluster.local"

    appdata = TracingRequirerAppData(
        host=host,
        ingesters=[
            Ingester(protocol="tempo", port=3200),
            Ingester(protocol="otlp_grpc", port=4317),
            Ingester(protocol="otlp_http", port=4318),
            Ingester(protocol="zipkin", port=9411),
        ],
    )
    databag = {}
    appdata.dump(databag)
    assert databag == {
        "host": f'"{host}"',
        "ingesters": '[{"protocol": "tempo", "port": 3200}, {"protocol": "otlp_grpc", "port": 4317}, '
        '{"protocol": "otlp_http", "port": 4318}, {"protocol": "zipkin", "port": 9411}]',
    }

    td = TracingRequirerAppData.load(databag)
    assert td == appdata
