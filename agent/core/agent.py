"""Agent wrapper exposing the required interface."""

import logging

from dotenv import load_dotenv

from ..domain.state import ConversationState
from .state_machine import PaymentStateMachine

logger = logging.getLogger(__name__)


class Agent:
    def __init__(self) -> None:
        load_dotenv()
        self._state = ConversationState()
        self._machine = PaymentStateMachine(self._state)
        logger.info("Agent session started")

    def next(self, user_input: str) -> dict:
        message = self._machine.handle_turn(user_input)
        return {"message": message}
