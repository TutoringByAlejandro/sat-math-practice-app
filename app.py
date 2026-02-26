import streamlit as st
import json
import random
import time
import io
import os
from datetime import datetime
from fractions import Fraction
from decimal import Decimal, InvalidOperation
from streamlit_autorefresh import st_autorefresh
import re

# PDF generation
from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import letter

# Charts
import matplotlib.pyplot as plt


# ============================================================
# Config
# ============================================================
st.set_page_config(page_title="SAT Math Practice", layout="wide")

st.markdown("""
<style>
div[role="radiogroup"] > label {
    margin-bottom: 16px;
}
</style>
""", unsafe_allow_html=True)
# ============================================================
# Load questions
# ============================================================
@st.cache_data
def load_questions(path: str):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


# ============================================================
# Helpers
# ============================================================
def normalize_choice(c):
    """
    Choices can be either:
      - a string (text choice)
      - an object: {"text": "...", "image": "images/....png"} (image or mixed choice)

    Returns a dict with keys: text, image
    """
    if c is None:
        return {"text": "", "image": None}
    if isinstance(c, str):
        return {"text": c, "image": None}
    if isinstance(c, dict):
        return {
            "text": c.get("text", "") if c.get("text") is not None else "",
            "image": c.get("image", None),
        }
    # fallback
    return {"text": str(c), "image": None}


def is_choice_obj_list(choices):
    return isinstance(choices, list) and len(choices) > 0 and isinstance(choices[0], dict)


def build_choice_label(choice_obj, idx):
    """
    Human-readable label for radio options.
    If it's image-only, label becomes "Option {idx+1}".
    If it has text, use that text.
    """
    txt = (choice_obj.get("text") or "").strip()
    if txt:
        return txt
    return f"Option {idx+1}"


def answer_matches(choice_obj, answer_value):
    """
    answer_value can be:
      - text answer (string)
      - image path answer (string)
    We consider it a match if it equals the choice text OR equals the choice image path.
    """
    if answer_value is None:
        return False
    a = str(answer_value).strip()
    txt = str(choice_obj.get("text") or "").strip()
    img = str(choice_obj.get("image") or "").strip()
    return (txt and a == txt) or (img and a == img)


def find_correct_choice_index(choices, answer_value):
    """
    Returns index of correct choice in normalized choices list, else None.
    """
    for i, c in enumerate(choices):
        if answer_matches(c, answer_value):
            return i
    return None


def safe_image_exists(path):
    if not path:
        return False
    return os.path.exists(path)


# ============================================================
# Question rendering
# ============================================================


_PLAIN_TEXT_PATTERNS = [
    re.compile(r"^\d+\s+and\s+\d+$", re.IGNORECASE),  # "9 and 3"
]



def to_latexish(s: str) -> str:
    s = str(s).strip()

    # Units like cm^2 -> \text{cm}^{2}  (also mm^2, m^3, etc.)
    s = re.sub(r"\b(cm|mm|m|km|in|ft)\^(\d+)\b", r"\\text{\1}^{\2}", s)

    # Use \cdot instead of ·
    s = s.replace("·", r"\cdot")

    return s


def format_for_radio_label(s: str) -> str:
    s = str(s).strip()

    # Only treat as LaTeX if it explicitly contains LaTeX commands
    if "\\" in s:
        clean = s.replace("$", "")
        return f"${clean}$"

    return s


def render_mcq(q, q_index):
    """
    Renders MCQ question. Supports:
      - question-level image q["image"]
      - choice images if choices are objects with {"text","image"}
    Returns (selected_value, submitted_bool)
    where selected_value is either:
      - choice text (if text choice) OR
      - choice image path (if image choice) OR
      - (for mixed) prefer text if exists else image
    """
    st.markdown("### Question")
    st.markdown(q["prompt"])
    if q.get("image"):
        # Show question image if present
        if safe_image_exists(q["image"]):
            st.image(q["image"], use_container_width=True)
        else:
            st.warning(f"Question image not found: {q['image']}")

    raw_choices = q.get("choices") or []
    choices = [normalize_choice(c) for c in raw_choices]

    # Build labels for the radio widget (must be strings)
    labels = [build_choice_label(c, i) for i, c in enumerate(choices)]

    # Use stable key per question render
    radio_key = f"mcq_choice_{q_index}_{q['id']}"

    st.write("Choose one:")

    

    selected_label = st.radio("", labels, key=radio_key, format_func=format_for_radio_label)

    # Show answer images if present (for object-based choices)
    any_choice_images = any(bool(c.get("image")) for c in choices)
    if any_choice_images:
        st.caption("Answer choice images:")
        cols = st.columns(2)
        for i, c in enumerate(choices):
            img = c.get("image")
            if img:
                with cols[i % 2]:
                    if safe_image_exists(img):
                        st.image(img, caption=f"Option {i+1}", use_container_width=True)
                    else:
                        st.warning(f"Missing image: {img}")

    # Map selected label back to stored value
    selected_idx = labels.index(selected_label)
    chosen_obj = choices[selected_idx]

    # If there is text, use text as the response value; else use image path.
    selected_value = (chosen_obj.get("text") or "").strip()
    if not selected_value:
        selected_value = chosen_obj.get("image")

    submitted = st.button("Submit Answer", key=f"submit_{q_index}_{q['id']}")
    return selected_value, submitted


def render_numeric(q, q_index):
    st.markdown(f"### {q['prompt']}")
    if q.get("image"):
        if safe_image_exists(q["image"]):
            st.image(q["image"], use_container_width=True)
        else:
            st.warning(f"Question image not found: {q['image']}")

    inp_key = f"num_{q_index}_{q['id']}"
    user_val = st.text_input("Your answer:", key=inp_key)
    submitted = st.button("Submit Answer", key=f"submit_{q_index}_{q['id']}")
    return user_val, submitted


def get_user_response_widget(q, q_index):
    q_type = q.get("type", "mcq")
    if q_type == "mcq":
        return render_mcq(q, q_index)
    elif q_type == "numeric":
        return render_numeric(q, q_index)
    else:
        st.error(f"Unsupported question type: {q_type}")
        return None, False


# ============================================================
# Results + Analytics helpers
# ============================================================

def _parse_decimal_to_fraction(s: str):
    try:
        d = Decimal(s).normalize()
        return Fraction(d)
    except:
        return None

def _parse_numeric(s: str):
    s = (s or "").strip()
    if not s:
        return None

    # fraction like 3/4
    if "/" in s:
        try:
            return Fraction(s)
        except:
            return None

    return _parse_decimal_to_fraction(s)

def is_correct_response(q, user_response):
    """
    Handles:
    - numeric equivalence (2 == 2.0 == 2.00)
    - normal mcq string matching
    """
    if user_response is None:
        return False

    q_type = (q.get("type") or "").lower()

    # --- Numeric type ---
    if q_type == "numeric":
        user_val = _parse_numeric(str(user_response))
        correct_val = _parse_numeric(str(q.get("answer")))

        if user_val is None or correct_val is None:
            return False

        return user_val == correct_val

    # --- MCQ / default ---
    a = str(q.get("answer", "")).strip()
    u = str(user_response).strip()
    return a == u

def choice_label_for_value(q, value):
    """
    Converts an answer VALUE into a user-friendly label.
    - If choices are dicts (image-based), show "Option N" (or explicit label if present).
    - If choices are strings (normal MCQ), return the string itself.
    """
    if value is None:
        return None

    value_str = str(value).strip()
    choices = q.get("choices")

    # Image-choice format: choices is a list of dicts
    if isinstance(choices, list) and len(choices) > 0 and isinstance(choices[0], dict):
        for i, c in enumerate(choices):
            # Support several possible keys to stay compatible with your JSON evolution
            c_val = c.get("value", None)
            if c_val is None:
                c_val = c.get("image", None)
            if c_val is None:
                c_val = c.get("text", None)

            if c_val is not None and str(c_val).strip() == value_str:
                return c.get("label") or f"Option {i+1}"

        # If not found, fallback to the raw value
        return value_str

    # Normal MCQ format: choices is a list of strings
    return value_str



def compute_summary(history):
    total = len(history)
    correct = sum(1 for h in history if h.get("correct"))
    incorrect = total - correct
    accuracy = (correct / total) if total else 0.0
    return {
        "total": total,
        "correct": correct,
        "incorrect": incorrect,
        "accuracy": accuracy,
    }


def build_charts(history):
    """
    Returns list of chart dicts:
      {"title":..., "fig": matplotlib_figure}
    """
    charts = []
    if not history:
        return charts

    # ----------------------------
    # Accuracy by difficulty
    # ----------------------------
    by_diff = {}
    for h in history:
        d = h.get("difficulty")
        if d is None:
            continue
        by_diff.setdefault(d, {"n": 0, "c": 0})
        by_diff[d]["n"] += 1
        if h.get("correct"):
            by_diff[d]["c"] += 1

    diffs = sorted(by_diff.keys())
    accs = [(by_diff[d]["c"] / by_diff[d]["n"]) for d in diffs]

    fig1 = plt.figure()
    plt.plot(diffs, accs, marker="o")
    plt.xlabel("Difficulty")
    plt.ylabel("Accuracy")
    plt.ylim(0, 1)
    plt.title("Accuracy by Difficulty")
    charts.append({"title": "Accuracy by Difficulty", "fig": fig1})

    # ----------------------------
    # Accuracy by domain  ✅ restored
    # ----------------------------
    by_domain = {}
    for h in history:
        dom = h.get("domain", "Unknown")
        by_domain.setdefault(dom, {"n": 0, "c": 0})
        by_domain[dom]["n"] += 1
        if h.get("correct"):
            by_domain[dom]["c"] += 1

    domains_sorted = sorted(by_domain.keys())
    dom_accs = [(by_domain[d]["c"] / by_domain[d]["n"]) for d in domains_sorted]

    fig2 = plt.figure()
    plt.bar(domains_sorted, dom_accs)
    plt.xlabel("Domain")
    plt.ylabel("Accuracy")
    plt.ylim(0, 1)
    plt.title("Accuracy by Domain")
    plt.xticks(rotation=25, ha="right")
    charts.append({"title": "Accuracy by Domain", "fig": fig2})

     # ----------------------------
    # ✅ Accuracy by subtopic (ONE CHART PER DOMAIN)
    # ----------------------------
    # Structure: by_dom_sub[domain][subtopic] = {"n":..., "c":...}
    by_dom_sub = {}
    for h in history:
        dom = h.get("domain", "Unknown")
        sub = h.get("subtopic", "Unknown")
        by_dom_sub.setdefault(dom, {})
        by_dom_sub[dom].setdefault(sub, {"n": 0, "c": 0})
        by_dom_sub[dom][sub]["n"] += 1
        if h.get("correct"):
            by_dom_sub[dom][sub]["c"] += 1

    for dom in sorted(by_dom_sub.keys()):
        subtopics = sorted(by_dom_sub[dom].keys())
        # If you want to hide a domain chart when there's only 1 subtopic attempted, uncomment:
        # if len(subtopics) < 2:
        #     continue

        sub_accs = []
        for s in subtopics:
            n = by_dom_sub[dom][s]["n"]
            c = by_dom_sub[dom][s]["c"]
            sub_accs.append(c / n if n else 0)

        fig = plt.figure()
        plt.bar(subtopics, sub_accs)
        plt.xlabel("Subtopic")
        plt.ylabel("Accuracy")
        plt.ylim(0, 1)
        plt.title(f"Accuracy by Subtopic — {dom}")
        plt.xticks(rotation=25, ha="right")

        charts.append({"title": f"Accuracy by Subtopic — {dom}", "fig": fig})

    return charts

# ============================================================
# PDF Export (fixed)
# ============================================================
def generate_results_pdf(summary, history, charts):
    """
    Returns: (pdf_bytes, error_message)
    """
    try:
        buffer = io.BytesIO()
        c = canvas.Canvas(buffer, pagesize=letter)
        width, height = letter

        x_margin = 50
        y = height - 50

        def line(text, dy=16, font="Helvetica", size=11):
            nonlocal y
            c.setFont(font, size)
            c.drawString(x_margin, y, text)
            y -= dy

        # Title
        line("SAT Math Practice — Results Report", dy=24, font="Helvetica-Bold", size=16)
        line(f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}", dy=20)

        # Summary
        line("Summary", dy=18, font="Helvetica-Bold", size=14)
        line(f"Total questions: {summary['total']}")
        line(f"Correct: {summary['correct']}")
        line(f"Incorrect: {summary['incorrect']}")
        line(f"Accuracy: {summary['accuracy']*100:.1f}%")

        y -= 10

        # Charts: render each fig to PNG bytes, then drawImage via ImageReader
        from reportlab.lib.utils import ImageReader

        for ch in charts:
            # new page if needed
            if y < 280:
                c.showPage()
                y = height - 50

            line(ch["title"], dy=18, font="Helvetica-Bold", size=13)

            img_buf = io.BytesIO()
            ch["fig"].savefig(img_buf, format="png", bbox_inches="tight", dpi=150)
            img_buf.seek(0)

            img_reader = ImageReader(img_buf)
            # Fit into page width
            img_w = width - 2 * x_margin
            img_h = 220
            c.drawImage(img_reader, x_margin, y - img_h, width=img_w, height=img_h, preserveAspectRatio=True)
            y -= (img_h + 20)

        # Question history
        if y < 200:
            c.showPage()
            y = height - 50

        line("Question History", dy=18, font="Helvetica-Bold", size=14)
        for i, h in enumerate(history, start=1):
            status = "Correct" if h.get("correct") else "Incorrect"
            dom = h.get("domain", "")
            sub = h.get("subtopic", "")
            diff = h.get("difficulty", "")
            qid = h.get("id", "")

            # Wrap to next page if low
            if y < 90:
                c.showPage()
                y = height - 50
                line("Question History (cont.)", dy=18, font="Helvetica-Bold", size=14)

            line(f"{i}. [{status}] {dom} | {sub} | Diff {diff}", dy=14)
            if qid:
                line(f"    ID: {qid}", dy=14)

        c.save()
        pdf_bytes = buffer.getvalue()
        buffer.close()
        return pdf_bytes, None

    except Exception as e:
        return None, str(e)


# ============================================================
# Timed mode: per-question timer (fixed)
# ============================================================
def init_timer(seconds_per_q: int):
    st.session_state.time_per_question = int(seconds_per_q)
    st.session_state.q_start_time = time.time()


def time_left():
    t0 = st.session_state.get("q_start_time", None)
    per = st.session_state.get("time_per_question", None)
    if t0 is None or per is None:
        return None
    elapsed = time.time() - t0
    remaining = int(per - elapsed)
    return max(0, remaining)


def tick_timer_ui():
    """
    Just DISPLAY the timer (no sleep, no rerun).
    We'll trigger reruns at the VERY END of the page render so the question shows.
    """
    remaining = time_left()
    if remaining is None:
        return
    st.markdown(f"**Time remaining: {remaining} seconds**")


# ============================================================
# App state init
# ============================================================
if "mode" not in st.session_state:
    st.session_state.mode = "Practice"

if "quiz_started" not in st.session_state:
    st.session_state.quiz_started = False

if "current_index" not in st.session_state:
    st.session_state.current_index = 0

if "history" not in st.session_state:
    st.session_state.history = []

if "quiz_questions" not in st.session_state:
    st.session_state.quiz_questions = []

if "submitted_current" not in st.session_state:
    st.session_state.submitted_current = False

if "last_feedback" not in st.session_state:
    st.session_state.last_feedback = None


# ============================================================
# Sidebar settings
# ============================================================
st.sidebar.title("Settings")

mode = st.sidebar.radio("Mode", ["Practice", "Timed Quiz"], index=0 if st.session_state.mode == "Practice" else 1)
st.session_state.mode = mode

# Load questions file
QUESTIONS_PATH = "question_bank.json"
questions_all = load_questions(QUESTIONS_PATH)

domains = sorted(list({q["domain"] for q in questions_all}))
subtopics = sorted(list({q["subtopic"] for q in questions_all}))

selected_domains = st.sidebar.multiselect("Select Domain(s)", domains, default=domains[:])
selected_subtopics = st.sidebar.multiselect("Select Subtopic(s)", subtopics, default=subtopics[:])

diff_min, diff_max = st.sidebar.slider("Difficulty range (1–5)", 1, 5, (1, 5))
num_questions = st.sidebar.number_input("Number of Questions", min_value=1, max_value=50, value=12, step=1)

# Timed settings: restored + working
seconds_per_question = None
if mode == "Timed Quiz":
    seconds_per_question = st.sidebar.number_input(
        "Seconds per question",
        min_value=10,
        max_value=600,
        value=int(st.session_state.get("time_per_question", 60)),
        step=5,
    )

# Filter questions
filtered = [
    q for q in questions_all
    if q["domain"] in selected_domains
    and q["subtopic"] in selected_subtopics
    and diff_min <= int(q.get("difficulty", 1)) <= diff_max
]

st.sidebar.caption(f"Questions available with filters: {len(filtered)}")

start = st.sidebar.button("Start Quiz")
reset = st.sidebar.button("Reset / Back to Settings")

if reset:
    st.session_state.quiz_started = False
    st.session_state.current_index = 0
    st.session_state.history = []
    st.session_state.quiz_questions = []
    st.session_state.submitted_current = False
    st.session_state.last_feedback = None
    if "q_start_time" in st.session_state:
        del st.session_state.q_start_time

if start:
    st.session_state.quiz_started = True
    st.session_state.current_index = 0
    st.session_state.history = []
    st.session_state.submitted_current = False
    st.session_state.last_feedback = None

    # Choose questions
    if len(filtered) == 0:
        st.sidebar.error("No questions match your filters.")
        st.session_state.quiz_started = False
    else:
        st.session_state.quiz_questions = random.sample(filtered, k=min(int(num_questions), len(filtered)))

        # Init timer if timed mode
        if mode == "Timed Quiz":
            init_timer(int(seconds_per_question))


# ============================================================
# Main UI
# ============================================================
st.title("SAT Math Practice")

if not st.session_state.quiz_started:
    st.info("Choose your settings on the left and click **Start Quiz**.")
    st.stop()

quiz = st.session_state.quiz_questions
idx = st.session_state.current_index

if idx >= len(quiz):
    st.success("Quiz complete!")

    summary = compute_summary(st.session_state.history)
    st.subheader("Results")
    st.write(summary)

    charts = build_charts(st.session_state.history)
    for ch in charts:
        st.pyplot(ch["fig"])

    st.subheader("Export")
    pdf_bytes, pdf_err = generate_results_pdf(summary, st.session_state.history, charts)
    if pdf_err:
        st.error(f"PDF export error: {pdf_err}")
    else:
        st.download_button(
            "Download PDF Report",
            data=pdf_bytes,
            file_name="sat_math_results_report.pdf",
            mime="application/pdf",
        )

    st.stop()

q = quiz[idx]

st.markdown(f"## Question {idx+1} of {len(quiz)}")
st.caption(f"Domain: {q['domain']} | Subtopic: {q['subtopic']} | Difficulty: {q['difficulty']}")


# Timer UI (working)
if mode == "Timed Quiz":
    
    # --- Initialize timer ONLY once per question ---
    current_qid = q.get("id",f"idx_{idx}")
    if st.session_state.get("timer_qid") != current_qid:
        init_timer(int(st.session_state.get("time_per_question",60)))
        st.session_state.timer_qid = current_qid
    # Show updating timer
    tick_timer_ui()
    # ✅ ADD THIS BLOCK RIGHT HERE (do not put it anywhere else)
    remaining = time_left()
    if (
        remaining is not None
        and remaining > 0
        and not st.session_state.get("submitted_current", False)
    ):
        st_autorefresh(interval=1000, key=f"timer_refresh_{current_qid}")


    # If time expired and not submitted, auto-mark incorrect and advance
    if time_left() == 0 and not st.session_state.submitted_current:
        # Auto-submit as blank
        user_response = ""
        correct = False
        st.session_state.history.append({
            "id": q.get("id"),
            "domain": q.get("domain"),
            "subtopic": q.get("subtopic"),
            "difficulty": q.get("difficulty"),
            "correct": False,
            "user_response": user_response,
            "answer": q.get("answer"),
        })
        st.session_state.last_feedback = {
            "correct": False,
            "message": "Time's up!",
            "correct_label": None,
        }
        st.session_state.submitted_current = True

# Question widget
user_response, submitted = get_user_response_widget(q, idx)

# Handle submission
if submitted and not st.session_state.submitted_current:
    correct = is_correct_response(q, user_response)

    st.session_state.history.append({
        "id": q.get("id"),
        "domain": q.get("domain"),
        "subtopic": q.get("subtopic"),
        "difficulty": q.get("difficulty"),
        "correct": correct,
        "user_response": user_response,
        "answer": q.get("answer"),
    })

    # Build better feedback for image answers (show "Option X" and/or display the image)
    correct_choice_label = None
    if q.get("type") == "mcq":
        raw_choices = q.get("choices") or []
        choices_norm = [normalize_choice(c) for c in raw_choices]
        correct_idx = find_correct_choice_index(choices_norm, q.get("answer"))
        if correct_idx is not None:
            correct_choice_label = build_choice_label(choices_norm[correct_idx], correct_idx)

    st.session_state.last_feedback = {
        "correct": correct,
        "message": "Correct!" if correct else "Incorrect.",
        "correct_label": correct_choice_label,
        "correct_answer": choice_label_for_value(q,q.get("answer")),
        "correct_choice_index": None if correct_choice_label is None else correct_choice_label,
    }

    st.session_state.submitted_current = True

# Feedback block
if st.session_state.submitted_current and st.session_state.last_feedback:
    fb = st.session_state.last_feedback
    if fb["correct"]:
        st.success(fb["message"])
    else:
        # If image-based correct answer, show label + image if available
        if fb.get("correct_label"):
            st.error(f"{fb['message']} Correct answer: **{fb['correct_label']}**")
        else:
            st.error(f"{fb['message']} Correct answer: **{q.get('answer')}**")

        # If the correct answer is an image path, display it to make it unambiguous
        ans = q.get("answer")
        if isinstance(ans, str) and ans.lower().endswith((".png", ".jpg", ".jpeg", ".webp")):
            if safe_image_exists(ans):
                st.image(ans, caption="Correct answer image", use_container_width=True)

    st.info(f"Explanation: {q.get('explanation','')}")

    if st.button("Next Question"):
        st.session_state.current_index += 1
        st.session_state.submitted_current = False
        st.session_state.last_feedback = None

        # Reset timer for next question in timed mode
        if mode == "Timed Quiz":
            init_timer(int(st.session_state.get("time_per_question", 60)))

        st.rerun()
    # --- Timed mode live refresh (rerun AFTER rendering the page) ---
    if mode == "Timed Quiz":
        remaining = time_left()
        if remaining is not None and remaining > 0 and not st.session_state.get("submitted_current", False):
            time.sleep(0.25)
            st.rerun()
