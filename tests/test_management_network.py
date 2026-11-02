"""Security invariants for the operator's narrow Docker network reconciler."""

import importlib.util
from pathlib import Path

import pytest

SCRIPT = Path(__file__).parents[1] / "deploy/operations/management_network.py"
spec = importlib.util.spec_from_file_location("management_network", SCRIPT)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class DockerFixture:
    def __init__(self):
        self.groups = {
            "backend": [
                {"id": "backend-id", "name": "backend-new", "healthy": True, "ingress_safe": True}
            ],
            "dashboard": [
                {
                    "id": "dashboard-id",
                    "name": "dashboard-new",
                    "healthy": True,
                    "ingress_safe": True,
                }
            ],
        }
        self.network = None
        self.attached = {
            "backend-id": {"coolify": {"GwPriority": 0}},
            "dashboard-id": {"coolify": {"GwPriority": 0}},
        }
        self.mutations = []

    def targets(self, resource):
        return self.groups[resource]

    def inspect_network(self, name):
        return self.network

    def attachments(self, identifier):
        return self.attached[identifier]

    def run(self, *args):
        self.mutations.append(args)
        if args[:2] == ("network", "create"):
            self.network = {"Internal": True, "Driver": "bridge", "Containers": {}}
        elif args[:2] == ("network", "connect"):
            assert args[2:4] == ("--gw-priority", "-1")
            alias, network, identifier = args[5:]
            self.attached[identifier][network] = {"GwPriority": -1, "Aliases": [alias]}
            self.network["Containers"][identifier] = {}
        else:
            raise AssertionError("Unexpected Docker mutation")


def test_replacement_containers_are_attached_idempotently_without_changing_outbound_routes():
    engine = DockerFixture()
    assert module.reconcile(engine, "jobtology-management", "backend", "dashboard") == 2
    assert module.reconcile(engine, "jobtology-management", "backend", "dashboard") == 0
    for attachments in engine.attached.values():
        assert attachments["coolify"]["GwPriority"] == 0
    engine.groups["backend"] = [
        {
            "id": "replacement-id",
            "name": "backend-replacement",
            "healthy": True,
            "ingress_safe": True,
        }
    ]
    engine.attached["replacement-id"] = {"coolify": {"GwPriority": 0}}
    del engine.network["Containers"]["backend-id"]
    assert module.reconcile(engine, "jobtology-management", "backend", "dashboard") == 1
    assert engine.attached["replacement-id"]["jobtology-management"]["Aliases"] == [
        "jobtology-management-api"
    ]


@pytest.mark.parametrize(
    "network",
    [
        {"Internal": False, "Driver": "bridge", "Containers": {}},
        {"Internal": True, "Driver": "overlay", "Containers": {}},
        {"Internal": True, "Driver": "bridge", "Containers": {"unrelated-app": {}}},
    ],
)
def test_existing_untrusted_network_is_rejected_before_any_mutation(network):
    engine = DockerFixture()
    engine.network = network
    with pytest.raises(module.NetworkError):
        module.reconcile(engine, "jobtology-management", "backend", "dashboard")
    assert not engine.mutations


def test_unhealthy_applications_are_not_exposed_on_management_alias():
    engine = DockerFixture()
    engine.groups["backend"][0]["healthy"] = False
    assert module.reconcile(engine, "jobtology-management", "backend", "dashboard") == 0
    assert not engine.mutations


@pytest.mark.parametrize("resource", ["backend", "dashboard"])
def test_ambiguous_proxy_network_is_rejected_before_any_attachment(resource):
    engine = DockerFixture()
    engine.groups[resource][0]["ingress_safe"] = False
    with pytest.raises(module.NetworkError, match="Pin Traefik ingress"):
        module.reconcile(engine, "jobtology-management", "backend", "dashboard")
    assert not engine.mutations


@pytest.mark.parametrize(
    "endpoint",
    [
        {"GwPriority": -1, "Aliases": ["wrong-alias"]},
        {"GwPriority": 1, "Aliases": ["jobtology-management-api"]},
    ],
)
def test_unexpected_attachment_does_not_silently_rewire_active_containers(endpoint):
    engine = DockerFixture()
    engine.network = {"Internal": True, "Driver": "bridge", "Containers": {"backend-id": {}}}
    engine.attached["backend-id"]["jobtology-management"] = endpoint
    with pytest.raises(module.NetworkError, match="operator repair"):
        module.reconcile(engine, "jobtology-management", "backend", "dashboard")
    assert not engine.mutations


def test_container_selection_uses_managed_resource_labels_and_never_reads_env_or_commands(
    monkeypatch,
):
    calls = []
    resource = "w131qqclc6wyke8347y9puqo"

    class Result:
        returncode = 0
        stdout = ""

    def run(args, **kwargs):
        calls.append(args)
        result = Result()
        if "ps" in args:
            result.stdout = "a" * 64 + " " + resource + "-20261102T125018"
        elif any(".Config.Labels" in arg for arg in args):
            result.stdout = "true coolify"
        else:
            result.stdout = "true healthy"
        assert not any(key.startswith("DOCKER_") for key in kwargs["env"])
        return result

    monkeypatch.setenv("DOCKER_HOST", "tcp://untrusted.invalid:2375")
    monkeypatch.setattr(module.subprocess, "run", run)
    targets = module.Engine().targets(resource)
    assert targets[0]["healthy"]
    assert targets[0]["ingress_safe"]
    assert all(
        args[:3] == ["/usr/bin/docker", "--host", "unix:///var/run/docker.sock"] for args in calls
    )
    assert "label=coolify.name=" + resource in calls[0]
    assert not any(
        field in part for args in calls for part in args for field in (".Config.Env", ".Config.Cmd")
    )


@pytest.mark.parametrize(
    ("labels", "safe"),
    [("true coolify", True), ("true ", False), ("true another-network", False), (" ", True)],
)
def test_ingress_pin_is_required_only_for_traefik_exposed_containers(monkeypatch, labels, safe):
    resource = "w131qqclc6wyke8347y9puqo"

    def run(self, *args, **kwargs):
        if args[0] == "ps":
            return "a" * 64 + " " + resource + "-20261102T125018"
        if any(".Config.Labels" in arg for arg in args):
            return labels
        return "true healthy"

    monkeypatch.setattr(module.Engine, "run", run)
    assert module.Engine().targets(resource)[0]["ingress_safe"] is safe
