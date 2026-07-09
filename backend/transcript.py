"""
transcript.py
--------------
Handles extraction of a YouTube video ID from arbitrary URL formats, and
retrieval + stitching of the video's transcript into a single flowing
paragraph suitable for downstream LLM processing.

Public API:
    extract_video_id(url: str) -> str
    get_transcript(url: str) -> str

Both functions raise clear, explicit exceptions on failure so callers
(app.py) can translate them into structured JSON error responses.
"""

import re

from youtube_transcript_api import YouTubeTranscriptApi
from youtube_transcript_api._errors import (
    TranscriptsDisabled,
    NoTranscriptFound,
    VideoUnavailable,
    CouldNotRetrieveTranscript,
)


class InvalidYouTubeURLError(Exception):
    """Raised when a video ID cannot be parsed from the supplied URL."""


class TranscriptUnavailableError(Exception):
    """Raised when a transcript cannot be retrieved for a valid video ID."""


# Matches an 11-character YouTube video ID (letters, digits, - and _).
_VIDEO_ID_PATTERN = r"([a-zA-Z0-9_-]{11})"

# Ordered list of regex patterns covering every common YouTube URL shape:
#   - Standard watch URLs, with or without extra query params / timestamps
#   - Shortened youtu.be links
#   - Embed URLs
#   - Live URLs
#   - Shorts URLs
_URL_PATTERNS = [
    re.compile(r"(?:youtube\.com|m\.youtube\.com)/watch\?(?:.*&)?v=" + _VIDEO_ID_PATTERN),
    re.compile(r"youtu\.be/" + _VIDEO_ID_PATTERN),
    re.compile(r"(?:youtube\.com|m\.youtube\.com)/embed/" + _VIDEO_ID_PATTERN),
    re.compile(r"(?:youtube\.com|m\.youtube\.com)/live/" + _VIDEO_ID_PATTERN),
    re.compile(r"(?:youtube\.com|m\.youtube\.com)/shorts/" + _VIDEO_ID_PATTERN),
    # Fallback: a bare 11-char ID anywhere in a v= param, tolerant of
    # ordering/timestamp noise like &t=90s appearing before or after v=.
    re.compile(r"[?&]v=" + _VIDEO_ID_PATTERN),
]


def extract_video_id(url: str) -> str:
    """
    Extract the 11-character YouTube video ID from any supported URL format.

    Supports:
        https://www.youtube.com/watch?v=VIDEOID
        https://www.youtube.com/watch?v=VIDEOID&t=90s
        https://youtu.be/VIDEOID
        https://youtu.be/VIDEOID?t=90
        https://www.youtube.com/embed/VIDEOID
        https://www.youtube.com/live/VIDEOID
        https://www.youtube.com/shorts/VIDEOID
        https://m.youtube.com/watch?v=VIDEOID

    Raises:
        InvalidYouTubeURLError: if no valid video ID can be located.

    Returns:
        str: the 11-character video ID.
    """
    if not url or not isinstance(url, str):
        raise InvalidYouTubeURLError("A non-empty YouTube URL string is required.")

    cleaned_url = url.strip()

    for pattern in _URL_PATTERNS:
        match = pattern.search(cleaned_url)
        if match:
            return match.group(1)

    raise InvalidYouTubeURLError(
        f"Could not extract a valid YouTube video ID from the provided URL: '{url}'"
    )


def _stitch_transcript(transcript_fragments: list) -> str:
    """
    Combine timed transcript fragments (list of dicts with a 'text' key)
    into a single, clean, flowing paragraph of text.

    - Strips leading/trailing whitespace on each fragment.
    - Collapses internal newlines within a fragment into spaces.
    - Removes bracketed sound-effect / music annotations (e.g. "[Music]").
    - Joins fragments with single spaces and normalizes repeated whitespace.
    """
    pieces = []
    for fragment in transcript_fragments:
        text = fragment.get("text", "")
        text = text.replace("\n", " ").strip()
        # Drop non-speech annotations like [Music], [Applause], [Laughter]
        text = re.sub(r"\[[^\]]*\]", "", text)
        text = text.strip()
        if text:
            pieces.append(text)

    full_text = " ".join(pieces)
    # Normalize any resulting double spaces
    full_text = re.sub(r"\s{2,}", " ", full_text).strip()
    return full_text


def get_transcript(url: str) -> str:
    video_id = extract_video_id(url)

    try:
        api = YouTubeTranscriptApi()

        transcript_list = api.list(video_id)

        try:
            transcript = transcript_list.find_transcript(
                ["en", "en-US", "en-GB", "en-IN"]
            )
        except NoTranscriptFound:
            transcript = transcript_list.find_generated_transcript(
                ["en", "en-US", "en-GB", "en-IN"]
            )

        fragments = transcript.fetch()

        stitched = _stitch_transcript(fragments)

        if not stitched:
            raise TranscriptUnavailableError(
                "Transcript is empty."
            )

        return stitched

    except TranscriptsDisabled:
        raise TranscriptUnavailableError(
            "Captions are disabled for this video."
        )

    except NoTranscriptFound:
        raise TranscriptUnavailableError(
            "No English transcript found."
        )

    except VideoUnavailable:
        raise TranscriptUnavailableError(
            "Video unavailable."
        )

    except CouldNotRetrieveTranscript:
        raise TranscriptUnavailableError(
            "Could not retrieve transcript."
        )

    except Exception as e:
        raise TranscriptUnavailableError(
            f"{type(e).__name__}: {e}"
        )