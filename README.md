# Tempo Operator

This repository contains the source code for a Charmed Operator that drives [Tempo] on Kubernetes.

## Usage

Assuming you have access to a bootstrapped Juju controller on Kubernetes, you can:

```bash
$ juju deploy tempo-k8s # --trust (use when cluster has RBAC enabled)
```

## OCI Images

This charm, by default, deploys `grafana/tempo:1.5.0`.

## Contributing

Please see the [Juju SDK docs](https://juju.is/docs/sdk) for guidelines
on enhancements to this charm following best practice guidelines, and the
[contributing] doc for developer guidance.

[Tempo]: https://grafana.com/traces/
[contributing]: https://github.com/PietroPasotti/tempo-k8s-operator/blob/main/CONTRIBUTING.md
