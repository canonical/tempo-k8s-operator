# Deploying Tempo

To manually test and develop tempo:

    charmcraft pack 
    charmcraft pack -p ./tests/integration/tester/ 
    j deploy ./tempo-k8s_ubuntu-22.04-amd64.charm tempo --resource tempo-image=grafana/tempo:2.4.0

you need to always deploy at least 2 units of tester:

    j deploy ./tester_ubuntu-22.04-amd64.charm tester -n 3 --resource workload=python:slim-buster
    j relate tempo:tracing tester:tracing

# Object storage

In order to test tempo with object storage (using [minio charm](https://github.com/canonical/minio-operator)), 
you can do the following (replace YOUR_MINIO_ACCESS_KEY and YOUR_MINIO_SECRET with anything you want as long as the
secret is more than 8 characters):

Deploy minio:
```
juju deploy minio --channel edge --trust
juju config minio access-key=YOUR_MINIO_ACCESS_KEY
juju config minio secret-key=YOUR_MINIO_SECRET
```

Deploy mc and set up buckets:
```
sudo snap install minio-client --edge --devmode
kubectl port-forward service/minio -n test-minio 9000:9000
minio-client config host add local http://localhost:9000 YOUR_MINIO_ACCESS_KEY YOUR_MINIO_SECRET
minio-client mb local/tempo
```

Deploy and configure s3-integrator:
```
juju deploy s3-integrator
juju config s3-integrator endpoint=http://MINIO_IP_ADDRESS:9000
juju config s3-integrator bucket=tempo
juju run s3-integrator/leader sync-s3-credentials access-key=YOUR_MINIO_ACCESS_KEY secret-key=YOUR_MINIO_SECRET
```

Deploy tempo:
```
juju deploy ./tempo-k8s_ubuntu-22.04-amd64.charm tempo --resource tempo-image=grafana/tempo:2.4.0
juju integrate tempo s3-integrator
```
