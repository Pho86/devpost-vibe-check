from __future__ import annotations

import json
import os
import re
import time
from typing import Any

from .config import RunConfig
from .models import VibeResult, github_repo_url

DEFAULT_GEMINI_MODEL = "gemini-2.5-flash"

SYSTEM = """You are assisting hackathon organizers reviewing Devpost submissions for
suspicious git history (pre-built work, wrong repo, dumped code, AI-generated
commit spam, unrelated reused repos).

You are NOT the final DQ decision — organizers review your note.
Be concise and skeptical. Prefer false positives over missing cheaters.
"""


def _extract_json(text: str) -> dict[str, Any]:
    raw = (text or "").strip()
    if not raw:
        return {}
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        m = re.search(r"\{[\s\S]*\}", raw)
        if not m:
            return {}
        try:
            return json.loads(m.group(0))
        except json.JSONDecodeError:
            return {}


def _prompt_for(result: VibeResult, config: RunConfig) -> str:
    return f"""Hackathon window (UTC): {config.start.isoformat()} → {config.end.isoformat()}
Hackathon: {config.hackathon_url}

Project: {result.project_title}
Devpost: {result.project_url or '-'}
Primary repo: {github_repo_url(result.primary_repo) or '-'}
All repos: {' | '.join(github_repo_url(r) for r in result.repos) or '-'}

Heuristic score: {result.suspicion_score}
Flags: {', '.join(result.flags) or '-'}
Reasons: {' | '.join(result.reasons) or '-'}
Commits: total={result.total_commits} in_window={result.in_window_commits} pre_start={result.pre_start_commits} post_end={result.post_end_commits}
Repo created_at: {result.created_at or '-'} (before_start={result.created_before_start})
First meaningful commit: {result.first_meaningful_commit_at or '-'}
Sample commit messages: {result.sample_messages or '-'}
Relevance hits: {', '.join(result.relevance_hits) or '-'} ({', '.join(result.relevance_sources) or 'none'})
Repo details: {result.repo_details or '-'}

Return JSON with:
- verdict: one of "suspicious", "likely_ok", "unclear"
- confidence: 0-100 integer
- summary: one short sentence for organizers
- signals: array of short bullet strings (max 4)
"""


class GeminiScanner:
    def __init__(
        self,
        *,
        api_key: str | None = None,
        model: str = DEFAULT_GEMINI_MODEL,
    ) -> None:
        self.api_key = (api_key or os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY") or "").strip()
        self.model = model or DEFAULT_GEMINI_MODEL
        self._client = None

    def available(self) -> tuple[bool, str]:
        if not self.api_key:
            return False, "GEMINI_API_KEY (or GOOGLE_API_KEY) not set"
        try:
            from google import genai  # noqa: F401
        except ImportError:
            return False, "google-genai not installed (pip install google-genai)"
        return True, "ok"

    def _client_or_raise(self):
        if self._client is None:
            from google import genai

            self._client = genai.Client(api_key=self.api_key)
        return self._client

    def scan_one(self, result: VibeResult, config: RunConfig) -> VibeResult:
        ok, why = self.available()
        if not ok:
            result.gemini_verdict = "skipped"
            result.gemini_summary = why
            return result

        try:
            from google.genai import types

            client = self._client_or_raise()
            resp = client.models.generate_content(
                model=self.model,
                contents=_prompt_for(result, config),
                config=types.GenerateContentConfig(
                    system_instruction=SYSTEM,
                    temperature=0.2,
                    response_mime_type="application/json",
                ),
            )
            data = _extract_json(getattr(resp, "text", "") or "")
        except Exception as exc:
            result.gemini_verdict = "error"
            result.gemini_summary = f"gemini error: {exc}"[:240]
            return result

        verdict = str(data.get("verdict") or "unclear").strip().lower()
        if verdict not in {"suspicious", "likely_ok", "unclear"}:
            verdict = "unclear"
        try:
            conf = int(data.get("confidence") or 0)
        except (TypeError, ValueError):
            conf = 0
        conf = max(0, min(100, conf))
        summary = str(data.get("summary") or "").strip()[:400]
        signals = data.get("signals") or []
        if isinstance(signals, list):
            signal_txt = "; ".join(str(s).strip() for s in signals[:4] if s)
        else:
            signal_txt = str(signals)[:240]
        if signal_txt and summary:
            summary = f"{summary} | {signal_txt}"[:500]

        result.gemini_verdict = verdict
        result.gemini_confidence = conf
        result.gemini_summary = summary or f"verdict={verdict}"

        if verdict == "suspicious" and conf >= 50:
            if "GEMINI_SUSPICIOUS" not in result.flags:
                result.flags.append("GEMINI_SUSPICIOUS")
            result.reasons.append(f"Gemini: {result.gemini_summary}")
            # Nudge into / keep in marked bucket without erasing heuristic score.
            bump = 15 + min(conf, 100) // 5
            result.suspicion_score = max(result.suspicion_score, config.min_score)
            result.suspicion_score = min(200, result.suspicion_score + bump)
        elif verdict == "likely_ok" and conf >= 70:
            if "GEMINI_OK" not in result.flags:
                result.flags.append("GEMINI_OK")
            # Informational only — never undo PRE_START_DQ / hard heuristics.
            if "PRE_START_DQ" not in result.flags and "PRE_START_COMMITS" not in result.flags:
                result.reasons.append(f"Gemini likely_ok ({conf}): {result.gemini_summary}")

        return result

    def scan_many(
        self,
        results: list[VibeResult],
        config: RunConfig,
        *,
        marked_only: bool = True,
        delay_s: float = 0.15,
    ) -> int:
        """Scan results in place. Returns number scanned."""
        ok, why = self.available()
        if not ok:
            print(f"[gemini] skip — {why}", flush=True)
            return 0

        targets = [
            r
            for r in results
            if (not marked_only) or r.suspicion_score >= config.min_score or "PRE_START_DQ" in r.flags
        ]
        # Also include near-misses when marked_only (score within 10 of threshold)
        if marked_only:
            seen = {id(r) for r in targets}
            for r in results:
                if id(r) in seen:
                    continue
                if r.suspicion_score >= max(0, config.min_score - 10) and r.primary_repo:
                    targets.append(r)

        print(
            f"[gemini] scanning {len(targets)} project(s) with {self.model}...",
            flush=True,
        )
        n = 0
        for i, r in enumerate(targets, 1):
            self.scan_one(r, config)
            n += 1
            print(
                f"  [{i}/{len(targets)}] {r.project_title}: "
                f"{r.gemini_verdict} ({r.gemini_confidence}) "
                f"score→{r.suspicion_score}",
                flush=True,
            )
            if delay_s:
                time.sleep(delay_s)
        print(f"[gemini] done — scanned {n}", flush=True)
        return n
