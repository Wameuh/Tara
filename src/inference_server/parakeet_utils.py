"""Utility functions for NeMo Parakeet ASR backend."""

from __future__ import annotations

import logging
import os
import subprocess
import tempfile
from pathlib import Path
from typing import Any

import numpy as np

from inference_server.backend import BackendError
from inference_server.models import TranscriptionSegment

LOGGER = logging.getLogger(__name__)


def ensure_mono_audio(audio_path: str | Path) -> Path:
    """Convert audio to mono (16kHz) if needed, returning path to converted file.

    NeMo ASR models require mono audio at 16kHz. This function tries multiple
    methods to convert audio (librosa, ffmpeg).

    Args:
        audio_path: Path to input audio file (str or Path)

    Returns:
        Path to mono WAV file (temp file with converted audio)

    Raises:
        BackendError: If neither librosa nor ffmpeg is available, or if conversion fails
    """
    audio_path_str = str(audio_path)

    # Try librosa first (most Pythonic, handles many formats)
    try:
        import librosa
        import soundfile as sf

        # Load audio without conversion to check format
        y, sr = librosa.load(audio_path_str, sr=None, mono=False)

        # Convert to mono if needed
        if y.ndim > 1:
            y = librosa.to_mono(y)

        # Resample to 16kHz if needed
        if sr != 16000:
            y = librosa.resample(y, orig_sr=sr, target_sr=16000)
            sr = 16000

        # Save to temporary WAV file
        temp_dir = tempfile.gettempdir()
        base_name = Path(audio_path_str).stem
        temp_file = os.path.join(temp_dir, f"nemo_mono_{base_name}.wav")
        sf.write(temp_file, y, sr, format='WAV')
        LOGGER.debug("Converted audio to mono 16kHz using librosa: %s", temp_file)
        return Path(temp_file)
    except ImportError:
        # librosa not available, try ffmpeg
        pass
    except Exception as e:
        # librosa failed, try ffmpeg
        LOGGER.debug("librosa conversion failed: %s, trying ffmpeg...", e)

    # Fallback to ffmpeg (most reliable, handles all formats)
    try:
        temp_dir = tempfile.gettempdir()
        base_name = Path(audio_path_str).stem
        temp_file = os.path.join(temp_dir, f"nemo_mono_{base_name}.wav")

        # Use ffmpeg to convert to mono 16kHz WAV
        subprocess.run(
            [
                "ffmpeg",
                "-i", audio_path_str,
                "-ac", "1",  # mono
                "-ar", "16000",  # 16kHz sample rate
                "-y",  # overwrite
                temp_file
            ],
            check=True,
            capture_output=True,
            text=True
        )
        LOGGER.debug("Converted audio to mono 16kHz using ffmpeg: %s", temp_file)
        return Path(temp_file)
    except FileNotFoundError:
        raise BackendError(
            "No audio conversion library found. Please install one of:\n"
            "  - librosa: pip install librosa soundfile\n"
            "  - ffmpeg: conda install -c conda-forge ffmpeg\n"
            "    or download from https://ffmpeg.org/download.html"
        ) from None
    except subprocess.CalledProcessError as e:
        error_msg = e.stderr.decode('utf-8', errors='replace') if isinstance(e.stderr, bytes) else str(e.stderr)
        raise BackendError(
            f"ffmpeg conversion failed: {error_msg}\n"
            "Make sure ffmpeg is installed and the input file is valid."
        ) from e


def _extract_text_from_result(result: Any) -> str:
    """Extract text string from transcription result (handles both str and Hypothesis objects).

    Args:
        result: Transcription result from NeMo (can be str, Hypothesis object, or other)

    Returns:
        Extracted text as a string
    """
    if isinstance(result, str):
        return result
    # Hypothesis object has .text attribute
    if hasattr(result, 'text'):
        return result.text
    # Try to convert to string
    return str(result)


def _extract_timestamps_from_result(result: Any) -> dict[str, Any] | None:
    """Extract timestamp information from transcription result.

    Args:
        result: Transcription result from NeMo (can be str, Hypothesis, or dict-like object)

    Returns:
        Dictionary with timestamp information (segment, word, char) or None if not available
    """
    # Check if result has timestamp attribute
    if hasattr(result, 'timestamp'):
        timestamp_data = result.timestamp
        if isinstance(timestamp_data, dict):
            return timestamp_data
    # Check if result is dict-like
    if isinstance(result, dict) and 'timestamp' in result:
        return result['timestamp']
    # No timestamps available
    return None


def _find_overlap_start(text1: str, text2: str, min_overlap_words: int = 3, tolerance: float = 0.8) -> int:
    """Find where text2 starts overlapping with the end of text1.

    Returns the index in text2 where the overlap starts, or 0 if no overlap found.
    Uses word-based matching with tolerance for minor transcription differences.

    Args:
        text1: Previous chunk transcription
        text2: Current chunk transcription
        min_overlap_words: Minimum number of words to consider as overlap
        tolerance: Minimum similarity ratio (0-1) for overlap match (default: 0.8)

    Returns:
        Number of words to skip from the start of text2 (overlap size)
    """
    words1 = text1.split()
    words2 = text2.split()

    if len(words1) < min_overlap_words or len(words2) < min_overlap_words:
        return 0

    # Look for overlap starting from the end of text1
    # Try different overlap lengths, starting from longest (most reliable)
    max_check = min(len(words1), len(words2), 50)  # Limit search to avoid performance issues

    best_match_len = 0
    best_match_score = 0.0

    for overlap_len in range(max_check, min_overlap_words - 1, -1):
        # Check if the last 'overlap_len' words of text1 match the first 'overlap_len' words of text2
        end_words1 = words1[-overlap_len:]
        start_words2 = words2[:overlap_len]

        # Exact match (most reliable)
        if end_words1 == start_words2:
            return overlap_len

        # Fuzzy match: count how many words match (allowing for small differences)
        matches = sum(1 for w1, w2 in zip(end_words1, start_words2) if w1.lower() == w2.lower())
        similarity = matches / overlap_len

        if similarity >= tolerance and overlap_len > best_match_len:
            best_match_len = overlap_len
            best_match_score = similarity

    # Return best match if it meets tolerance threshold
    if best_match_len >= min_overlap_words and best_match_score >= tolerance:
        return best_match_len

    # No overlap found
    return 0


def _merge_overlapping_transcriptions(transcriptions: list[str], overlap_ratio: float = 0.1) -> str:
    """Merge transcriptions from overlapping chunks, removing duplicates.

    Args:
        transcriptions: List of transcribed text from each chunk
        overlap_ratio: Estimated ratio of overlap (used as fallback if detection fails)

    Returns:
        Merged text without duplicates
    """
    if not transcriptions:
        return ""

    if len(transcriptions) == 1:
        return transcriptions[0]

    merged = [transcriptions[0]]

    for i in range(1, len(transcriptions)):
        prev_text = merged[-1]
        curr_text = transcriptions[i]

        # Find where current text starts overlapping with previous
        overlap_start = _find_overlap_start(prev_text, curr_text, min_overlap_words=3)

        if overlap_start > 0:
            # Found overlap, use only the non-overlapping part
            words_to_add = curr_text.split()[overlap_start:]
            if words_to_add:
                merged.append(" ".join(words_to_add))
                LOGGER.debug("Removed %d overlapping words from chunk %d", overlap_start, i + 1)
            # If all words were overlap, don't add anything (shouldn't happen but handle gracefully)
        else:
            # No clear overlap found, use a simple heuristic
            # Try to remove approximate overlap based on overlap_ratio
            curr_words = curr_text.split()
            words_to_remove = max(0, int(len(curr_words) * overlap_ratio))
            if words_to_remove > 0 and len(curr_words) > words_to_remove:
                words_to_add = curr_words[words_to_remove:]
                if words_to_add:
                    merged.append(" ".join(words_to_add))
            else:
                # Fallback: just append the text
                merged.append(curr_text)

    return " ".join(merged)


def transcribe_with_nemo_partial_audio(
    model: Any,  # nemo_asr.models.ASRModel
    audio_path: Path,
    chunk_secs: float = 400.0,
    overlap_percentage: float = 5.0,
    timestamps: bool = True,
    logger: logging.Logger | None = None,
) -> dict[str, Any]:
    """Transcribe large audio files using chunked approach with manifest-based planning.

    This function creates a manifest file with 'offset' and 'duration' fields for each chunk,
    then processes each chunk individually. This is an alternative to manual chunking that
    uses a manifest structure for better organization.
    Chunks can overlap to avoid cutting words at boundaries (similar to manual chunking).

    Args:
        model: NeMo ASR model (nemo.collections.asr.models.ASRModel)
        audio_path: Path to audio file (will be converted to mono 16kHz if needed)
        chunk_secs: Length of each chunk in seconds (default: 400.0)
        overlap_percentage: Overlap between chunks as percentage of chunk size (default: 5.0, i.e., 5%)
        timestamps: Whether to include timestamp information (default: True)
        logger: Optional logger instance

    Returns:
        Dictionary with keys:
        - 'text': Transcribed text as a single string (merged, without duplicates if overlap > 0)
        - 'timestamps': Dictionary with segment, word, and char level timestamps (if timestamps=True)
        - 'segment_timestamps': List of segment timestamps with absolute times adjusted for chunk offsets
        - 'duration': Audio duration in seconds

    Raises:
        BackendError: If audio conversion fails or transcription fails
    """
    import json

    # Try to import librosa, but allow function to be mocked even if unavailable
    try:
        import librosa
        import soundfile as sf
    except ImportError:
        # If librosa is not available, we'll rely on ensure_mono_audio which
        # handles this case. This allows tests to mock the function without
        # triggering the import.
        librosa = None  # type: ignore[assignment]
        sf = None  # type: ignore[assignment]

    if logger is None:
        logger = LOGGER

    # Convert audio to mono 16kHz if needed
    # Note: ensure_mono_audio expects str or Path and returns Path
    audio_path_mono = ensure_mono_audio(audio_path)
    sample_rate = 16000  # We ensure this in ensure_mono_audio

    # Get audio duration
    # If librosa is available, use it; otherwise, we'll need to handle duration differently
    if librosa is not None:
        try:
            duration = librosa.get_duration(path=str(audio_path_mono), sr=sample_rate)
        except Exception as e:
            raise BackendError(f"Failed to get audio duration: {e}") from e
    else:
        # Fallback: use ffprobe if available
        # Since ensure_mono_audio already handled conversion, try to get duration
        # using a method that doesn't require librosa
        result = subprocess.run(
            [
                "ffprobe",
                "-v", "error",
                "-show_entries", "format=duration",
                "-of", "default=noprint_wrappers=1:nokey=1",
                str(audio_path_mono)
            ],
            capture_output=True,
            text=True,
            check=True,
        )
        try:
            duration = float(result.stdout.strip())
        except (subprocess.CalledProcessError, ValueError) as e:
            raise BackendError(
                "Cannot determine audio duration: librosa is not available and ffprobe failed. "
                "Install 'librosa' or ensure 'ffprobe' is available."
            ) from e

    num_chunks = int(np.ceil(duration / chunk_secs))

    # Calculate overlap in seconds from percentage
    overlap_secs = chunk_secs * (overlap_percentage / 100.0)

    # Calculate overlap ratio for fallback deduplication
    overlap_ratio = overlap_percentage / 100.0 if chunk_secs > 0 else 0.05

    logger.info(
        "Audio duration: %.2fs, processing in %d chunks of ~%.1fs each (overlap: %.1f%% = %.1fs)",
        duration, num_chunks, chunk_secs, overlap_percentage, overlap_secs
    )

    # Create manifest entries for each chunk
    # NeMo's transcribe() method can handle manifest JSON with 'offset' and 'duration' fields
    manifest_entries = []
    for chunk_idx in range(num_chunks):
        offset = chunk_idx * chunk_secs
        if chunk_idx == num_chunks - 1:
            # Last chunk: take remaining duration (no overlap for last chunk)
            chunk_duration = duration - offset
        else:
            # Regular chunk: add overlap to avoid cutting words
            chunk_duration = chunk_secs + overlap_secs

        manifest_entry = {
            "audio_filepath": str(audio_path_mono),
            "offset": offset,
            "duration": chunk_duration,
            "text": "",  # Empty for inference
        }
        manifest_entries.append(manifest_entry)

    # Extract each chunk and transcribe individually
    # Chunks may overlap if overlap_secs > 0
    text_list = []
    all_chunk_timestamps = []  # Store timestamps for each chunk
    chunk_files = []  # Track temp files for cleanup

    try:
        for chunk_idx, manifest_entry in enumerate(manifest_entries):
            offset = manifest_entry['offset']
            chunk_duration = manifest_entry['duration']

            logger.debug(
                "Processing chunk %d/%d (offset=%.1fs, dur=%.1fs)",
                chunk_idx + 1, len(manifest_entries), offset, chunk_duration
            )

            # Load the chunk from the audio file
            if librosa is None:
                raise BackendError(
                    "Cannot load audio chunks: librosa is not available. "
                    "Install 'librosa' to use the Parakeet backend."
                )
            try:
                chunk_audio, _ = librosa.load(
                    str(audio_path_mono),
                    sr=sample_rate,
                    offset=offset,
                    duration=chunk_duration,
                    mono=True
                )
            except Exception as e:
                logger.warning("Error loading chunk %d: %s", chunk_idx + 1, e)
                continue

            # Save chunk to temporary file
            chunk_temp = tempfile.NamedTemporaryFile(delete=False, suffix='.wav')
            chunk_temp_path = chunk_temp.name
            chunk_temp.close()
            chunk_files.append(chunk_temp_path)

            if sf is None:  # pragma: no cover - difficult to test in isolation
                raise BackendError(
                    "Cannot save audio chunks: soundfile is not available. "
                    "Install 'soundfile' to use the Parakeet backend."
                )
            sf.write(chunk_temp_path, chunk_audio, sample_rate, format='WAV')

            # Transcribe this chunk using model.transcribe()
            try:
                chunk_result = model.transcribe([chunk_temp_path], return_hypotheses=False, timestamps=timestamps)
                if isinstance(chunk_result, list) and len(chunk_result) > 0:
                    result = chunk_result[0]
                else:
                    result = chunk_result

                text = _extract_text_from_result(result)
                text_list.append(text.strip())

                # Extract timestamps if available
                chunk_timestamps = None
                if timestamps:
                    chunk_timestamps = _extract_timestamps_from_result(result)
                    if chunk_timestamps:
                        # Adjust timestamps to absolute time (add chunk offset)
                        adjusted_timestamps = {
                            'segment': [],
                            'word': [],
                            'char': []
                        }
                        for level in ['segment', 'word', 'char']:
                            if level in chunk_timestamps:
                                for stamp in chunk_timestamps[level]:
                                    adjusted_stamp = stamp.copy() if isinstance(stamp, dict) else dict(stamp)
                                    if 'start' in adjusted_stamp:
                                        adjusted_stamp['start'] += offset
                                    if 'end' in adjusted_stamp:
                                        adjusted_stamp['end'] += offset
                                    adjusted_timestamps[level].append(adjusted_stamp)
                        all_chunk_timestamps.append(adjusted_timestamps)

                logger.debug("Chunk %d transcribed (%d chars)", chunk_idx + 1, len(text))
            except Exception as e:
                logger.error("Error transcribing chunk %d: %s", chunk_idx + 1, e)
                # Continue with next chunk
                continue

    finally:
        # Cleanup temporary chunk files
        for chunk_file in chunk_files:
            try:
                if os.path.exists(chunk_file):
                    os.unlink(chunk_file)
            except Exception as e:
                logger.warning("Could not delete temp file %s: %s", chunk_file, e)

    # Combine all transcriptions
    # If overlap > 0, merge with deduplication like manual chunking
    # Otherwise, simply join them
    if overlap_percentage > 0 and len(text_list) > 1:
        logger.debug("Merging %d transcriptions and removing overlaps", len(text_list))
        full_text = _merge_overlapping_transcriptions(text_list, overlap_ratio)
        # Show statistics
        total_chars_before = sum(len(t) for t in text_list)
        total_chars_after = len(full_text)
        reduction = ((total_chars_before - total_chars_after) / total_chars_before * 100) if total_chars_before > 0 else 0
        logger.info(
            "Merged text: %d → %d chars (removed %.1f%% duplicates)",
            total_chars_before, total_chars_after, reduction
        )
    else:
        # No overlap, simply join
        full_text = " ".join(text_list)

    logger.info("Total transcription length: %d characters", len(full_text))

    # Combine timestamps from all chunks
    combined_timestamps = None
    if timestamps and all_chunk_timestamps:
        # Merge timestamps from all chunks (they're already adjusted to absolute time)
        combined_timestamps = {
            'segment': [],
            'word': [],
            'char': []
        }
        for chunk_ts in all_chunk_timestamps:
            for level in ['segment', 'word', 'char']:
                if level in chunk_ts and chunk_ts[level]:
                    combined_timestamps[level].extend(chunk_ts[level])
        logger.debug(
            "Combined timestamps: %d segments, %d words",
            len(combined_timestamps.get('segment', [])),
            len(combined_timestamps.get('word', []))
        )

    return {
        'text': full_text,
        'timestamps': combined_timestamps,
        'segment_timestamps': combined_timestamps['segment'] if combined_timestamps else [],
        'duration': duration,
    }


def iter_nemo_transcription_segments(
    model: Any,  # nemo_asr.models.ASRModel
    audio_path: Path,
    chunk_secs: float = 400.0,
    overlap_percentage: float = 5.0,
    timestamps: bool = True,
    logger: logging.Logger | None = None,
) -> tuple[Iterable[TranscriptionSegment], float]:
    """Yield transcription segments progressively as chunks are processed."""
    try:
        import librosa
        import soundfile as sf
    except ImportError:
        librosa = None  # type: ignore[assignment]
        sf = None  # type: ignore[assignment]

    if logger is None:
        logger = LOGGER

    audio_path_mono = ensure_mono_audio(audio_path)
    sample_rate = 16000

    if librosa is not None:
        try:
            duration = librosa.get_duration(path=str(audio_path_mono), sr=sample_rate)
        except Exception as exc:
            raise BackendError(f"Failed to get audio duration: {exc}") from exc
    else:
        result = subprocess.run(
            [
                "ffprobe",
                "-v", "error",
                "-show_entries", "format=duration",
                "-of", "default=noprint_wrappers=1:nokey=1",
                str(audio_path_mono)
            ],
            capture_output=True,
            text=True,
            check=True,
        )
        try:
            duration = float(result.stdout.strip())
        except (subprocess.CalledProcessError, ValueError) as exc:
            raise BackendError(
                "Cannot determine audio duration: librosa is not available and ffprobe failed. "
                "Install 'librosa' or ensure 'ffprobe' is available."
            ) from exc

    num_chunks = int(np.ceil(duration / chunk_secs))
    overlap_secs = chunk_secs * (overlap_percentage / 100.0)

    logger.info(
        "Audio duration: %.2fs, processing in %d chunks of ~%.1fs each (overlap: %.1f%% = %.1fs)",
        duration, num_chunks, chunk_secs, overlap_percentage, overlap_secs
    )

    def segment_iter() -> Iterable[TranscriptionSegment]:
        chunk_files: list[str] = []
        try:
            for chunk_idx in range(num_chunks):
                offset = chunk_idx * chunk_secs
                if chunk_idx == num_chunks - 1:
                    chunk_duration = duration - offset
                else:
                    chunk_duration = chunk_secs + overlap_secs

                if librosa is None:
                    raise BackendError(
                        "Cannot load audio chunks: librosa is not available. "
                        "Install 'librosa' to use the Parakeet backend."
                    )
                try:
                    chunk_audio, _ = librosa.load(
                        str(audio_path_mono),
                        sr=sample_rate,
                        offset=offset,
                        duration=chunk_duration,
                        mono=True,
                    )
                except Exception as exc:
                    logger.warning("Error loading chunk %d: %s", chunk_idx + 1, exc)
                    continue

                chunk_temp = tempfile.NamedTemporaryFile(delete=False, suffix=".wav")
                chunk_temp_path = chunk_temp.name
                chunk_temp.close()
                chunk_files.append(chunk_temp_path)

                if sf is None:  # pragma: no cover
                    raise BackendError(
                        "Cannot save audio chunks: soundfile is not available. "
                        "Install 'soundfile' to use the Parakeet backend."
                    )
                sf.write(chunk_temp_path, chunk_audio, sample_rate, format="WAV")

                try:
                    chunk_result = model.transcribe(
                        [chunk_temp_path],
                        return_hypotheses=False,
                        timestamps=timestamps,
                    )
                    if isinstance(chunk_result, list) and len(chunk_result) > 0:
                        result = chunk_result[0]
                    else:
                        result = chunk_result

                    text = _extract_text_from_result(result).strip()
                    chunk_timestamps = _extract_timestamps_from_result(result) if timestamps else None
                    if chunk_timestamps and "segment" in chunk_timestamps:
                        for stamp in chunk_timestamps["segment"]:
                            adjusted = stamp.copy() if isinstance(stamp, dict) else dict(stamp)
                            if "start" in adjusted:
                                adjusted["start"] += offset
                            if "end" in adjusted:
                                adjusted["end"] += offset
                            if chunk_idx > 0 and adjusted.get("end", 0.0) <= offset + overlap_secs:
                                continue
                            segment_text = str(adjusted.get("segment", "")).strip()
                            if not segment_text:
                                continue
                            yield TranscriptionSegment(
                                start=float(adjusted.get("start", 0.0)),
                                end=float(adjusted.get("end", 0.0)),
                                text=segment_text,
                            )
                    elif text:
                        yield TranscriptionSegment(
                            start=float(offset),
                            end=float(offset + chunk_duration),
                            text=text,
                        )
                except Exception as exc:
                    logger.error("Error transcribing chunk %d: %s", chunk_idx + 1, exc)
                    continue
        finally:
            for chunk_file in chunk_files:
                try:
                    if os.path.exists(chunk_file):
                        os.unlink(chunk_file)
                except Exception as exc:
                    logger.warning("Could not delete temp file %s: %s", chunk_file, exc)

    return segment_iter(), duration







