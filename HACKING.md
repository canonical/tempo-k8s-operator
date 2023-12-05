To manually test and develop tempo:

    charmcraft pack 
    charmcraft pack -p ./tests/integration/tester/ 
    j deploy ./tempo-k8s_ubuntu-22.04-amd64.charm tempo --resource tempo-image=grafana/tempo:1.5.0

you need to always deploy at least 2 units of tester:

    j deploy ./tester_ubuntu-22.04-amd64.charm tester -n 3 --resource workload=python:slim-buster
    j relate tempo:tracing tester:tracing

