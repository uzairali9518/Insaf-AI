import { useState, useEffect } from "react";
import { AlertCircle, ArrowLeft } from "lucide-react";
import { Card, CardContent } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import Seal from "./Seal";

const RAW_URL = import.meta.env.VITE_API_BASE_URL || "http://localhost:8000";
const API_BASE_URL = RAW_URL.replace(/\/+$/, "");

export default function CertificatePage() {
  const [cert, setCert] = useState(null);
  const [error, setError] = useState(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    const match = window.location.pathname.match(/^\/certificate\/([^/]+)\/?$/);
    const certificateId = match ? match[1] : null;

    if (!certificateId) {
      setError("Invalid certificate link.");
      setLoading(false);
      return;
    }

    fetch(`${API_BASE_URL}/api/certificate/${certificateId}`)
      .then(async (res) => {
        if (!res.ok) throw new Error("Certificate not found.");
        return res.json();
      })
      .then((data) => {
        setCert(data);
        setLoading(false);
      })
      .catch((err) => {
        setError(err.message);
        setLoading(false);
      });
  }, []);

  if (loading) {
    return (
      <div className="min-h-screen flex items-center justify-center bg-background p-6">
        <div className="flex flex-col items-center gap-4 text-muted-foreground">
          <div className="w-6 h-6 border-2 border-primary/30 border-t-primary rounded-full animate-spin" />
          <p>Verifying certificate…</p>
        </div>
      </div>
    );
  }

  if (error) {
    return (
      <div className="min-h-screen flex items-center justify-center bg-background p-6">
        <Card className="max-w-md w-full text-center">
          <CardContent className="pt-10 pb-10 flex flex-col items-center gap-4">
            <AlertCircle className="w-12 h-12 text-destructive" />
            <h1 className="font-display text-2xl font-semibold text-foreground">
              Certificate not found
            </h1>
            <p className="text-muted-foreground">{error}</p>
            <Button asChild className="mt-2">
              <a href="/">Back to Insaaf AI</a>
            </Button>
          </CardContent>
        </Card>
      </div>
    );
  }

  const generatedDate = cert.generated_at
    ? new Date(cert.generated_at).toLocaleDateString(undefined, {
        year: "numeric",
        month: "long",
        day: "numeric",
      })
    : null;

  const privValues = cert.group_definition?.privileged_values?.join(", ") || "—";
  const unprivValues = cert.group_definition?.unprivileged_values?.join(", ") || "—";

  return (
    <div className="min-h-screen flex items-center justify-center bg-background p-6">
      <Card className="relative max-w-2xl w-full overflow-hidden rounded-2xl shadow-lg border-border">
        <div
          className="absolute inset-0 rounded-2xl p-px pointer-events-none"
          style={{
            background:
              "linear-gradient(135deg, rgba(46,107,79,0.25), transparent 40%, transparent 60%, rgba(46,107,79,0.12))",
            WebkitMask:
              "linear-gradient(#fff 0 0) content-box, linear-gradient(#fff 0 0)",
            WebkitMaskComposite: "xor",
            maskComposite: "exclude",
          }}
        />
        <CardContent className="relative p-10 md:p-14">
          <header className="flex items-center justify-between pb-5 mb-8 border-b border-border">
            <img
              src="/logo/lockup_horizontal.png"
              alt="Insaaf AI"
              className="h-8 w-auto mix-blend-multiply"
            />
            <span className="font-mono text-2xs uppercase tracking-wider text-primary bg-primary/10 px-3 py-1.5 rounded-full">
              Public Verification
            </span>
          </header>

          <div className="flex justify-center mb-6">
            <Seal passed={true} size={120} />
          </div>

          <h1 className="font-display text-3xl md:text-4xl text-center text-success mb-2 tracking-tight">
            INSAAF CERTIFIED
          </h1>
          <p className="text-center text-muted-foreground max-w-md mx-auto mb-8 leading-relaxed">
            This AI system has been audited for fairness and meets the Insaaf AI
            certification threshold.
          </p>

          <div className="flex justify-center mb-8">
            <div className="w-40 h-40 rounded-full border-4 border-success bg-secondary flex flex-col items-center justify-center shadow-inner">
              <span className="font-display text-6xl font-bold text-success leading-none">
                {cert.trust_score}
              </span>
              <span className="font-mono text-xs uppercase tracking-wider text-muted-foreground mt-2">
                Trust Score / 100
              </span>
            </div>
          </div>

          <div className="grid grid-cols-1 sm:grid-cols-2 gap-4 mb-8">
            <Detail label="Protected attribute" value={cert.protected_attribute} />
            <Detail label="Privileged group(s)" value={privValues} />
            <Detail label="Unprivileged group(s)" value={unprivValues} />
            {generatedDate && <Detail label="Certified on" value={generatedDate} />}
          </div>

          <footer className="text-center pt-6 border-t border-border">
            <p className="text-muted-foreground mb-4">
              Verified by <strong className="text-foreground">Insaaf AI</strong> — Auditing AI
              for a Fairer Pakistan.
            </p>
            <Button variant="outline" size="sm" asChild>
              <a href="/" className="inline-flex items-center gap-2">
                <ArrowLeft className="w-4 h-4" />
                Audit your own system
              </a>
            </Button>
          </footer>
        </CardContent>
      </Card>
    </div>
  );
}

function Detail({ label, value }) {
  return (
    <div className="bg-secondary border border-border rounded-lg p-4 flex flex-col gap-1">
      <span className="font-mono text-2xs uppercase tracking-wider text-muted-foreground">
        {label}
      </span>
      <span className="font-semibold text-foreground break-words">{value}</span>
    </div>
  );
}
