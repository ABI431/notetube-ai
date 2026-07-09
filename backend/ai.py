"""
ai.py
-----
Interfaces with Google's Gemini API (via the google-genai SDK) to transform
a raw video transcript into structured academic study material.

Public API:
    generate_notes(transcript: str) -> str

Returns a single Markdown string containing:
    - Executive Summary
    - Comprehensive Chapter Notes
    - Key Takeaways
    - 20 Multiple Choice Questions + separate Answer Key
    - 10 Interview Questions
"""

import os

from google import genai
from google.genai import types

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-2.5-flash")

# Gemini 2.5 Flash comfortably supports very large single-shot contexts, but
# we still guard against pathological inputs (multi-hour lecture series,
# stitched playlists, etc.) by chunking above this threshold and merging the
# per-chunk notes with a final synthesis pass.
CHUNK_CHAR_THRESHOLD = 60_000
CHUNK_SIZE = 45_000
CHUNK_OVERLAP = 500


class AIGenerationError(Exception):
    """Raised when the Gemini API call fails or returns an unusable response."""


class MissingAPIKeyError(Exception):
    """Raised when GEMINI_API_KEY is not configured in the environment."""


def _get_client() -> genai.Client:
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        raise MissingAPIKeyError(
            "GEMINI_API_KEY is not set. Add it to your backend/.env file."
        )
    return genai.Client(api_key=api_key)


# ---------------------------------------------------------------------------
# Prompts
# ---------------------------------------------------------------------------

_SYSTEM_PROMPT = """You are an elite university professor and top-tier technical writer, \
producing publication-quality study material from lecture transcripts.

STRICT OUTPUT RULES:
1. Return output EXCLUSIVELY in valid Markdown syntax. Use '#', '##', '###' for \
hierarchical headings, '**bold**' for key terms, standard '-' bullet lists, numbered \
lists where sequence matters, Markdown tables for comparisons, and fenced code blocks \
(```) for any code, formulas, or syntax examples.
2. Do not include any commentary about yourself, the transcript, or the process. \
Output only the requested document.
3. Do not reference timestamps, filler words, or transcription artifacts.

REQUIRED DOCUMENT STRUCTURE, IN THIS EXACT ORDER:

# Executive Summary
A tight 150-250 word overview of what this material covers and why it matters.

# Comprehensive Notes
Fully structured chapter-style notes using '##' for major topics and '###' for \
sub-topics. Bold all key terms on first use. Include a one-line plain-English \
definition immediately after any technical term. Use tables to compare related \
concepts wherever that clarifies the material. Use fenced code blocks for any code, \
mathematical notation, or step-by-step procedures mentioned in the source.

# Key Takeaways
A concise bullet list of the 5-10 most important points a student must remember.

# Multiple Choice Questions
Exactly 20 numbered multiple-choice questions (1-20), each with four options \
labeled A-D, testing understanding of the material at varying difficulty levels \
(recall, application, analysis).

# Answer Key
A separate, clearly labeled section listing the correct letter answer for each of \
the 20 MCQs above (e.g. "1. C"), with a one-sentence justification for each answer.

# Interview Questions
Exactly 10 numbered, highly technical interview-style questions that probe deep \
understanding of the material, suitable for a technical interview on this subject.

ANTI-HALLUCINATION GUARDRAIL (MOST IMPORTANT RULE):
Ground every assertion exclusively in facts explicitly stated in the provided \
transcript. Do not extrapolate, invent outside sources, fabricate statistics, or \
hypothesize beyond the textual context given to you. If the transcript's content is \
too thin to fully populate a section (e.g. fewer distinct concepts than would \
normally warrant 20 rich MCQs), draw additional MCQs and interview questions strictly \
from variations, edge cases, and applications of the concepts that ARE present in the \
transcript, rather than introducing unrelated external knowledge."""

_CHUNK_NOTES_PROMPT = """You are an elite university professor. The following is one \
segment of a longer lecture transcript. Produce detailed, well-structured Markdown \
notes for ONLY this segment: use '##' and '###' headings, bold key terms, and tables \
or code blocks where useful. Ground every statement strictly in this text; do not \
invent outside information. Do not add an Executive Summary, MCQs, or Interview \
Questions here — those will be generated later from the merged notes. Output only the \
segment notes in Markdown."""

_MERGE_PROMPT = """You are an elite university professor and technical writer. Below \
are chapter notes generated separately from consecutive segments of the same lecture. \
Synthesize them into ONE unified, non-redundant, logically flowing set of study \
material, strictly following this exact Markdown structure and order:

# Executive Summary
# Comprehensive Notes
# Key Takeaways
# Multiple Choice Questions
# Answer Key
# Interview Questions

Requirements: exactly 20 MCQs (A-D options) with a separate Answer Key including \
one-sentence justifications, and exactly 10 technical Interview Questions. Merge \
duplicate or overlapping points from the segment notes rather than repeating them. \
Ground every assertion strictly in the segment notes provided below — do not invent \
outside facts.

SEGMENT NOTES:
{combined_segments}"""


# ---------------------------------------------------------------------------
# Core generation logic
# ---------------------------------------------------------------------------

def _call_gemini(client: genai.Client, system_prompt: str, user_content: str) -> str:
    try:
        response = client.models.generate_content(
            model=GEMINI_MODEL,
            contents=user_content,
            config=types.GenerateContentConfig(
                system_instruction=system_prompt,
                temperature=0.4,
                max_output_tokens=8192,
            ),
        )
    except Exception as exc:  # noqa: BLE001
        raise AIGenerationError(f"Gemini API request failed: {exc}")

    text = getattr(response, "text", None)
    if not text or not text.strip():
        raise AIGenerationError("Gemini returned an empty response.")

    return text.strip()


def _chunk_transcript(transcript: str) -> list:
    """Split a very long transcript into overlapping chunks on whitespace
    boundaries to avoid cutting mid-sentence as much as possible."""
    chunks = []
    start = 0
    length = len(transcript)

    while start < length:
        end = min(start + CHUNK_SIZE, length)
        if end < length:
            # Try to break at the last sentence boundary within the chunk.
            boundary = transcript.rfind(". ", start, end)
            if boundary != -1 and boundary > start:
                end = boundary + 1
        chunks.append(transcript[start:end].strip())
        start = max(end - CHUNK_OVERLAP, end) if end >= length else end - CHUNK_OVERLAP

    return [c for c in chunks if c]


def generate_notes(transcript: str) -> str:
    """
    Generate structured academic study notes (Markdown) from a transcript.

    For short/medium transcripts this is a single Gemini call. For very long
    transcripts, the transcript is chunked, notes are generated per chunk,
    and a final synthesis pass merges them into the required document shape.

    Raises:
        MissingAPIKeyError: if GEMINI_API_KEY is not configured.
        AIGenerationError: if any Gemini call fails or returns nothing usable.
    """
    if not transcript or not transcript.strip():
        raise AIGenerationError("Cannot generate notes from an empty transcript.")

    client = _get_client()

    if len(transcript) <= CHUNK_CHAR_THRESHOLD:
        return _call_gemini(client, _SYSTEM_PROMPT, transcript)

    # --- Long transcript path: chunk -> per-chunk notes -> merge/synthesize ---
    chunks = _chunk_transcript(transcript)
    segment_notes = []
    for index, chunk in enumerate(chunks, start=1):
        notes = _call_gemini(client, _CHUNK_NOTES_PROMPT, chunk)
        segment_notes.append(f"### Segment {index}\n{notes}")

    combined_segments = "\n\n".join(segment_notes)
    merge_content = _MERGE_PROMPT.format(combined_segments=combined_segments)

    return _call_gemini(client, _SYSTEM_PROMPT, merge_content)
