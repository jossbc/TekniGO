import React, { useState, useEffect } from "react";
import { Lock, ShieldCheck, CreditCard, X, AlertCircle } from "lucide-react";

export interface CardPaymentResult {
  referenciaPasarela: string;
  marcaTarjeta: string;
  ultimosCuatro: string;
}

interface SecurePaymentGatewayModalProps {
  monto: number;
  onSuccess: (result: CardPaymentResult) => void;
  onCancel: () => void;
  titulo?: string;
}

export const SecurePaymentGatewayModal: React.FC<SecurePaymentGatewayModalProps> = ({
  monto,
  onSuccess,
  onCancel,
  titulo = "Pago de prueba",
}) => {
  const [nombreTitular, setNombreTitular] = useState("");
  const [numeroTarjeta, setNumeroTarjeta] = useState("");
  const [expiracion, setExpiracion] = useState("");
  const [cvv, setCvv] = useState("");
  const [processing, setProcessing] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Formato automático de tarjeta (grupos de 4)
  const handleCardNumberChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const raw = e.target.value.replace(/\D/g, "").slice(0, 16);
    const formatted = raw.replace(/(\d{4})/g, "$1 ").trim();
    setNumeroTarjeta(formatted);
  };

  // Formato automático de expiración (MM/AA)
  const handleExpiryChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const raw = e.target.value.replace(/\D/g, "").slice(0, 4);
    if (raw.length >= 3) {
      setExpiracion(`${raw.slice(0, 2)}/${raw.slice(2)}`);
    } else {
      setExpiracion(raw);
    }
  };

  const detectBrand = (num: string): string => {
    const clean = num.replace(/\s+/g, "");
    if (clean.startsWith("4")) return "Visa";
    if (/^(5[1-5]|2[2-7])/.test(clean)) return "Mastercard";
    if (/^3[47]/.test(clean)) return "American Express";
    return "Tarjeta";
  };

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    setError(null);

    const cleanNum = numeroTarjeta.replace(/\s+/g, "");
    if (cleanNum.length < 15) {
      setError("Ingresa un número de tarjeta válido (mínimo 15 dígitos).");
      return;
    }

    const expParts = expiracion.split("/");
    if (expParts.length !== 2 || Number(expParts[0]) < 1 || Number(expParts[0]) > 12) {
      setError("Ingresa una fecha de expiración válida (MM/AA).");
      return;
    }

    if (cvv.length < 3) {
      setError("El código de seguridad (CVV) debe tener 3 o 4 dígitos.");
      return;
    }

    setProcessing(true);

    // Simulación de procesamiento en pasarela segura (PCI-DSS)
    // Se genera una referencia criptográfica. NUNCA se persiste el PAN ni el CVV en TekniGO (CA-05).
    setTimeout(() => {
      const brand = detectBrand(cleanNum);
      const last4 = cleanNum.slice(-4);
      const randomSuffix = Math.random().toString(36).substring(2, 8).toUpperCase();
      const gatewayRef = `TKGO-GW-${Date.now().toString(36).toUpperCase()}-${randomSuffix}`;

      // Limpieza preventiva de campos sensibles
      setNumeroTarjeta("");
      setCvv("");
      setProcessing(false);

      onSuccess({
        referenciaPasarela: gatewayRef,
        marcaTarjeta: brand,
        ultimosCuatro: last4,
      });
    }, 1200);
  };

  return (
    <div className="gateway-modal-backdrop" role="dialog" aria-modal="true" aria-labelledby="gateway-title">
      <div className="gateway-modal">
        <header className="gateway-header">
          <div className="gateway-header-title">
            <Lock size={18} aria-hidden="true" />
            <h3 id="gateway-title">{titulo}</h3>
          </div>
          <button
            type="button"
            className="gateway-close-button"
            onClick={onCancel}
            disabled={processing}
            aria-label="Cerrar pasarela de pago"
          >
            <X size={18} />
          </button>
        </header>

        <form onSubmit={handleSubmit} className="gateway-body">
          <div className="gateway-amount-box">
            <span>Monto a autorizar</span>
            <strong>L. {monto.toFixed(2)}</strong>
          </div>

          {error && (
            <div className="alert" role="alert" style={{ margin: "8px 0" }}>
              <AlertCircle size={15} />
              <span>{error}</span>
            </div>
          )}

          <label className="field">
            Nombre del titular
            <input
              type="text"
              required
              placeholder="Como aparece en la tarjeta"
              value={nombreTitular}
              onChange={(e) => setNombreTitular(e.target.value)}
              disabled={processing}
            />
          </label>

          <label className="field">
            Número de tarjeta
            <div className="gateway-input-with-icon">
              <input
                type="text"
                required
                placeholder="4000 1234 5678 9010"
                value={numeroTarjeta}
                onChange={handleCardNumberChange}
                disabled={processing}
              />
              <CreditCard size={18} className="gateway-input-icon" />
            </div>
          </label>

          <div className="field-row" style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: "10px" }}>
            <label className="field">
              Vencimiento
              <input
                type="text"
                required
                placeholder="MM/AA"
                maxLength={5}
                value={expiracion}
                onChange={handleExpiryChange}
                disabled={processing}
              />
            </label>

            <label className="field">
              CVV / CVC
              <input
                type="password"
                required
                placeholder="•••"
                maxLength={4}
                value={cvv}
                onChange={(e) => setCvv(e.target.value.replace(/\D/g, ""))}
                disabled={processing}
              />
            </label>
          </div>

          <div className="gateway-security-notice">
            <ShieldCheck size={16} aria-hidden="true" />
            <p>
              Simulación local. Usa únicamente datos ficticios de tarjeta. El simulador no realiza cargos.
            </p>
          </div>

          <footer className="gateway-actions">
            <button
              type="button"
              className="button button-outline"
              onClick={onCancel}
              disabled={processing}
            >
              Cancelar
            </button>
            <button type="submit" className="button" disabled={processing}>
              {processing ? "Simulando autorización…" : `Simular pago de L. ${monto.toFixed(2)}`}
            </button>
          </footer>
        </form>
      </div>
    </div>
  );
};

export default SecurePaymentGatewayModal;
