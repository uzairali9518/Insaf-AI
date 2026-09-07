"""
Insaaf AI - FastAPI Backend

Endpoints:
  POST /api/audit          - upload a CSV, run the full pipeline, get JSON results
  GET  /api/report/{id}     - download the generated PDF report
  GET  /api/health          - health check

Run locally:
  pip install -r requirements.txt
  uvicorn main:app --reload --port 8000

TODO (Qoder build phase):
  - Add PostgreSQL persistence (SQLAlchemy) for audit history per user/org.
  - Add auth (even a simple API key) before this goes past demo stage.
  - Add the /api/audit-from-json endpoint for API-based (non-CSV) audits.
"""

import io
import os
import uuid
import json
import logging
import pandas as pd
from fastapi import FastAPI, UploadFile, File, HTTPException, Form, Body
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse, JSONResponse
from typing import Optional, Dict, Any

from crew import build_insaaf_crew, llm
from pipeline import InsaafPipeline
from agents.reporting_agent import ReportingAgent
from streaming import audit_event_stream

# DEMO-LEVEL IN-MEMORY STORE
# --------------------------
# ``_audit_store`` caches full audit results keyed by report_id so the
# chat endpoint can answer questions grounded in a specific report.
# ``_certificate_store`` caches a minimal, public-safe summary keyed by
# report_id (used as the certificate ID) so /api/certificate/{id} can be
# shared without exposing raw data.
#
# NOTE: This is intentionally in-memory and temporary. For production it
# should be replaced by a persistent database (e.g. PostgreSQL) and the
# public certificate records should be signed or otherwise protected from
# tampering.
_audit_store: Dict[str, Dict[str, Any]] = {}
_certificate_store: Dict[str, Dict[str, Any]] = {}

app = FastAPI(title="Insaaf AI API", version="0.1.0")

def _parse_cors_origins() -> list:
    raw = os.getenv("CORS_ALLOW_ORIGINS", "*")
    if raw.strip() == "*":
        return ["*"]
    return [o.strip() for o in raw.split(",") if o.strip()]

app.add_middleware(
    CORSMiddleware,
    allow_origins=_parse_cors_origins(),
    allow_methods=["*"],
    allow_headers=["*"],
)

REPORTS_DIR = "generated_reports"
os.makedirs(REPORTS_DIR, exist_ok=True)


def _cache_audit_result(report_id: str, result: Dict[str, Any]) -> None:
    """Cache a full audit result for the /ask endpoint."""
    _audit_store[report_id] = result


def _cache_certificate(report_id: str, result: Dict[str, Any]) -> None:
    """Cache a public-safe certificate summary if the audit is certified."""
    report = result.get("report", {})
    if not report.get("certified"):
        return

    bias = result.get("bias_detection", {})
    group_def = bias.get("group_definition", {})

    _certificate_store[report_id] = {
        "protected_attribute": result.get("config_used", {}).get("protected_attribute"),
        "trust_score": report.get("trust_score"),
        "certified": True,
        "generated_at": report.get("generated_at"),
        "group_definition": group_def,
    }


@app.get("/api/health")
def health():
    return {"status": "ok", "service": "Insaaf AI"}


@app.post("/api/audit")
async def audit(
    file: UploadFile = File(...),
    protected_attribute: Optional[str] = Form(None),
    privileged_value: Optional[str] = Form(None),
    positive_outcome_value: Optional[str] = Form(None),
    ground_truth_column: Optional[str] = Form(None),
):
    if not file.filename.endswith(".csv"):
        raise HTTPException(status_code=400, detail="Please upload a CSV file.")

    contents = await file.read()
    try:
        df = pd.read_csv(io.BytesIO(contents))
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Could not parse CSV: {exc}")

    crew, state = build_insaaf_crew(
        df=df,
        protected_attribute=protected_attribute,
        privileged_value=privileged_value,
        positive_outcome_value=positive_outcome_value,
        ground_truth_column=ground_truth_column,
    )
    try:
        await crew.kickoff_async()
        result = state["results"]
    except Exception as crew_err:
        # Fallback: no LLM available or crew failed — run the original
        # sequential pipeline so the API always returns a valid response.
        import logging
        logging.warning("CrewAI crew failed (%s), falling back to sequential pipeline.", crew_err)
        pipeline = InsaafPipeline(
            df=df,
            protected_attribute=protected_attribute,
            privileged_value=privileged_value,
            positive_outcome_value=positive_outcome_value,
            ground_truth_column=ground_truth_column,
        )
        result = pipeline.run()

    if "stage_failed" in result:
        raise HTTPException(status_code=422, detail=result)

    # Propagate nested stage failures from individual pipeline stages
    # (e.g. bias_detection returning stage_failed for empty groups).
    for stage_key, stage_val in result.items():
        if isinstance(stage_val, dict) and "stage_failed" in stage_val:
            raise HTTPException(status_code=422, detail=stage_val)

    if result.get("intake", {}).get("valid") is False:
        raise HTTPException(
            status_code=422,
            detail={"stage_failed": "intake", "error": result["intake"].get("message", "Intake failed.")},
        )

    # Generate the PDF and store it for download
    report_id = str(uuid.uuid4())
    reporting_agent = ReportingAgent(
        result["intake"], result["bias_detection"], result["explainability"]
    )
    pdf_path = os.path.join(REPORTS_DIR, f"{report_id}.pdf")
    reporting_agent.generate_pdf(result["report"], pdf_path)

    result["report_id"] = report_id

    # Cache for /ask and /certificate endpoints (demo-level in-memory store)
    _cache_audit_result(report_id, result)
    _cache_certificate(report_id, result)

    return result


@app.post("/api/audit-stream")
async def audit_stream(
    file: UploadFile = File(...),
    protected_attribute: Optional[str] = Form(None),
    privileged_value: Optional[str] = Form(None),
    positive_outcome_value: Optional[str] = Form(None),
    ground_truth_column: Optional[str] = Form(None),
):
    if not file.filename.endswith(".csv"):
        raise HTTPException(status_code=400, detail="Please upload a CSV file.")

    contents = await file.read()

    async def _wrapped_stream():
        """Wrap the event stream so we can cache the final result."""
        final_result = None
        async for chunk in audit_event_stream(
            contents,
            protected_attribute,
            privileged_value,
            positive_outcome_value,
            ground_truth_column,
            REPORTS_DIR,
        ):
            yield chunk
            # The final event carries the complete result payload.
            if chunk.startswith("data:"):
                try:
                    payload = json.loads(chunk[len("data:"):].strip())
                    if payload.get("stage") == "complete":
                        final_result = payload.get("result")
                except Exception:
                    pass

        if final_result and final_result.get("report_id"):
            _cache_audit_result(final_result["report_id"], final_result)
            _cache_certificate(final_result["report_id"], final_result)

    return StreamingResponse(
        _wrapped_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@app.get("/api/report/{report_id}")
def get_report(report_id: str):
    path = os.path.join(REPORTS_DIR, f"{report_id}.pdf")
    if not os.path.exists(path):
        raise HTTPException(status_code=404, detail="Report not found.")
    return FileResponse(path, media_type="application/pdf", filename="Insaaf_AI_Trust_Report.pdf")


@app.post("/api/audit/{report_id}/ask")
async def ask_about_report(report_id: str, body: dict = Body(...)):
    """
    Answer a natural-language question about a specific audit result.

    The answer is grounded in the cached report metrics (DIR, EOD, SHAP
    features, trust score, mitigation projection). It uses the same LLM
    configured for CrewAI. If no LLM is available, returns a clear message
    instead of crashing.
    """
    question = body.get("question", "").strip()
    if not question:
        raise HTTPException(status_code=400, detail="Question is required.")

    audit = _audit_store.get(report_id)
    if not audit:
        raise HTTPException(status_code=404, detail="Report not found.")

    # Build a compact, grounded context from the cached audit.
    bias = audit.get("bias_detection", {})
    dir_data = bias.get("disparate_impact", {})
    eod_data = bias.get("equal_opportunity")
    explain = audit.get("explainability", {})
    report = audit.get("report", {})
    mitigation = audit.get("mitigation", {})
    config_used = audit.get("config_used", {})

    context_lines = [
        f"Protected attribute: {config_used.get('protected_attribute')}",
        f"Outcome column: {config_used.get('outcome_column')}",
        f"Privileged value(s): {config_used.get('privileged_value')}",
        f"Positive outcome value: {config_used.get('positive_outcome_value')}",
        f"Trust Score: {report.get('trust_score')}/100",
        f"Certified: {report.get('certified', False)}",
    ]

    if dir_data:
        context_lines.append(
            f"Disparate Impact Ratio: {dir_data.get('score')} "
            f"(privileged positive rate {dir_data.get('privileged_positive_rate')}, "
            f"unprivileged positive rate {dir_data.get('unprivileged_positive_rate')}, "
            f"passes 80% rule: {dir_data.get('passes_80_percent_rule')})"
        )

    if eod_data:
        context_lines.append(
            f"Equal Opportunity Difference: {eod_data.get('score')} "
            f"(within acceptable range: {eod_data.get('within_acceptable_range')})"
        )

    top_features = explain.get("top_features", [])
    if top_features:
        context_lines.append(
            "Top SHAP features: " + ", ".join(
                f"{f['feature']} ({f['importance']:.4f})" for f in top_features[:5]
            )
        )
        context_lines.append(
            f"Protected attribute in top features: {explain.get('protected_attribute_in_top_features', False)}"
        )

    group_def = bias.get("group_definition", {})
    if group_def:
        context_lines.append(
            f"Group comparison: privileged={group_def.get('privileged_values')}, "
            f"unprivileged={group_def.get('unprivileged_values')}"
        )

    if mitigation and mitigation.get("mitigation_applied"):
        context_lines.append(
            f"Mitigation: {mitigation.get('mitigation_applied')} — "
            f"original DIR {mitigation.get('original_dir')}, "
            f"projected DIR {mitigation.get('projected_dir')}, "
            f"original Trust Score {mitigation.get('original_trust_score')}, "
            f"projected Trust Score {mitigation.get('projected_trust_score')}"
        )

    context = "\n".join(context_lines)

    prompt = (
        "You are Insaaf AI, a fairness audit assistant answering questions about a "
        "specific audit report. Follow these rules:\n\n"
        "1. If the user asks for the DEFINITION or MEANING of a fairness concept, "
        "metric, term, or rule mentioned in the report (e.g. 'What is Equal Opportunity "
        "Difference?', 'What does SHAP mean?', 'Why does the 80 percent rule matter?'), "
        "give a clear, accurate general explanation of the concept. You may also note "
        "whether that metric was actually computed for this audit and, if not, why not.\n\n"
        "2. If the user asks for a SPECIFIC VALUE, FACT, or CLAIM about this particular "
        "audit (e.g. 'What's the Disparate Impact Ratio?', 'Which group was privileged?', "
        "'Does Multan have lower approval than Lahore?'), answer ONLY from the audit "
        "metrics provided below. If the specific fact is not in the metrics, say exactly: "
        "\"The data doesn't show that.\" Do not guess or make up numbers.\n\n"
        "3. Keep your answer concise and directly relevant to the question.\n\n"
        f"--- AUDIT METRICS ---\n{context}\n\n"
        f"--- USER QUESTION ---\n{question}\n\n"
        "--- ANSWER ---\n"
    )

    try:
        answer = llm.call(prompt)
    except Exception as exc:
        logging.warning("LLM call failed for /ask: %s", exc)
        return JSONResponse(
            status_code=503,
            content={
                "answer": "Ask feature requires an LLM API key - see README. "
                          "Set GROQ_API_KEY or run Ollama locally."
            },
        )

    return {"answer": answer}


@app.get("/api/certificate/{certificate_id}")
def get_certificate(certificate_id: str):
    """
    Public certificate verification endpoint.

    Returns a minimal, privacy-safe summary for certified audits. No auth
    required — the URL itself is the credential. Returns 404 if the ID is
    unknown or the audit was not certified.

    NOTE: This is demo-level storage. Production should use a signed,
    tamper-evident record in a persistent database.
    """
    cert = _certificate_store.get(certificate_id)
    if not cert:
        raise HTTPException(status_code=404, detail="Certificate not found.")
    return cert
