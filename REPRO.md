To reproduce nginx grcp issue:

```charmcraft pack
juju add-model repro
charmcraft pack
juju deploy ./tempo-k8s_ubuntu-22.04-amd64.charm --resource nginx-image=nginx:latest --resource tempo-image=grafana/tempo:2.4.0 tempo
juju deploy traefik-k8s --channel edge traefik
juju deploy grafana-k8s --channel edge grafana

# cross-relate all charms over ingress, grafana-*, and tracing interfaces
jhack imatrix fill

# wait for tempo to reach active/idle (can take a few minutes)
juju scp ./tests/integration/tester/src/resources/webserver-dependencies.txt tempo/0:/

# edit tracegen.py and replace at line 13 with tempo/0 unit IP
juju scp ./tracegen.py tempo/0:/ 
juju ssh tempo/0 python3 -m pip install -r /webserver-dependencies.txt

juju ssh tempo/0 python3 /tracegen.py

# you should see:
# Failed to export traces to <tempo IP>:8080, error code: StatusCode.UNIMPLEMENTED
```

useful links:
- https://github.com/open-telemetry/opentelemetry-operator/issues/902
- https://www.nginx.com/blog/deploying-nginx-plus-as-an-api-gateway-part-3-publishing-grpc-services/
