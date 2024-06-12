This directory contains some files to be used in conjunction with the facade charm.

For example, to simulate s3:

```
juju deploy facade s3-facade
juju relate s3-facade:provide-s3 tempo:s3
juju run s3-facade/0 update --params ./tests/manual/facades/s3.yaml
```

