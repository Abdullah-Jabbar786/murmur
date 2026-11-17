"""Validated transport selection shared by the swarm CLI and local tests."""

from core.loopback import LoopbackTransport
from core.tcp import TcpTransport
from murmurtp.api import MurmurTransport
from murmurtp.flow import FlowControlConfig
from murmurtp.lossy import LossConfig


def create_transport(config, *, registry=None):
    if not isinstance(config, dict):
        raise ValueError("config must be a JSON object")
    name = config.get("transport", "tcp")
    if name == "tcp":
        return TcpTransport(congestion_control=config.get("tcp_congestion_control"))
    if name == "loopback":
        return LoopbackTransport(registry)
    if name != "murmurtp":
        raise ValueError("transport must be tcp, murmurtp or loopback")
    options, loss = config.get("murmurtp", {}), config.get("loss_shim", {})
    if not isinstance(options, dict) or not isinstance(loss, dict):
        raise ValueError("murmurtp and loss_shim settings must be JSON objects")
    try:
        return MurmurTransport(config=FlowControlConfig(**options), loss=LossConfig(**loss),
                               seed=config.get("seed", 0))
    except TypeError as exc:
        raise ValueError(f"invalid MurmurTP configuration: {exc}") from exc


def transport_port(config, explicit=None):
    name = config.get("transport", "tcp")
    if name not in ("tcp", "murmurtp"):
        raise ValueError("the swarm CLI requires transport tcp or murmurtp; loopback is in-process only")
    key, default = ("peer_port_udp", 6882) if name == "murmurtp" else ("peer_port_tcp", 6881)
    port = config.get(key, default) if explicit is None else explicit
    if type(port) is not int or not 0 <= port <= 65535:
        raise ValueError("peer port must be an integer from 0 to 65535")
    return port
