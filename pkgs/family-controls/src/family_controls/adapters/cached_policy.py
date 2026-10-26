"""Restore backend policy before local enforcement starts, including offline boots."""

from ..config import AgentConfig, User
from ..domain import InvalidCommand
from .sqlite import Repository


def load_cached_policy(config: AgentConfig, repository: Repository) -> None:
    from .timekpr import validate_config

    cached = repository.relay_get("backend-policy")
    if isinstance(cached, dict):
        users = cached["users"]
        if set(users) != set(config.managedUsers):
            raise InvalidCommand("Cached policy must match configured local users")
        config.managedUsers = {user: User.model_validate(value) for user, value in users.items()}
        validate_config(config)
