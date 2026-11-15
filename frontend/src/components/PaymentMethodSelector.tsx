import React from "react";
import { Banknote, CreditCard, ShieldCheck, AlertCircle } from "lucide-react";

export type MetodoPago = "efectivo" | "tarjeta";

export interface PaymentMethodSelectorProps {
  value: MetodoPago;
  onChange?: (metodo: MetodoPago) => void;
  montoBase?: number | string | null;
  disabled?: boolean;
  readOnly?: boolean;
  showSummary?: boolean;
  idPrefix?: string;
}

export const PaymentMethodSelector: React.FC<PaymentMethodSelectorProps> = ({
  value,
  onChange,
  montoBase,
  disabled = false,
  readOnly = false,
  showSummary = true,
  idPrefix = "payment-method-selector",
}) => {
  const isEfectivo = value === "efectivo";

  const handleChange = (e: React.ChangeEvent<HTMLSelectElement>) => {
    if (disabled || readOnly || !onChange) return;
    onChange(e.target.value as MetodoPago);
  };

  const formattedMonto =
    montoBase !== null && montoBase !== undefined && !isNaN(Number(montoBase))
      ? `L. ${Number(montoBase).toFixed(2)}`
      : null;

  return (
    <div className="payment-method-container">
      <label className="field" htmlFor={idPrefix}>
        
        <select
          id={idPrefix}
          value={value}
          onChange={handleChange}
          disabled={disabled || readOnly}
          className="payment-method-select"
        >
          <option value="efectivo">Efectivo (pago directo contra entrega)</option>
          <option value="tarjeta">Tarjeta de crédito / débito (pasarela segura)</option>
        </select>
      </label>

      {readOnly && (
        <div className="payment-method-readonly-badge" role="status">
          <span>Acuerdo formalizado: El método de pago acordado no puede modificarse tras la aceptación del servicio.</span>
        </div>
      )}

      {/* Tarjeta informativa dinámica según el método seleccionado */}
      <div className="payment-method-info-banner">
        <div className="payment-method-info-icon">
          {isEfectivo ? (
            <Banknote size={20} aria-hidden="true" />
          ) : (
            <CreditCard size={20} aria-hidden="true" />
          )}
        </div>
        <div className="payment-method-info-text">
          <strong>{isEfectivo ? "Pago en efectivo" : "Pago con tarjeta bancaria"}</strong>
          <p>
            {isEfectivo
              ? "Sin cobro digital previo. Se liquida contra entrega según lo acordado con el técnico."
              : "Pago protegido mediante pasarela bancaria. Se solicitará la autorización digital segura antes de que el técnico confirme la solicitud."}
          </p>
        </div>
      </div>

      {/* Resumen previo a la confirmación */}
      {showSummary && (
        <div className="payment-method-summary" role="region" aria-label="Resumen de acuerdo de pago">
          <div className="payment-summary-row">
            <span>Presupuesto base acordado:</span>
            <strong>{formattedMonto || "Por confirmar"}</strong>
          </div>
          <div className="payment-summary-row">
            <span>Método formalizado:</span>
            <strong style={{ textTransform: "capitalize" }}>{value}</strong>
          </div>
          <div className="payment-summary-adjustments">
            <AlertCircle size={15} aria-hidden="true" />
            <span>
              <strong>Tratamiento de ajustes aprobados:</strong> Si durante el trabajo surgen variaciones de alcance aprobadas, se mantendrán las condiciones del acuerdo y podrá registrarse un método de pago independiente solo por la diferencia.
            </span>
          </div>
          <div className="payment-summary-security">
            <ShieldCheck size={15} aria-hidden="true" />
            <span>
              Seguridad garantizada: TekniGO procesa transacciones mediante pasarela certificada y no almacena números de tarjeta ni códigos de seguridad en sus servidores.
            </span>
          </div>
        </div>
      )}
    </div>
  );
};

export default PaymentMethodSelector;