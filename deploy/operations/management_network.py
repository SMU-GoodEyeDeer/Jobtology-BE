#!/usr/bin/env python3
"""Maintain private management attachments for two allowlisted Coolify resources.

Run on the Docker host as its existing operator; never mount the Docker socket
into either application. No environment variables, credentials or container
commands are inspected. --watch uses a private lock so cron can restart it safely.
"""

from __future__ import annotations

import argparse
import fcntl
import json
import logging
import os
import re
import subprocess
import time
from logging.handlers import RotatingFileHandler
from pathlib import Path

LOG = logging.getLogger("jobtology.management-network")
DOCKER = ["/usr/bin/docker", "--host", "unix:///var/run/docker.sock"]


class NetworkError(Exception):
    """A Docker operation or the required isolation invariant failed."""


class Engine:
    def run(self, *args: str, optional: bool = False) -> str | None:
        result = subprocess.run(
            [*DOCKER, *args],
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
            env={key: value for key, value in os.environ.items() if not key.startswith("DOCKER_")},
        )
        if result.returncode:
            if optional:
                return None
            # Do not copy arbitrary daemon output or configuration into logs.
            raise NetworkError(f"Docker {args[0]} failed (exit {result.returncode})")
        return result.stdout.strip()

    def targets(self, resource: str) -> list[dict]:
        raw = self.run(
            "ps",
            "--all",
            "--no-trunc",
            "--filter",
            "label=coolify.managed=true",
            "--filter",
            "label=coolify.type=application",
            "--filter",
            f"label=coolify.name={resource}",
            "--format",
            "{{.ID}} {{.Names}}",
        )
        targets = []
        for line in (raw or "").splitlines():
            identifier, name = line.split(maxsplit=1)
            if not re.fullmatch(r"[a-f0-9]{64}", identifier) or not re.fullmatch(
                re.escape(resource) + r"-\d{8}T\d{6}", name
            ):
                raise NetworkError("Unexpected Coolify application identity")
            status = self.run(
                "inspect",
                "--format",
                "{{.State.Running}} {{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}",
                identifier,
            )
            ingress = self.run(
                "inspect",
                "--format",
                '{{index .Config.Labels "traefik.enable"}} {{index .Config.Labels "traefik.docker.network"}}',
                identifier,
            )
            enabled, _, ingress_network = (ingress or "").partition(" ")
            targets.append(
                {
                    "id": identifier,
                    "name": name,
                    "healthy": status == "true healthy",
                    "ingress_safe": enabled != "true" or ingress_network == "coolify",
                }
            )
        return targets

    def inspect_network(self, name: str) -> dict | None:
        raw = self.run("network", "inspect", name, optional=True)
        return json.loads(raw)[0] if raw is not None else None

    def attachments(self, identifier: str) -> dict:
        raw = self.run("inspect", "--format", "{{json .NetworkSettings.Networks}}", identifier)
        return json.loads(raw or "{}")


def reconcile(engine: Engine, network: str, backend: str, dashboard: str) -> int:
    """Attach healthy approved containers without changing their ingress or default route."""
    groups = [
        (engine.targets(backend), "jobtology-management-api"),
        (engine.targets(dashboard), "jobtology-management-dashboard"),
    ]
    approved = {target["id"] for targets, _ in groups for target in targets}
    info = engine.inspect_network(network)
    if info is not None:
        if info.get("Driver") != "bridge" or info.get("Internal") is not True:
            raise NetworkError("Management network must be an internal bridge")
        if set(info.get("Containers", {})) - approved:
            raise NetworkError("Management network contains an unapproved container")
    if any(not any(target["healthy"] for target in targets) for targets, _ in groups):
        return 0  # Wait for normal Coolify healthchecks; do not start or stop applications.
    if any(
        target["healthy"] and not target["ingress_safe"]
        for targets, _ in groups
        for target in targets
    ):
        raise NetworkError("Pin Traefik ingress to coolify before adding a management attachment")
    if info is None:
        engine.run(
            "network",
            "create",
            "--driver",
            "bridge",
            "--internal",
            "--label",
            "jobtology.management=private",
            network,
        )
        LOG.info("Created internal management bridge %s", network)
    connected = 0
    for targets, alias in groups:
        for target in targets:
            if not target["healthy"]:
                continue
            attached = engine.attachments(target["id"])
            existing = attached.get(network)
            if existing is not None:
                if alias not in (existing.get("Aliases") or []) or existing.get("GwPriority") != -1:
                    raise NetworkError("Existing management attachment needs operator repair")
                continue
            engine.run(
                "network", "connect", "--gw-priority", "-1", "--alias", alias, network, target["id"]
            )
            LOG.info("Attached healthy application %s", target["name"])
            connected += 1
    return connected


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--backend-resource", default="w131qqclc6wyke8347y9puqo")
    parser.add_argument("--dashboard-resource", default="d0n6yjniwrcfoz0oppkjhjua")
    parser.add_argument("--network", default="jobtology-management")
    parser.add_argument("--watch", action="store_true")
    parser.add_argument(
        "--state-dir", type=Path, default=Path.home() / ".local/state/jobtology-management-network"
    )
    args = parser.parse_args()
    for resource in (args.backend_resource, args.dashboard_resource):
        if not re.fullmatch(r"[a-z0-9]{8,64}", resource):
            parser.error("Resource IDs must be lowercase alphanumeric Coolify IDs")
    if args.backend_resource == args.dashboard_resource:
        parser.error("Backend and dashboard resources must differ")
    if not re.fullmatch(r"jobtology-management(?:-[a-z0-9]+)?", args.network):
        parser.error("Use the dedicated management network name")
    if not args.watch:
        logging.basicConfig(level=logging.INFO, format="%(message)s")
        print(
            f"Management attachments added: {reconcile(Engine(), args.network, args.backend_resource, args.dashboard_resource)}"
        )
        return
    os.umask(0o077)
    state = args.state_dir
    state.mkdir(parents=True, exist_ok=True, mode=0o700)
    if state.is_symlink() or state.stat().st_uid != os.getuid() or state.stat().st_mode & 0o077:
        parser.error("State directory must be private and owned by the operator")
    lock_fd = os.open(state / "watch.lock", os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    with os.fdopen(lock_fd, "r+") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return  # The existing watcher owns this work; cron does not duplicate it.
        lock.seek(0)
        lock.truncate()
        lock.write(str(os.getpid()))
        lock.flush()
        log_path = state / "watch.log"
        fd = os.open(log_path, os.O_APPEND | os.O_CREAT | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
        os.close(fd)
        handler = RotatingFileHandler(log_path, maxBytes=1_048_576, backupCount=1)
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
        LOG.addHandler(handler)
        LOG.setLevel(logging.INFO)
        LOG.info("Management attachment watcher started")
        last_error = None
        while True:
            try:
                reconcile(Engine(), args.network, args.backend_resource, args.dashboard_resource)
            except (NetworkError, OSError, ValueError, subprocess.TimeoutExpired) as error:
                message = str(error) if isinstance(error, NetworkError) else type(error).__name__
                if message != last_error:
                    LOG.error("Reconciliation unavailable: %s", message)
                last_error = message
            else:
                if last_error is not None:
                    LOG.info("Management attachment reconciliation recovered")
                last_error = None
            time.sleep(10)


if __name__ == "__main__":
    main()
