# Copyright 2023 Canonical
# See LICENSE file for licensing details.
"""Nginx workload."""

import logging
from typing import Any, Dict, List, Optional, Set

import crossplane
from ops.pebble import Layer

logger = logging.getLogger(__name__)


NGINX_DIR = "/etc/nginx"
NGINX_CONFIG = f"{NGINX_DIR}/nginx.conf"
KEY_PATH = f"{NGINX_DIR}/certs/server.key"
CERT_PATH = f"{NGINX_DIR}/certs/server.cert"
CA_CERT_PATH = f"{NGINX_DIR}/certs/ca.cert"

_PORTS_BY_PROTOCOL = [
    {
        "directive": "upstream",
        "args": ["api"],
        "block": [
            {"directive": "server", "args": [f"localhost:3200"]}
        ],
    },
    {
        "directive": "upstream",
        "args": ["otlp_http"],
        "block": [
            {"directive": "server", "args": [f"localhost:4318"]}
        ],
    }
]


class Nginx:
    """Helper class to manage the nginx workload."""

    config_path = NGINX_CONFIG

    def __init__(self, server_name: str):
        self.server_name = server_name

    def config(self, tls: bool = False) -> str:
        """Build and return the Nginx configuration."""
        log_level = "error"

        # build the complete configuration
        full_config = [
            {"directive": "worker_processes", "args": ["5"]},
            {"directive": "error_log", "args": ["/dev/stderr", log_level]},
            {"directive": "pid", "args": ["/tmp/nginx.pid"]},
            {"directive": "worker_rlimit_nofile", "args": ["8192"]},
            {
                "directive": "events",
                "args": [],
                "block": [{"directive": "worker_connections", "args": ["4096"]}],
            },
            {
                "directive": "http",
                "args": [],
                "block": [
                    # upstreams (load balancing)
                    # TODO once Tempo is HA we'll need addresses_by_role similar to mimir
                    {
                        "directive": "upstream",
                        "args": ["api"],
                        "block": [
                            {"directive": "server", "args": [f"localhost:3200"]}
                        ],
                    },
                    {
                        "directive": "upstream",
                        "args": ["otlp_http"],
                        "block": [
                            {"directive": "server", "args": [f"localhost:4318"]}
                        ],
                    },
                    # temp paths
                    {"directive": "client_body_temp_path", "args": ["/tmp/client_temp"]},
                    {"directive": "proxy_temp_path", "args": ["/tmp/proxy_temp_path"]},
                    {"directive": "fastcgi_temp_path", "args": ["/tmp/fastcgi_temp"]},
                    {"directive": "uwsgi_temp_path", "args": ["/tmp/uwsgi_temp"]},
                    {"directive": "scgi_temp_path", "args": ["/tmp/scgi_temp"]},
                    # logging
                    {"directive": "default_type", "args": ["application/octet-stream"]},
                    {
                        "directive": "log_format",
                        "args": [
                            "main",
                            '$remote_addr - $remote_user [$time_local]  $status "$request" $body_bytes_sent "$http_referer" "$http_user_agent" "$http_x_forwarded_for"',
                        ],
                    },
                    *self._log_verbose(verbose=False),
                    *self._resolver(custom_resolver=None),
                    # TODO: add custom http block for the user to config?
                    {
                        "directive": "map",
                        "args": ["$http_x_scope_orgid", "$ensured_x_scope_orgid"],
                        "block": [
                            {"directive": "default", "args": ["$http_x_scope_orgid"]},
                            {"directive": "", "args": ["anonymous"]},
                        ],
                    },
                    {"directive": "proxy_read_timeout", "args": ["300"]},
                    # server block
                    self._server(tls),
                ],
            },
        ]

        return crossplane.build(full_config)

    @property
    def layer(self) -> Layer:
        """Return the Pebble layer for Nginx."""
        return Layer(
            {
                "summary": "nginx layer",
                "description": "pebble config layer for Nginx",
                "services": {
                    "nginx": {
                        "override": "replace",
                        "summary": "nginx",
                        "command": "nginx",
                        "startup": "enabled",
                    }
                },
            }
        )

    def _log_verbose(self, verbose: bool = True) -> List[Dict[str, Any]]:
        if verbose:
            return [{"directive": "access_log", "args": ["/dev/stderr", "main"]}]
        return [
            {
                "directive": "map",
                "args": ["$status", "$loggable"],
                "block": [
                    {"directive": "~^[23]", "args": ["0"]},
                    {"directive": "default", "args": ["1"]},
                ],
            },
            {"directive": "access_log", "args": ["/dev/stderr"]},
        ]

    def _upstreams(self, addresses_by_role: Dict[str, Set[str]]) -> List[Dict[str, Any]]:
        nginx_upstreams = []
        for role, address_set in addresses_by_role.items():
            nginx_upstreams.append(
                {
                    "directive": "upstream",
                    "args": [role],
                    "block": [
                        {"directive": "server", "args": [f"{addr}:8080"]} for addr in address_set
                    ],
                }
            )

        return nginx_upstreams

    def _resolver(self, custom_resolver: Optional[List[Any]] = None) -> List[Dict[str, Any]]:
        if custom_resolver:
            return [{"directive": "resolver", "args": [custom_resolver]}]
        return [{"directive": "resolver", "args": ["kube-dns.kube-system.svc.cluster.local."]}]

    def _basic_auth(self, enabled: bool) -> List[Optional[Dict[str, Any]]]:
        if enabled:
            return [
                {"directive": "auth_basic", "args": ['"Tempo"']},
                {
                    "directive": "auth_basic_user_file",
                    "args": ["/etc/nginx/secrets/.htpasswd"],
                },
            ]
        return []

    def _locations(self) -> List[Dict[str, Any]]:
        return [
            {
                "directive": "location",
                "args": ["/api/"],
                "block": [
                    {
                        "directive": "proxy_pass",
                        "args": ["http://api/"],
                    },
                ],
            },
            {
                "directive": "location",
                "args": ["/otlp_http/"],
                "block": [
                    {
                        "directive": "proxy_pass",
                        "args": ["http://otlp_http/"],
                    },
                ],
            },
        ]

    def _server(self, tls: bool = False) -> Dict[str, Any]:
        auth_enabled = False

        if tls:
            return {
                "directive": "server",
                "args": [],
                "block": [
                    {"directive": "listen", "args": ["443", "ssl"]},
                    {"directive": "listen", "args": ["[::]:443", "ssl"]},
                    *self._basic_auth(auth_enabled),
                    {
                        "directive": "location",
                        "args": ["=", "/"],
                        "block": [
                            {"directive": "return", "args": ["200", "'OK'"]},
                            {"directive": "auth_basic", "args": ["off"]},
                        ],
                    },
                    {
                        "directive": "proxy_set_header",
                        "args": ["X-Scope-OrgID", "$ensured_x_scope_orgid"],
                    },
                    # FIXME: use a suitable SERVER_NAME
                    {"directive": "server_name", "args": [self.server_name]},
                    {"directive": "ssl_certificate", "args": [CERT_PATH]},
                    {"directive": "ssl_certificate_key", "args": [KEY_PATH]},
                    {"directive": "ssl_protocols", "args": ["TLSv1", "TLSv1.1", "TLSv1.2"]},
                    {"directive": "ssl_ciphers", "args": ["HIGH:!aNULL:!MD5"]},  # pyright: ignore
                    # TODO what's tempo's equivalent of this one?
                    *self._locations(),
                ],
            }

        return {
            "directive": "server",
            "args": [],
            "block": [
                {"directive": "listen", "args": ["8080"]},
                {"directive": "listen", "args": ["[::]:8080"]},
                *self._basic_auth(auth_enabled),
                {
                    "directive": "location",
                    "args": ["=", "/"],
                    "block": [
                        {"directive": "return", "args": ["200", "'OK'"]},
                        {"directive": "auth_basic", "args": ["off"]},
                    ],
                },
                {
                    "directive": "proxy_set_header",
                    "args": ["X-Scope-OrgID", "$ensured_x_scope_orgid"],
                },
                *self._locations()
            ],
        }
