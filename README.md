# Tempo Operator

[![CharmHub Badge](https://charmhub.io/tempo-k8s/badge.svg)](https://charmhub.io/tempo-k8s)
[![Release](https://github.com/canonical/tempo-k8s-operator/actions/workflows/release.yaml/badge.svg)](https://github.com/canonical/tempo-k8s-operator/actions/workflows/release.yaml)
[![Discourse Status](https://img.shields.io/discourse/status?server=https%3A%2F%2Fdiscourse.charmhub.io&style=flat&label=CharmHub%20Discourse)](https://discourse.charmhub.io)

This public archive contains the source code for a Charmed Operator that drives a monolithic deployment of [Tempo] on Kubernetes.

The charm has been superseded by the [Charmed Tempo HA solution](https://discourse.charmhub.io/t/charmed-tempo-ha/15531), consisting of two charms:
- `tempo-coordinator-k8s`
  - [github](https://github.com/canonical/tempo-coordinator-k8s-operator/)
  - [charmhub](https://charmhub.io/tempo-coordinator-k8s-operator/)
- `tempo-worker-k8s`
  - [github](https://github.com/canonical/tempo-worker-k8s-operator)
  - [charmhub](https://charmhub.io/tempo-worker-k8s-operator)

This charm is now unmaintained.

All charm libraries that used to be owned by it have been transferred to the `tempo-coordinator-k8s` ownership and their versions have been reset.

| library       | old location                      | new location                                  |
|---------------|:----------------------------------|-----------------------------------------------|
| tracing       | charms.tempo_k8s.v2.tracing       | charms.tempo_coordinator_k8s.v0.tracing       |
| charm_tracing | charms.tempo_k8s.v1.charm_tracing | charms.tempo_coordinator_k8s.v0.charm_tracing |

The APIs are unchanged, so after fetching the new libraries, a search and replace should be all you need.