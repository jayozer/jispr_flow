"""Agent IPC: send dictated commands to poppy-desk over Unix socket or localhost."""

from local_flow.agent.sink import AgentSink, _get_default_agent_socket_path

__all__ = ["AgentSink", "_get_default_agent_socket_path"]
