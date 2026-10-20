"""Literal observations only. Neither keywords nor model picks establish proficiency."""
import re
from math import isfinite

from pydantic import ValidationError

from jobtology_be.application.specs import SkillItem
from jobtology_be.resume.models import (
    MAX_SUGGESTIONS,
    AiPick,
    ResumeBlock,
    ResumeInterpretResponse,
    ResumeSuggestion,
)


def _pattern(alias: str) -> re.Pattern | None:
    alias = alias.strip()
    if (len(alias) < 3 and alias not in {'C#', 'C++'}) or alias.isdigit() or ':' in alias:
        return None
    escaped = re.escape(alias).replace(r'\ ', r'\s+')
    # ASCII boundaries keep Java separate from JavaScript, C++ separate from C.
    left = r'(?<![\w+#])' if alias[0].isascii() else ''
    right = r'(?![A-Za-z0-9_+#])' if alias[-1].isascii() else ''
    return re.compile(left + escaped + right, re.IGNORECASE)


def literal_suggestions(blocks: tuple[ResumeBlock, ...], items: tuple[SkillItem, ...]):
    patterns = [(item, tuple(pattern for alias in {item.label, item.original_label, *item.aliases}
                             if (pattern := _pattern(alias)))) for item in items]
    found = []
    section = 'skill'
    for block in blocks:
        heading = block.text.strip().casefold()
        headings = {
            '프로젝트': 'project', 'projects': 'project', 'project': 'project',
            '경력': 'experience', '업무 경험': 'experience', 'experience': 'experience',
            '자격증': 'certification', 'certifications': 'certification', 'certification': 'certification',
            '시험': 'test', '어학': 'test', 'tests': 'test',
            '기술': 'skill', 'skills': 'skill', '기술·역량': 'skill',
        }
        if heading in headings:
            section = headings[heading]
            continue
        if heading in {'자격증 · 시험', '자격증/시험', 'certifications & tests'}:
            section = 'skill'
            continue
        competencies = tuple(item for item, patterns_for_item in patterns
                             if any(pattern.search(block.text) for pattern in patterns_for_item))[:12]
        score = re.search(r'\b(TOEIC|TOEFL|IELTS)\s*[:：]?\s*(\d+(?:\.\d+)?)(?:\s*/\s*(\d+(?:\.\d+)?))?', block.text, re.IGNORECASE)
        category = ('test' if score else 'certification' if re.search(r'자격증|정보처리(?:산업)?기사|certificat', block.text, re.IGNORECASE)
                    else 'project' if re.search(r'프로젝트|\bproject\b', block.text, re.IGNORECASE)
                    else 'experience' if re.search(r'근무|인턴|경력|\b(?:internship|employment)\b', block.text, re.IGNORECASE)
                    else section)
        if not competencies and category not in {'test', 'certification'}:
            continue
        # Keep explicit scores only; never invent dates, issuers or equivalence.
        result = float(score[2]) if score else None
        maximum = float(score[3]) if score and score[3] else None
        if (result is not None and not isfinite(result)
                or maximum is not None and not isfinite(maximum)):
            result = maximum = None
        if maximum is not None and (maximum <= 0 or result > maximum):
            result = maximum = None
        found.append(ResumeSuggestion(
            suggestion_id=f'keyword-{block.block_id}', category=category,
            title=(score[1].upper() if score else competencies[0].label if competencies
                   else block.text[:100].strip()),
            competencies=competencies, block_id=block.block_id, quote=block.text,
            start=0, end=len(block.text), method='KEYWORD', score=result, score_max=maximum,
        ))
        if len(found) > MAX_SUGGESTIONS:
            break
    return tuple(found[:MAX_SUGGESTIONS]), len(found) > MAX_SUGGESTIONS


def validated_ai_picks(raw: object, text: str, items: tuple[SkillItem, ...]):
    by_id = {item.entity_id: item for item in items}
    found = []
    rejected = 0
    seen = set()
    if not isinstance(raw, list):
        return ResumeInterpretResponse(suggestions=(), rejected_count=1)
    for value in raw[:20]:
        try:
            pick = AiPick.model_validate(value)
        except ValidationError:
            rejected += 1
            continue
        identity = (pick.start, pick.end, tuple(sorted(pick.competency_ids)))
        if (text[pick.start:pick.end] != pick.quote
                or any(key not in by_id for key in pick.competency_ids)
                or len(set(pick.competency_ids)) != len(pick.competency_ids)):
            rejected += 1
            continue
        if identity in seen:
            continue
        seen.add(identity)
        found.append(ResumeSuggestion(
            suggestion_id=f'ai-{len(found)}', title=pick.title, category=pick.category,
            competencies=tuple(by_id[key] for key in pick.competency_ids), block_id='selected',
            quote=pick.quote, start=pick.start, end=pick.end, method='AI_ASSISTED',
        ))
    return ResumeInterpretResponse(suggestions=tuple(found[:5]), rejected_count=rejected)
