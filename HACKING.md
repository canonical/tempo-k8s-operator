To manually test and develop tempo:

    charmcraft pack 
    charmcraft pack -p ./tests/integration/tester/ 
    j deploy ./tempo-k8s_ubuntu-22.04-amd64.charm tempo

you need to always deploy at least 2 units of tester:

    j deploy ./tests/integration/tester/tester-k8s_ubuntu-22.04-amd64.charm tester -n 3
    j relate tempo:tracing tester:tracing

