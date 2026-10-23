"""Shared runtime dependencies passed to every operation."""

from .fx import RateTable
from .ids import IdGenerator
from .store import Store


class Context:
    """Bundle of collaborators that every billing operation receives as `ctx`.

    `clock` provides `now()` in epoch seconds, `gateway` charges and refunds payments,
    `transport` posts webhooks, and `dunning_policy` decides how failed payments are
    retried. A fresh in-memory store, id generator and listener list are created per
    context, so two contexts never share state.
    """

    def __init__(self, clock, gateway, transport, dunning_policy, fx=None):
        self.clock = clock
        self.gateway = gateway
        self.transport = transport
        self.dunning_policy = dunning_policy
        self.fx = fx if fx is not None else RateTable()
        self.store = Store()
        self.ids = IdGenerator()
        self.listeners = []
