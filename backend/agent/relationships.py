"""Stage 3 of the pipeline: relationship / graph analysis.

Transactions are modelled as a directed multigraph whose nodes are entities
(accounts, users, banks, gateways, merchants) and whose edges are individual
transfers. Implemented with plain dicts so the package stays dependency-free.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Any, Iterable, Sequence

from .models import Entity, Transaction

__all__ = ["Edge", "EntityGraph", "build_graph", "summarize_entities"]


@dataclass(slots=True, frozen=True)
class Edge:
    """A directed transfer between two entities."""

    source: str
    target: str
    transaction_id: str
    timestamp: datetime | None
    amount: Decimal | None
    currency: str | None
    status: str


@dataclass(slots=True)
class EntityGraph:
    """A directed transaction graph with the queries the detectors need."""

    edges: list[Edge] = field(default_factory=list)
    #: entity id -> declared type ("account", "bank", "gateway", …)
    node_types: dict[str, str] = field(default_factory=dict)
    #: entity id -> set of entities it is associated with (undirected)
    associations: dict[str, set[str]] = field(default_factory=lambda: defaultdict(set))

    # -- construction ----------------------------------------------------- #
    def add_node(self, entity_id: str, entity_type: str) -> None:
        """Register ``entity_id``; the more specific type wins on conflict."""
        if not entity_id:
            return
        existing = self.node_types.get(entity_id)
        if existing is None or (existing == "unknown" and entity_type != "unknown"):
            self.node_types[entity_id] = entity_type

    def associate(self, left: str | None, right: str | None) -> None:
        """Record a non-directional association (e.g. account ↔ bank)."""
        if not left or not right or left == right:
            return
        self.associations[left].add(right)
        self.associations[right].add(left)

    def add_edge(self, edge: Edge) -> None:
        self.edges.append(edge)
        self.associate(edge.source, edge.target)

    # -- queries ---------------------------------------------------------- #
    @property
    def nodes(self) -> list[str]:
        return sorted(self.node_types.keys())

    def outgoing(self, node: str) -> list[Edge]:
        return [e for e in self.edges if e.source == node]

    def incoming(self, node: str) -> list[Edge]:
        return [e for e in self.edges if e.target == node]

    def out_neighbours(self, node: str) -> set[str]:
        return {e.target for e in self.edges if e.source == node}

    def in_neighbours(self, node: str) -> set[str]:
        return {e.source for e in self.edges if e.target == node}

    def adjacency(self) -> dict[str, set[str]]:
        """Directed adjacency map, collapsing parallel edges."""
        adjacency: dict[str, set[str]] = defaultdict(set)
        for edge in self.edges:
            adjacency[edge.source].add(edge.target)
        return adjacency

    def find_cycles(self, max_length: int = 5) -> list[list[str]]:
        """Return simple directed cycles up to ``max_length`` nodes.

        Cycles are de-duplicated by their rotation-invariant node set order so
        A→B→A and B→A→B are reported once.
        """
        adjacency = self.adjacency()
        found: dict[tuple[str, ...], list[str]] = {}

        def walk(start: str, node: str, path: list[str], seen: set[str]) -> None:
            for neighbour in sorted(adjacency.get(node, ())):
                if neighbour == start and len(path) >= 2:
                    cycle = list(path)
                    pivot = cycle.index(min(cycle))
                    key = tuple(cycle[pivot:] + cycle[:pivot])
                    found.setdefault(key, cycle)
                elif neighbour not in seen and len(path) < max_length:
                    walk(start, neighbour, path + [neighbour], seen | {neighbour})

        for start in sorted(adjacency.keys()):
            walk(start, start, [start], {start})
        return list(found.values())

    def find_paths(
        self,
        min_length: int = 3,
        max_length: int = 4,
        time_ordered: bool = True,
        result_limit: int = 200,
    ) -> list[list[Edge]]:
        """Find directed paths of ``min_length``..``max_length`` edges + 1 nodes.

        With ``time_ordered`` the timestamps along the path must be
        non-decreasing, which is what makes a path evidence of a *flow* of
        funds rather than an incidental co-occurrence.
        """
        by_source: dict[str, list[Edge]] = defaultdict(list)
        for edge in self.edges:
            by_source[edge.source].append(edge)

        results: list[list[Edge]] = []

        def extend(chain: list[Edge], visited: set[str]) -> None:
            if len(results) >= result_limit:
                return
            if len(chain) + 1 >= min_length:
                results.append(list(chain))
            if len(chain) + 1 >= max_length:
                return
            last = chain[-1]
            for nxt in by_source.get(last.target, ()):
                if nxt.target in visited:
                    continue
                if time_ordered:
                    if last.timestamp is None or nxt.timestamp is None:
                        continue
                    if nxt.timestamp < last.timestamp:
                        continue
                extend(chain + [nxt], visited | {nxt.target})

        for edge in self.edges:
            if len(results) >= result_limit:
                break
            extend([edge], {edge.source, edge.target})
        return results

    def connected_components(self) -> list[set[str]]:
        """Undirected connected components over the association map."""
        seen: set[str] = set()
        components: list[set[str]] = []
        for node in self.nodes:
            if node in seen:
                continue
            stack = [node]
            component: set[str] = set()
            while stack:
                current = stack.pop()
                if current in component:
                    continue
                component.add(current)
                stack.extend(self.associations.get(current, set()) - component)
            seen |= component
            components.append(component)
        return components

    def to_dict(self) -> dict[str, Any]:
        return {
            "node_count": len(self.node_types),
            "edge_count": len(self.edges),
            "nodes": [{"id": n, "type": t} for n, t in sorted(self.node_types.items())],
            "edges": [
                {
                    "source": e.source,
                    "target": e.target,
                    "transaction_id": e.transaction_id,
                    "amount": float(e.amount) if e.amount is not None else None,
                    "currency": e.currency,
                    "status": e.status,
                }
                for e in self.edges
            ],
            "component_count": len(self.connected_components()),
        }


def build_graph(transactions: Sequence[Transaction]) -> EntityGraph:
    """Build the entity graph for a set of normalized transactions."""
    graph = EntityGraph()

    for txn in transactions:
        source = txn.sender or f"<unknown-sender:{txn.transaction_id}>"
        target = txn.receiver or f"<unknown-receiver:{txn.transaction_id}>"
        graph.add_node(source, "account")
        graph.add_node(target, "account")

        if txn.account_id:
            graph.add_node(txn.account_id, "account")
            graph.associate(txn.account_id, source)
            graph.associate(txn.account_id, target)
        if txn.bank:
            graph.add_node(txn.bank, "bank")
            graph.associate(txn.bank, txn.account_id or source)
        if txn.gateway:
            graph.add_node(txn.gateway, "gateway")
            graph.associate(txn.gateway, source)
            graph.associate(txn.gateway, target)

        graph.add_edge(
            Edge(
                source=source,
                target=target,
                transaction_id=txn.transaction_id,
                timestamp=txn.timestamp,
                amount=txn.amount,
                currency=txn.currency,
                status=txn.status.value,
            )
        )

    return graph


def summarize_entities(
    transactions: Sequence[Transaction],
    graph: EntityGraph,
    flagged_entities: Iterable[str] = (),
) -> list[Entity]:
    """Roll transactions up into :class:`Entity` records for the report.

    ``flagged_entities`` are entity ids that pattern detection implicated;
    they receive a ``"implicated_by_pattern"`` flag.
    """
    flagged = set(flagged_entities)
    entities: dict[str, Entity] = {}

    def ensure(entity_id: str, entity_type: str) -> Entity:
        entity = entities.get(entity_id)
        if entity is None:
            entity = Entity(entity_id=entity_id, entity_type=entity_type)
            entities[entity_id] = entity
        return entity

    for txn in transactions:
        if txn.sender:
            sender = ensure(txn.sender, graph.node_types.get(txn.sender, "account"))
            sender.transaction_count += 1
            sender.outbound_count += 1
            if "sender" not in sender.roles:
                sender.roles.append("sender")
            if txn.amount is not None:
                sender.total_amount = (sender.total_amount or Decimal("0")) + txn.amount
            if txn.currency and txn.currency not in sender.currencies:
                sender.currencies.append(txn.currency)

        if txn.receiver:
            receiver = ensure(txn.receiver, graph.node_types.get(txn.receiver, "account"))
            receiver.transaction_count += 1
            receiver.inbound_count += 1
            if "receiver" not in receiver.roles:
                receiver.roles.append("receiver")
            if txn.amount is not None:
                receiver.total_amount = (receiver.total_amount or Decimal("0")) + txn.amount
            if txn.currency and txn.currency not in receiver.currencies:
                receiver.currencies.append(txn.currency)

        for value, kind, role in (
            (txn.bank, "bank", "institution"),
            (txn.gateway, "gateway", "processor"),
            (txn.account_id, "account", "account_of_record"),
        ):
            if not value:
                continue
            entity = ensure(value, kind)
            entity.transaction_count += 1
            if role not in entity.roles:
                entity.roles.append(role)

    for entity_id, entity in entities.items():
        entity.linked_entities = sorted(graph.associations.get(entity_id, set()))
        if entity_id in flagged:
            entity.flags.append("implicated_by_pattern")
        if entity_id.startswith("<unknown-"):
            entity.flags.append("identity_unknown")
        if len(entity.currencies) > 1:
            entity.flags.append("multi_currency")

    return sorted(
        entities.values(), key=lambda e: (-e.transaction_count, e.entity_id)
    )
