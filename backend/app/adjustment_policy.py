"""HU-36 ST-03: reglas de validación del ajuste único (cadena de reglas, OCP vía interfaz)."""
from abc import ABC, abstractmethod
from dataclasses import dataclass
from decimal import Decimal
from typing import Sequence


class AdjustmentRuleViolation(Exception):
    def __init__(self, status_code: int, message: str):
        super().__init__(message)
        self.status_code = status_code
        self.message = message


@dataclass(frozen=True)
class AdjustmentContext:
    actor_is_selected_technician: bool
    request_state: str          # normalizado con state_key()
    already_requested: bool     # existe un ajuste previo, en cualquier estado
    monto_base: Decimal
    nuevo_total: Decimal
    agreed_total: Decimal       # total financiero vigente
    has_active_incident: bool = False


class AdjustmentRule(ABC):
    @abstractmethod
    def validate(self, ctx: AdjustmentContext) -> None:
        """Lanza AdjustmentRuleViolation si no se cumple."""


class SelectedTechnicianRule(AdjustmentRule):
    def validate(self, ctx):
        if not ctx.actor_is_selected_technician:
            raise AdjustmentRuleViolation(403, "Solo el técnico seleccionado puede solicitar un ajuste.")


class InProgressRule(AdjustmentRule):
    def validate(self, ctx):
        if ctx.request_state != "EN PROCESO":
            raise AdjustmentRuleViolation(409, "El ajuste solo puede solicitarse mientras el servicio está En proceso.")


class SingleAdjustmentRule(AdjustmentRule):
    def validate(self, ctx):
        if ctx.already_requested:
            raise AdjustmentRuleViolation(
                409, "Solo se permite un ajuste por servicio, aunque el anterior haya sido rechazado."
            )


class PositiveDistinctAmountsRule(AdjustmentRule):
    def validate(self, ctx):
        if ctx.monto_base <= 0 or ctx.nuevo_total <= 0:
            raise AdjustmentRuleViolation(400, "El monto base y el nuevo total deben ser positivos.")
        if ctx.nuevo_total == ctx.monto_base:
            raise AdjustmentRuleViolation(400, "El nuevo total debe ser distinto del monto base.")


class BaseMatchesAgreedTotalRule(AdjustmentRule):
    def validate(self, ctx):
        if ctx.monto_base != ctx.agreed_total:
            raise AdjustmentRuleViolation(
                409, f"El monto base no coincide con el total acordado vigente (L. {ctx.agreed_total})."
            )

class NoActiveIncidentRule(AdjustmentRule):
    def validate(self, ctx):
        if ctx.has_active_incident:
            raise AdjustmentRuleViolation(
                409, "No se puede solicitar un ajuste mientras exista una incidencia abierta en revisión administrativa."
            )

class AdjustmentPolicy:
    def __init__(self, rules: Sequence[AdjustmentRule]):
        self._rules = tuple(rules)

    def check(self, ctx: AdjustmentContext) -> None:
        for rule in self._rules:
            rule.validate(ctx)


def default_adjustment_policy() -> AdjustmentPolicy:
    return AdjustmentPolicy([
        SelectedTechnicianRule(),
        InProgressRule(),
        NoActiveIncidentRule(),
        SingleAdjustmentRule(),
        PositiveDistinctAmountsRule(),
        BaseMatchesAgreedTotalRule(),
    ])