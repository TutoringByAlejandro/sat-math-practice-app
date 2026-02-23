import json
import random
import re
import time
from datetime import datetime
from decimal import Decimal, InvalidOperation
from fractions import Fraction
from io import BytesIO

import altair as alt
import streamlit as st


# ----------------------------
# Optional: safe timer refresh
# ----------------------------
try:
    from streamlit_autorefresh import st_autorefresh  # type: ignore
    HAS_AUTOREFRESH = True
except Exception:
    HAS_AUTOREFRESH = False


# ----------------------------
# Config
# ----------------------------
APP_TITLE = "SAT Math Practice"
QUESTION_FILE = "question_bank.json"


# ----------------------------
# Helpers: loading questions
# ----------------------------
@st.cache_data(show_spinner=False)
def load_questions(path: str):
    with open(path, "r", encoding="utf-8") as f:
        raw = f.read().strip()
        if not raw:
            raise ValueError("question_bank.json is empty.")
        data = json.loads(raw)
        if not isinstance(data, list):
            raise ValueError("question_bank.json must be a JSON list (array) of question objects.")
        return data


def normalize_text(s: str) -> str:
    return str(s).strip()


def is_mixed_number(s: str) -> bool:
    return re.match(r"^\s*\d+\s+\d+\s*/\s*\d+\s*$", s) is not None


def parse_decimal_to_fraction(s: str) -> Fraction:
    d = Decimal(s)
    return Fraction(d)


def parse_fraction_string(s: str) -> Fraction:
    m = re.match(r"^\s*([+-]?\d+)\s*/\s*([+-]?\d+)\s*$", s)
    if not m:
        raise ValueError("Not a fraction")
    num = int(m.group(1))
    den = int(m.group(2))
    if den == 0:
        raise ValueError("Denominator cannot be 0")
    return Fraction(num, den)


def parse_sat_numeric(user_str: str) -> Fraction | None:
    s = normalize_text(user_str)
    if s == "":
        return None

    if any(ch in s for ch in [",", "$", "%"]):
        return None

    if is_mixed_number(s):
        return None

    if re.match(r"^\s*\d+\s*-\s*\d+/\d+\s*$", s):
        return None

    try:
        return parse_fraction_string(s)
    except Exception:
        pass

    try:
        return parse_decimal_to_fraction(s)
    except (InvalidOperation, ValueError):
        return None


def is_correct(q: dict, user_response) -> bool:
    qtype = str(q.get("type", "mcq")).lower()
    correct_answer = str(q.get("answer", "")).strip()

    if qtype == "mcq":
        if user_response is None:
            return False
        return str(user_response).strip() == correct_answer

    if qtype == "numeric":
        if user_response is None:
            return False
        user_str = str(user_response).strip()
        user_val = parse_sat_numeric(user_str)
        if user_val is None:
            return False

        ans_val = parse_sat_numeric(correct_answer)
        if ans_val is None:
            return user_str == correct_answer

        return user_val == ans_val

    return False


def get_user_response_widget(q: dict, idx: int):
    qtype = str(q.get("type", "mcq")).lower()

    if qtype == "mcq":
        choices = q.get("choices", None)
        if not isinstance(choices, list) or len(choices) == 0:
            st.error("MCQ question is missing a non-empty 'choices' list.")
            return None
        return st.radio("Choose one:", choices, key=f"mcq_{idx}")

    if qtype == "numeric":
        return st.text_input("Enter your answer (decimal or fraction like 3/4):", key=f"num_{idx}")

    st.error(f"Unsupported question type: {qtype}")
    return None


# ----------------------------
# PDF helpers (Option B + charts)
# ----------------------------
def _try_make_altair_png(chart) -> bytes | None:
    """
    Convert an Altair chart to PNG bytes using vl-convert-python.
    Returns None if conversion deps aren't installed.
    """
    try:
        import vl_convert as vlc  # type: ignore
        spec = chart.to_dict()
        png = vlc.vegalite_to_png(spec, scale=2)
        return png
    except Exception:
        return None


def _build_charts_from_history(history_rows: list[dict]):
    # Use Altair with inline data; no need for pandas to render in-app,
    # but for PDF image export, Altair spec is enough.
    base_data = alt.Data(values=history_rows)

    domain_chart = (
        alt.Chart(base_data)
        .mark_bar()
        .encode(
            x=alt.X("domain:N", title="Domain", sort="-y"),
            y=alt.Y("mean(correct):Q", title="Accuracy", scale=alt.Scale(domain=[0, 1])),
            tooltip=["domain:N", alt.Tooltip("mean(correct):Q", title="Accuracy")]
        )
        .properties(width=520, height=220, title="Accuracy by Domain")
    )

    subtopic_chart = (
        alt.Chart(base_data)
        .mark_bar()
        .encode(
            x=alt.X("subtopic:N", title="Subtopic", sort="-y"),
            y=alt.Y("mean(correct):Q", title="Accuracy", scale=alt.Scale(domain=[0, 1])),
            tooltip=["subtopic:N", alt.Tooltip("mean(correct):Q", title="Accuracy")]
        )
        .properties(width=520, height=260, title="Accuracy by Subtopic")
    )

    return domain_chart, subtopic_chart


def build_results_pdf_option_b(summary: dict) -> tuple[bytes | None, str | None]:
    """
    Option B PDF: summary + question history + embedded charts as images.
    Returns (pdf_bytes, error_message).
    """
    try:
        from reportlab.lib.pagesizes import letter
        from reportlab.lib.utils import ImageReader
        from reportlab.pdfgen import canvas
    except Exception as e:
        return None, f"reportlab not installed: {e}"

    buffer = BytesIO()
    c = canvas.Canvas(buffer, pagesize=letter)
    width, height = letter

    x = 50
    y = height - 50

    def line(txt, dy=16, font="Helvetica", size=11):
        nonlocal y
        c.setFont(font, size)
        c.drawString(x, y, txt)
        y -= dy
        if y < 60:
            c.showPage()
            y = height - 50

    def spacer(dy=12):
        nonlocal y
        y -= dy
        if y < 60:
            c.showPage()
            y = height - 50

    c.setTitle("SAT Math Practice Results")

    # Header
    line("SAT Math Practice Results", dy=24, font="Helvetica-Bold", size=18)
    line(f"Date: {summary['date']}")
    line(f"Mode: {summary['mode']}")
    line(f"Score: {summary['score']} / {summary['total']}")
    line(f"Domains: {', '.join(summary['domains']) if summary['domains'] else 'All'}")
    line(f"Subtopics: {', '.join(summary['subtopics']) if summary['subtopics'] else 'All'}")
    line(f"Difficulty range: {summary['difficulty_min']} - {summary['difficulty_max']}")
    if summary.get("seconds_per_question") is not None:
        line(f"Seconds per question: {summary['seconds_per_question']}")

    spacer(10)

    # Build history rows for charts
    history = summary.get("history", [])
    history_rows = []
    for h in history:
        history_rows.append({
            "domain": h.get("domain", "Unknown"),
            "subtopic": h.get("subtopic", "Unknown"),
            "difficulty": int(h.get("difficulty", 1)),
            "correct": 1 if h.get("correct", False) else 0,
        })

    # Charts (if conversion works)
    domain_chart_png = None
    subtopic_chart_png = None
    if history_rows:
        domain_chart, subtopic_chart = _build_charts_from_history(history_rows)
        domain_chart_png = _try_make_altair_png(domain_chart)
        subtopic_chart_png = _try_make_altair_png(subtopic_chart)

    if domain_chart_png and subtopic_chart_png:
        # Embed charts
        line("Performance Charts", dy=18, font="Helvetica-Bold", size=14)
        spacer(6)

        # Domain chart
        img1 = ImageReader(BytesIO(domain_chart_png))
        # drawImage(x, y, width, height) uses bottom-left origin, so compute placement carefully
        chart_w = 520
        chart_h = 220
        y -= chart_h
        c.drawImage(img1, x, y, width=chart_w, height=chart_h, preserveAspectRatio=True, mask="auto")
        spacer(20)

        # Subtopic chart
        img2 = ImageReader(BytesIO(subtopic_chart_png))
        chart_w2 = 520
        chart_h2 = 260
        y -= chart_h2
        if y < 60:
            c.showPage()
            y = height - 50
            y -= chart_h2
        c.drawImage(img2, x, y, width=chart_w2, height=chart_h2, preserveAspectRatio=True, mask="auto")
        spacer(30)
    else:
        # If charts couldn't be generated, tell them why
        line("Performance Charts", dy=18, font="Helvetica-Bold", size=14)
        line("Charts could not be embedded (missing chart-to-PNG converter).")
        line("Install: pip install vl-convert-python")
        spacer(10)

    # Question history
    line("Question History", dy=18, font="Helvetica-Bold", size=14)

    for i, h in enumerate(history, start=1):
        status = "Correct" if h.get("correct") else "Incorrect"
        dom = h.get("domain", "")
        sub = h.get("subtopic", "")
        diff = h.get("difficulty", "")
        qid = h.get("id", "")
        difficulty_labels = {
            1: "Very Easy",
            2: "Easy",
            3: "Medium",
            4: "Hard",
            5: "Very Hard"
        }
        label = difficulty_labels.get(int(diff), str(diff))
        line(f"{i}. [{status}] {dom} | {sub} | Difficulty: {label} ({diff}/5) ", dy=14)
        if qid:
            line(f"    ID: {qid}", dy=14)

    c.save()
    pdf_bytes = buffer.getvalue()
    buffer.close()
    return pdf_bytes, None


# ----------------------------
# Session state init
# ----------------------------
def init_state():
    defaults = {
        "in_quiz": False,
        "quiz_started": False,
        "quiz_questions": [],
        "current_index": 0,
        "score": 0,
        "history": [],
        "question_start_time": None,
        "submitted_for_index": None,
        "last_result": None,
        "last_explanation": None,
        "last_correct_answer": None,
        "last_user_answer": None,
        "selected_domains": None,
        "selected_subtopics": None,
    }
    for k, v in defaults.items():
        if k not in st.session_state:
            st.session_state[k] = v


def reset_quiz_state(keep_filters=True):
    st.session_state.in_quiz = False
    st.session_state.quiz_started = False
    st.session_state.quiz_questions = []
    st.session_state.current_index = 0
    st.session_state.score = 0
    st.session_state.history = []
    st.session_state.question_start_time = None
    st.session_state.submitted_for_index = None
    st.session_state.last_result = None
    st.session_state.last_explanation = None
    st.session_state.last_correct_answer = None
    st.session_state.last_user_answer = None
    if not keep_filters:
        st.session_state.selected_domains = None
        st.session_state.selected_subtopics = None


# ----------------------------
# UI
# ----------------------------
st.set_page_config(page_title=APP_TITLE, layout="wide")
init_state()

st.title(APP_TITLE)

# Load questions
try:
    questions_all = load_questions(QUESTION_FILE)
except Exception as e:
    st.error(f"Failed to load {QUESTION_FILE}: {e}")
    st.stop()

all_domains = sorted({str(q.get("domain", "Unknown")) for q in questions_all})
all_subtopics = sorted({str(q.get("subtopic", "Unknown")) for q in questions_all})

with st.sidebar:
    st.header("Settings")

    mode = st.radio("Mode", ["Practice", "Timed Quiz"], index=0)

    # Default ALL selected
    if st.session_state.selected_domains is None:
        st.session_state.selected_domains = all_domains.copy()
    if st.session_state.selected_subtopics is None:
        st.session_state.selected_subtopics = all_subtopics.copy()

    selected_domains = st.multiselect(
        "Select Domain(s)",
        options=all_domains,
        default=st.session_state.selected_domains,
    )
    st.session_state.selected_domains = selected_domains

    selected_subtopics = st.multiselect(
        "Select Subtopic(s)",
        options=all_subtopics,
        default=st.session_state.selected_subtopics,
    )
    st.session_state.selected_subtopics = selected_subtopics

    diff_min, diff_max = st.slider("Difficulty range (1–5)", 1, 5, (1, 5))
    num_questions = st.number_input("Number of Questions", min_value=1, max_value=100, value=10, step=1)

    seconds_per_question = None
    if mode == "Timed Quiz":
        seconds_per_question = st.number_input("Seconds per question", min_value=10, max_value=300, value=60, step=5)

    # Filter questions
    filtered_questions = []
    for q in questions_all:
        d = str(q.get("domain", "Unknown"))
        s = str(q.get("subtopic", "Unknown"))
        diff = int(q.get("difficulty", 1))

        domain_ok = (len(selected_domains) == 0) or (d in selected_domains)
        sub_ok = (len(selected_subtopics) == 0) or (s in selected_subtopics)
        diff_ok = (diff_min <= diff <= diff_max)

        if domain_ok and sub_ok and diff_ok:
            filtered_questions.append(q)

    st.caption(f"Questions available with filters: {len(filtered_questions)}")

    colA, colB = st.columns(2)
    with colA:
        if st.button("Start Quiz", use_container_width=True):
            if len(filtered_questions) == 0:
                st.warning("No questions match your filters.")
            else:
                k = min(int(num_questions), len(filtered_questions))
                st.session_state.quiz_questions = random.sample(filtered_questions, k=k)

                st.session_state.in_quiz = True
                st.session_state.quiz_started = True
                st.session_state.current_index = 0
                st.session_state.score = 0
                st.session_state.history = []
                st.session_state.submitted_for_index = None
                st.session_state.last_result = None
                st.session_state.last_explanation = None
                st.session_state.last_correct_answer = None
                st.session_state.last_user_answer = None
                st.session_state.question_start_time = time.time() if mode == "Timed Quiz" else None

                st.rerun()

    with colB:
        if st.button("Reset / Back to Settings", use_container_width=True):
            reset_quiz_state(keep_filters=True)
            st.rerun()


if not st.session_state.in_quiz:
    st.subheader("How it works")
    st.write(
        "- Filters apply first (domain/subtopic/difficulty).\n"
        "- Then we randomly choose your requested number of questions.\n"
        "- Practice shows explanations after submit.\n"
        "- Timed Quiz auto-submits when time runs out (no explanations)."
    )
    st.stop()

quiz = st.session_state.quiz_questions
idx = st.session_state.current_index

# Finished
if idx >= len(quiz):
    st.success(f"Complete! Final Score: {st.session_state.score} / {len(quiz)}")

    st.subheader("Performance Dashboard")

    if len(st.session_state.history) > 0:
        rows = []
        for h in st.session_state.history:
            rows.append({
                "domain": h.get("domain", "Unknown"),
                "subtopic": h.get("subtopic", "Unknown"),
                "difficulty": int(h.get("difficulty", 1)),
                "correct": 1 if h.get("correct", False) else 0
            })

        domain_chart = (
            alt.Chart(alt.Data(values=rows))
            .mark_bar()
            .encode(
                x=alt.X("domain:N", title="Domain", sort="-y"),
                y=alt.Y("mean(correct):Q", title="Accuracy", scale=alt.Scale(domain=[0, 1])),
            )
        )
        st.write("Accuracy by Domain")
        st.altair_chart(domain_chart, use_container_width=True)

        subtopic_chart = (
            alt.Chart(alt.Data(values=rows))
            .mark_bar()
            .encode(
                x=alt.X("subtopic:N", title="Subtopic", sort="-y"),
                y=alt.Y("mean(correct):Q", title="Accuracy", scale=alt.Scale(domain=[0, 1])),
            )
        )
        st.write("Accuracy by Subtopic")
        st.altair_chart(subtopic_chart, use_container_width=True)

    st.subheader("Export")

    export_summary = {
        "date": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "mode": mode,
        "score": st.session_state.score,
        "total": len(quiz),
        "domains": st.session_state.selected_domains or [],
        "subtopics": st.session_state.selected_subtopics or [],
        "difficulty_min": diff_min,
        "difficulty_max": diff_max,
        "seconds_per_question": seconds_per_question,
        "history": st.session_state.history,
    }

    pdf_bytes, pdf_error = build_results_pdf_option_b(export_summary)

    if pdf_bytes:
        st.download_button(
            "Download Results PDF (with charts)",
            data=pdf_bytes,
            file_name="sat_math_results.pdf",
            mime="application/pdf",
        )
        st.caption("If charts are missing, install: pip install vl-convert-python")
    else:
        st.warning(
            "PDF export needs: pip install reportlab vl-convert-python\n"
            "Then restart Streamlit."
        )
        if pdf_error:
            st.caption(f"PDF error: {pdf_error}")

    st.stop()


# Current question
q = quiz[idx]

q_domain = str(q.get("domain", "Unknown"))
q_subtopic = str(q.get("subtopic", "Unknown"))
q_diff = int(q.get("difficulty", 1))
q_prompt = str(q.get("prompt", ""))
q_explanation = q.get("explanation", None)
q_image = q.get("image", None)
q_type = str(q.get("type", "mcq")).lower()

st.markdown(f"### Question {idx + 1} of {len(quiz)}")
st.caption(f"Domain: {q_domain} | Subtopic: {q_subtopic} | Difficulty: {q_diff} | Type: {q_type}")
st.subheader(q_prompt)

if isinstance(q_image, str) and q_image.strip():
    try:
        st.image(q_image, use_container_width=True)
    except Exception:
        st.warning(f"Couldn't load image: {q_image}")

user_response = get_user_response_widget(q, idx)


# Practice mode
if mode == "Practice":
    submit_clicked = st.button("Submit Answer", key=f"submit_{idx}")

    if submit_clicked and st.session_state.submitted_for_index != idx:
        correct = is_correct(q, user_response)
        if correct:
            st.session_state.score += 1

        st.session_state.history.append({
            "id": q.get("id", ""),
            "domain": q_domain,
            "subtopic": q_subtopic,
            "difficulty": q_diff,
            "correct": correct,
        })

        st.session_state.submitted_for_index = idx
        st.session_state.last_result = correct
        st.session_state.last_correct_answer = str(q.get("answer", "")).strip()
        st.session_state.last_user_answer = "" if user_response is None else str(user_response).strip()
        st.session_state.last_explanation = str(q_explanation) if q_explanation is not None else None
        st.rerun()

    if st.session_state.submitted_for_index == idx and st.session_state.last_result is not None:
        if st.session_state.last_result:
            st.success("Correct! 🎉")
        else:
            st.error(f"Incorrect. Correct answer: {st.session_state.last_correct_answer}")

        if st.session_state.last_explanation:
            st.info(f"Explanation: {st.session_state.last_explanation}")

        if st.button("Next Question", key=f"next_{idx}"):
            st.session_state.current_index += 1
            st.session_state.submitted_for_index = None
            st.session_state.last_result = None
            st.session_state.last_explanation = None
            st.session_state.last_correct_answer = None
            st.session_state.last_user_answer = None
            st.session_state.question_start_time = time.time() if mode == "Timed Quiz" else None
            st.rerun()

    st.stop()


# Timed Quiz mode
if mode == "Timed Quiz":
    if st.session_state.question_start_time is None:
        st.session_state.question_start_time = time.time()

    elapsed = int(time.time() - st.session_state.question_start_time)
    remaining = int(seconds_per_question) - elapsed
    if remaining < 0:
        remaining = 0

    if HAS_AUTOREFRESH and remaining > 0 and st.session_state.submitted_for_index != idx:
        st_autorefresh(interval=1000, key=f"refresh_{idx}")

    st.warning(f"Time remaining: {remaining} seconds")

    submit_now = st.button("Submit Now", key=f"submit_now_{idx}")
    time_up = (remaining <= 0)

    if (time_up or submit_now) and st.session_state.submitted_for_index != idx:
        correct = is_correct(q, user_response)
        if correct:
            st.session_state.score += 1

        st.session_state.history.append({
            "id": q.get("id", ""),
            "domain": q_domain,
            "subtopic": q_subtopic,
            "difficulty": q_diff,
            "correct": correct,
        })

        st.session_state.submitted_for_index = idx

        # Auto-advance
        st.session_state.current_index += 1
        st.session_state.question_start_time = time.time()
        st.session_state.submitted_for_index = None
        st.rerun()

    st.stop()