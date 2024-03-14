# Tempo Operator

[![CharmHub Badge](https://charmhub.io/tempo-k8s/badge.svg)](https://charmhub.io/tempo-k8s)
[![Release](https://github.com/canonical/tempo-k8s-operator/actions/workflows/release.yaml/badge.svg)](https://github.com/canonical/tempo-k8s-operator/actions/workflows/release.yaml)
[![Discourse Status](https://img.shields.io/discourse/status?server=https%3A%2F%2Fdiscourse.charmhub.io&style=flat&label=CharmHub%20Discourse)](https://discourse.charmhub.io)

This repository contains the source code for a Charmed Operator that drives [Tempo] on Kubernetes.

## Usage

Assuming you have access to a bootstrapped Juju controller on Kubernetes, you can:

```bash
$ juju deploy tempo-k8s # --trust (use when cluster has RBAC enabled)
```

## OCI Images

This charm, by default, deploys `grafana/tempo:2.4.0`.

## Contributing

Please see the [Juju SDK docs](https://juju.is/docs/sdk) for guidelines
on enhancements to this charm following best practice guidelines, and the
[contributing] doc for developer guidance.

[Tempo]: https://grafana.com/traces/
[contributing]: https://github.com/PietroPasotti/tempo-k8s-operator/blob/main/CONTRIBUTING.md
