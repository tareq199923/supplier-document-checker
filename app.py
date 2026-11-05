"""F010: the Streamlit page for the supplier quotation check.

PRESENTATION ONLY. All workflow logic lives in src/ui_helpers.py, which reuses the
existing single-entry workflow and the F009 report writer. Run with:  streamlit run app.py

Rules this page follows:
  * The check starts ONLY from the button. Choosing or changing a file never starts one.
  * The outcome is kept in st.session_state, so clicking Download (which makes Streamlit
    rerun the script) shows the stored outcome again and never runs the workflow again.
  * If either upload changes, the stored outcome is dropped, so a result is never shown
    next to different files.
  * Text that comes from the supplier document is shown with st.text only, never as
    Markdown or HTML. Headings, banners and labels are fixed strings.
"""
import streamlit as st

from src import ui_helpers as helpers
from src.report import MODEL_CALLS_LABEL, NOT_STATED_PREFIX

KEY_OUTCOME = "outcome"
KEY_FINGERPRINT = "fingerprint"
KEY_RUNNING = "running"

TOO_LARGE_MESSAGES = (helpers.MSG_PDF_TOO_LARGE, helpers.MSG_CSV_TOO_LARGE)


def start_check() -> None:
    """Button callback: only raises a flag. The work is done in the script run that follows."""
    st.session_state[KEY_RUNNING] = True


def show_banner(banner: helpers.Banner) -> None:
    if banner.kind == helpers.KIND_SUCCESS:
        st.success(banner.text)
    elif banner.kind == helpers.KIND_ERROR:
        st.error(banner.text)
    elif banner.kind == helpers.KIND_WARNING:
        st.warning(banner.text)
    else:
        st.text(banner.text)          # neutral: plain text, never interpreted


def reason_block(reason: helpers.ReasonView) -> str:
    lines = [f"{reason.number}. [{reason.code}] {reason.severity} - {reason.message}", "   Evidence:"]
    if reason.evidence:
        lines += [f'     {label}: "{quote}"' for label, quote in reason.evidence]
    else:
        lines.append(f"     {reason.no_quote_text}")
    if reason.not_stated:
        lines.append("   " + NOT_STATED_PREFIX + ", ".join(reason.not_stated))
    return "\n".join(lines)


def show_view(view: helpers.ResultView) -> None:
    show_banner(view.banner)
    st.text(f"Supplier: {view.supplier}")
    if view.purchase_request:
        st.text(f"Purchase request: {view.purchase_request}")

    if view.error is not None:
        st.subheader("What happened")
        st.text(
            "\n".join(
                [
                    f"Error code: {view.error.code}",
                    f"Message: {view.error.message}",
                    f"Stage: {view.error.stage}",
                    f"Retry: {view.error.retry}",
                ]
            )
        )
    else:
        st.subheader("Why")
        if view.reasons_note:
            st.text(view.reasons_note)
        for reason in view.reasons:
            st.text(reason_block(reason))

    if view.match is not None:
        st.subheader("Purchase request match")
        st.text(view.match.summary)
        if view.match.row_lines:
            st.text("\n".join(f"{label}: {value}" for label, value in view.match.row_lines))
        for title, items in (("Problems", view.match.problems), ("Notes", view.match.notes)):
            if items:
                st.text("\n".join([f"{title}:"] + [f"  - {item}" for item in items]))

    if view.model_calls is not None:
        st.text(MODEL_CALLS_LABEL + view.model_calls)


def show_outcome(outcome: helpers.Outcome) -> None:
    if outcome.failure:
        st.error(outcome.failure)
        return
    show_view(helpers.build_view(outcome.result))
    if outcome.report_bytes is not None:
        st.download_button(
            "Download decision report (.txt)",
            data=outcome.report_bytes,
            file_name=outcome.report_name,
            mime="text/plain",
            key="download_report",
        )
    elif outcome.report_failure:
        st.warning(outcome.report_failure)


st.set_page_config(page_title="Supplier quotation check", layout="centered")
st.title("Supplier quotation check")
st.caption("Upload one supplier quotation and its purchase request, then press the button.")
st.caption(helpers.LIMITS_NOTE)

pdf_file = st.file_uploader("Supplier quotation (PDF)", type=["pdf"], key="pdf_upload")
csv_file = st.file_uploader("Purchase request (CSV)", type=["csv"], key="csv_upload")

pdf_bytes = pdf_file.getvalue() if pdf_file is not None else None
csv_bytes = csv_file.getvalue() if csv_file is not None else None
pdf_name = pdf_file.name if pdf_file is not None else None
csv_name = csv_file.name if csv_file is not None else None

# A changed upload makes the old outcome stale: drop it.
current = helpers.fingerprint(pdf_name, pdf_bytes, csv_name, csv_bytes)
if st.session_state.get(KEY_FINGERPRINT) != current:
    st.session_state[KEY_FINGERPRINT] = current
    st.session_state.pop(KEY_OUTCOME, None)

problems = helpers.upload_problems(pdf_bytes, csv_bytes)
for problem in problems:
    if problem in TOO_LARGE_MESSAGES:
        st.error(problem)
    else:
        st.info(problem)

button_slot = st.empty()


def draw_button(key: str, disabled: bool) -> None:
    with button_slot.container():
        st.button("Check quotation", type="primary", key=key, on_click=start_check, disabled=disabled)


if st.session_state.get(KEY_RUNNING):
    # A disabled copy while the check runs, so a second click cannot start another one.
    draw_button("check_button_busy", disabled=True)
    try:
        if not problems:
            with st.spinner("Checking the quotation. This can take a little while."):
                st.session_state[KEY_OUTCOME] = helpers.run_submission(pdf_name, pdf_bytes, csv_bytes)
    finally:
        st.session_state[KEY_RUNNING] = False
# The normal button replaces the busy one in the same slot once the check is done.
draw_button("check_button", disabled=bool(problems))

stored = st.session_state.get(KEY_OUTCOME)
if stored is not None:
    show_outcome(stored)
