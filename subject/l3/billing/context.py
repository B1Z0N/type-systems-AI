"""Shared runtime dependencies passed to every operation."""

from .fx import RateTable
from .ids import IdGenerator
from .models import Clock, DunningPolicy, EventListener, PaymentGateway, Transport
from .store import Store


class Context:
    def __init__(
        self,
        clock: Clock,
        gateway: PaymentGateway,
        transport: Transport,
        dunning_policy: DunningPolicy,
        fx: RateTable | None = None,
    ) -> None:
        self.clock = clock
        self.gateway = gateway
        self.transport = transport
        self.dunning_policy = dunning_policy
        self.fx = fx if fx is not None else RateTable()
        self.store = Store()
        self.ids = IdGenerator()
        self.listeners: list[EventListener] = []
