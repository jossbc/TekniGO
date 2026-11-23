"""HU-36 ST-01: matriz de causas y plazos de cancelación (Strategy, OCP)."""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import Enum
from typing import Sequence

# Decisión pendiente: exactamente 24 h => sin penalización (>=).
CLIENT_FREE_CANCELLATION_WINDOW = timedelta(hours=24)

PRE_ACCEPTANCE_STATES = frozenset({"PENDIENTE", "PUBLICADA", "CON OFERTAS"})
POST_ACCEPTANCE_STATES = frozenset({"ACEPTADA", "EN PROCESO"})


class CancellationCause(str, Enum):
    VOLUNTARIA = "VOLUNTARIA"
    INASISTENCIA_CONTRAPARTE = "INASISTENCIA_CONTRAPARTE"
    AJUSTE_RECHAZADO = "AJUSTE_RECHAZADO"  # lo habilita ST-03


class FaultStatus(str, Enum):
    NO_APLICA = "NO_APLICA"
    PENDIENTE_JUSTIFICACION = "PENDIENTE_JUSTIFICACION"
    JUSTIFICADA = "JUSTIFICADA"          # CA-09 (la usará la justificación)
    NO_JUSTIFICADA = "NO_JUSTIFICADA"    # CA-09 rechazada


class CancellationNotAllowed(Exception):
    """Ninguna regla de la matriz cubre la combinación causa/estado/rol."""


@dataclass(frozen=True)
class CancellationContext:
    previous_state: str      # ya normalizado con state_key()
    actor_role: str          # "CLIENTE" | "TECNICO"
    cause: CancellationCause
    scheduled_at: datetime   # UTC con tz
    now: datetime            # UTC con tz
    adjustment_rejected: bool = False


@dataclass(frozen=True)
class CancellationDecision:
    rule: str
    counts_fault: bool
    responsible_role: str | None = None
    allows_reopen: bool = False


def _other(role: str) -> str:
    return "TECNICO" if role == "CLIENTE" else "CLIENTE"


class CancellationRule(ABC):
    """Contrato: cada regla decide si aplica y qué consecuencias tiene."""

    @abstractmethod
    def applies(self, ctx: CancellationContext) -> bool: ...

    @abstractmethod
    def decide(self, ctx: CancellationContext) -> CancellationDecision: ...


class RejectedAdjustmentRule(CancellationRule):
    """CA-06: tras un ajuste rechazado cualquiera cancela sin penalización."""

    def applies(self, ctx):
                return (ctx.cause is CancellationCause.AJUSTE_RECHAZADO
                and ctx.previous_state == "EN PROCESO" and ctx.adjustment_rejected)

    def decide(self, ctx):
        return CancellationDecision(rule="AJUSTE_RECHAZADO", counts_fault=False)


class CounterpartNoShowRule(CancellationRule):
    """CA-02/CA-03: la inasistencia se imputa a la contraparte de quien cancela."""

    def applies(self, ctx):
        return (ctx.cause is CancellationCause.INASISTENCIA_CONTRAPARTE
                and ctx.previous_state in POST_ACCEPTANCE_STATES)

    def decide(self, ctx):
        responsible = _other(ctx.actor_role)
        return CancellationDecision(
            rule="INASISTENCIA_CONTRAPARTE", counts_fault=True,
            responsible_role=responsible, allows_reopen=responsible == "TECNICO",
        )


class PreAcceptanceRule(CancellationRule):
    """CA-02: antes de Aceptada nadie incurre en falta."""

    def applies(self, ctx):
        return ctx.cause is CancellationCause.VOLUNTARIA and ctx.previous_state in PRE_ACCEPTANCE_STATES

    def decide(self, ctx):
        return CancellationDecision(rule="ANTES_DE_ACEPTADA", counts_fault=False)


class ClientVoluntaryAfterAcceptanceRule(CancellationRule):
    """CA-02: cliente con >= 24 h sin falta; con menos, incumplimiento."""

    def __init__(self, free_window: timedelta = CLIENT_FREE_CANCELLATION_WINDOW):
        self._free_window = free_window

    def applies(self, ctx):
        return (ctx.cause is CancellationCause.VOLUNTARIA and ctx.actor_role == "CLIENTE"
                and ctx.previous_state in POST_ACCEPTANCE_STATES)

    def decide(self, ctx):
        if ctx.scheduled_at - ctx.now >= self._free_window:
            return CancellationDecision(rule="CLIENTE_CON_ANTICIPACION", counts_fault=False)
        return CancellationDecision(
            rule="CLIENTE_TARDIA", counts_fault=True, responsible_role="CLIENTE",
        )


class TechnicianVoluntaryAfterAcceptanceRule(CancellationRule):
    """CA-03: el técnico que cancela tras aceptar incumple y el cliente puede reabrir."""

    def applies(self, ctx):
        return (ctx.cause is CancellationCause.VOLUNTARIA and ctx.actor_role == "TECNICO"
                and ctx.previous_state in POST_ACCEPTANCE_STATES)

    def decide(self, ctx):
        return CancellationDecision(
            rule="TECNICO_TRAS_ACEPTAR", counts_fault=True,
            responsible_role="TECNICO", allows_reopen=True,
        )


class CancellationPolicy:
    """Depende de la abstracción CancellationRule, no de reglas concretas."""

    def __init__(self, rules: Sequence[CancellationRule]):
        self._rules = tuple(rules)

    def evaluate(self, ctx: CancellationContext) -> CancellationDecision:
        for rule in self._rules:
            if rule.applies(ctx):
                return rule.decide(ctx)
        raise CancellationNotAllowed(
            f"No se puede cancelar desde {ctx.previous_state} con la causa {ctx.cause.value}."
        )


def default_cancellation_policy() -> CancellationPolicy:
    return CancellationPolicy([
        RejectedAdjustmentRule(),
        CounterpartNoShowRule(),
        PreAcceptanceRule(),
        ClientVoluntaryAfterAcceptanceRule(),
        TechnicianVoluntaryAfterAcceptanceRule(),
    ])