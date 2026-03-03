"""
clients/groq_client.py — Groq API wrapper (Qwen3-32B)
"""

import logging
from typing import Any

from groq import AsyncGroq
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

from config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()


class GroqClient:
    """Async wrapper around the Groq SDK for Qwen3-32B interactions."""

    def __init__(self):
        self._client = AsyncGroq(api_key=settings.groq_api_key)
        self.model = settings.groq_model

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=30),
        retry=retry_if_exception_type(Exception),
        reraise=True,
    )
    async def chat(
        self,
        system_prompt: str,
        user_prompt: str,
        temperature: float = 0.7,
        max_tokens: int = 8192,
    ) -> str:
        """Single-turn chat completion. Returns the assistant message content."""
        logger.debug("Groq request: model=%s | tokens=%d", self.model, max_tokens)
        response = await self._client.chat.completions.create(
            model=self.model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user",   "content": user_prompt},
            ],
            temperature=temperature,
            max_tokens=max_tokens,
        )
        content = response.choices[0].message.content or ""
        logger.debug("Groq response: %d chars", len(content))
        return content

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=30),
        retry=retry_if_exception_type(Exception),
        reraise=True,
    )
    async def complete(
        self,
        prompt: str,
        temperature: float = 0.3,
        max_tokens: int = 4096,
    ) -> str:
        """Single-prompt (no system msg) completion."""
        response = await self._client.chat.completions.create(
            model=self.model,
            messages=[{"role": "user", "content": prompt}],
            temperature=temperature,
            max_tokens=max_tokens,
        )
        return response.choices[0].message.content or ""

    async def scout_context(self, topic: str, niche: str) -> str:
        """
        Scout Node prompt: gather background, key points, and SEO keywords
        for a given topic/niche using Qwen3-32B reasoning.
        """
        system = (
            "You are an elite technical research assistant with deep expertise across "
            "software engineering, AI/ML, DevOps, and emerging technologies. "
            "Provide comprehensive, factually accurate background research."
        )
        user = (
            f"Research the topic: **{topic}** for the niche: **{niche}**.\n\n"
            "Provide:\n"
            "1. A 3-paragraph technical overview\n"
            "2. 5 key concepts or subtopics to cover\n"
            "3. Current trends and real-world use cases (2024-2025)\n"
            "4. 10 high-value SEO keywords (long-tail preferred)\n"
            "5. 3 potential code examples or technical demonstrations\n\n"
            "Be specific, technical, and current. Avoid generic descriptions."
        )
        return await self.chat(system, user, temperature=0.5, max_tokens=4096)

    async def write_blog(self, topic: str, niche: str, context: str) -> dict[str, Any]:
        """
        Writer Node prompt: produce a full markdown blog post.
        Returns dict with 'title', 'content', 'tags', 'teaser'.
        """
        system = (
            "You are a world-class technical blogger and SEO expert. "
            "Write deeply technical, engaging content that ranks on Google. "
            "ALWAYS return valid JSON only — no markdown fences, no extra text."
        )
        user = (
            f"Write a comprehensive blog post about: **{topic}** for niche: **{niche}**.\n\n"
            f"Research context:\n{context}\n\n"
            "Requirements:\n"
            "- 1500-2500 words in pure markdown\n"
            "- SEO-optimized H2/H3 structure\n"
            "- At least 2 practical code examples with triple backtick fences\n"
            "- Hook in the first paragraph\n"
            "- Conclusion with CTA\n\n"
            "Return ONLY this JSON structure (no markdown fences):\n"
            '{"title": "...", "content": "...full markdown...", '
            '"tags": ["tag1","tag2","tag3","tag4","tag5"], '
            '"teaser": "...1-2 sentence summary..."}'
        )
        raw = await self.chat(system, user, temperature=0.75, max_tokens=8192)
        import json, re

        # Strip any accidental markdown fences around the JSON itself
        raw = re.sub(r"^```(?:json)?\n?", "", raw.strip())
        raw = re.sub(r"\n?```$", "", raw.strip())
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            # Fallback: extract JSON object
            match = re.search(r"\{.*\}", raw, re.DOTALL)
            if match:
                data = json.loads(match.group())
            else:
                raise ValueError(f"Could not parse Groq writer response as JSON:\n{raw[:500]}")

        # Strip ```markdown / ```md / ``` wrapper that LLMs sometimes put
        # inside the "content" field value (the actual markdown text)
        content = data.get("content", "")
        if isinstance(content, str):
            content = content.strip()
            for fence in ("```markdown", "```md", "```"):
                if content.startswith(fence) and content.endswith("```"):
                    inner = content[len(fence):].lstrip("\n")
                    if inner.endswith("```"):
                        inner = inner[:-3].rstrip()
                        if inner.strip():
                            content = inner
                            break
            data["content"] = content

        # Strip markdown syntax from teaser for clean plain-text display
        teaser = data.get("teaser", "")
        if isinstance(teaser, str):
            teaser = re.sub(r"(\*{1,3}|_{1,3}|`{1,3}|~~|>\s?)", "", teaser).strip()
            data["teaser"] = teaser

        return data

    async def diagnose_post(self, title: str, teaser: str, tags: list[str], views: int, seo_score: float) -> dict[str, Any]:
        """Diagnostic Node: identify why a post is underperforming."""
        system = (
            "You are an expert content strategist and SEO analyst. "
            "Diagnose why a blog post is underperforming and provide actionable fixes. "
            "Return ONLY valid JSON, no markdown fences."
        )
        tags_str = ", ".join(tags) if tags else "None"
        user = (
            f"This blog post is underperforming:\n"
            f"Title: {title}\n"
            f"Teaser: {teaser}\n"
            f"Tags: {tags_str}\n"
            f"Views: {views} | SEO Score: {seo_score}/100\n\n"
            "Diagnose the issue and return JSON:\n"
            '{"diagnosis": "...", "improved_title": "...", '
            '"improved_teaser": "...", "improved_tags": ["t1","t2","t3","t4","t5"], '
            '"fixes": ["fix1", "fix2", "fix3"]}'
        )
        raw = await self.chat(system, user, temperature=0.4, max_tokens=2048)
        import json, re
        raw = re.sub(r"^```(?:json)?\n?", "", raw.strip())
        raw = re.sub(r"\n?```$", "", raw.strip())
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            match = re.search(r"\{.*\}", raw, re.DOTALL)
            if match:
                return json.loads(match.group())
            raise

    async def rewrite_post(self, title: str, content: str, diagnosis: str, fixes: list[str]) -> dict[str, Any]:
        """Optimizer Node: rewrite underperforming content based on diagnosis."""
        system = (
            "You are an expert technical writer and SEO specialist. "
            "Rewrite the provided blog post to fix its identified issues. "
            "Return ONLY valid JSON, no markdown fences."
        )
        fixes_str = "\n".join(f"- {f}" for f in fixes)
        user = (
            f"Rewrite this underperforming blog post:\n\nTitle: {title}\n\n"
            f"DIAGNOSIS:\n{diagnosis}\n\nFIXES TO APPLY:\n{fixes_str}\n\n"
            f"ORIGINAL CONTENT:\n{content[:3000]}...\n\n"
            "Return JSON:\n"
            '{"title": "...", "content": "...full rewritten markdown...", '
            '"tags": ["t1","t2","t3","t4","t5"], "teaser": "..."}'
        )
        raw = await self.chat(system, user, temperature=0.6, max_tokens=8192)
        import json, re
        raw = re.sub(r"^```(?:json)?\n?", "", raw.strip())
        raw = re.sub(r"\n?```$", "", raw.strip())
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            match = re.search(r"\{.*\}", raw, re.DOTALL)
            if match:
                return json.loads(match.group())
            raise

    async def verify_facts(self, content: str, context: str) -> dict[str, Any]:
        """Revisor Node: check for hallucinations against researched context."""
        system = (
            "You are a rigorous fact-checker for technical content. "
            "Identify factual inaccuracies, unsubstantiated claims, and hallucinations. "
            "Return ONLY valid JSON, no markdown fences."
        )
        user = (
            f"Verify this blog content against the research context:\n\n"
            f"RESEARCH CONTEXT:\n{context[:2000]}\n\n"
            f"BLOG CONTENT (first 3000 chars):\n{content[:3000]}\n\n"
            "Return JSON:\n"
            '{"hallucination_detected": true|false, '
            '"issues": ["issue1", "issue2"], '
            '"revision_notes": "...", '
            '"confidence_score": 0-100}'
        )
        raw = await self.chat(system, user, temperature=0.2, max_tokens=1024)
        import json, re
        raw = re.sub(r"^```(?:json)?\n?", "", raw.strip())
        raw = re.sub(r"\n?```$", "", raw.strip())
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return {"hallucination_detected": False, "issues": [], "revision_notes": "", "confidence_score": 80}
