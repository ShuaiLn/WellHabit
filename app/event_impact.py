from __future__ import annotations

import re

EVENT_IMPACT_LABELS = {
    'hydration': 'Hydration',
    'energy': 'Energy',
    'fitness': 'Fitness',
    'focus': 'Focus',
    'mood': 'Mood',
    'overall': 'Overall Wellness',
}

ZERO_IMPACT_TITLES = {'task added', 'task edited', 'task deleted', 'ai suggestion added'}
HYDRATION_WORDS = ['drink', 'water', 'hydration', 'milk', 'coke', 'beverage']
MEAL_WORDS = ['breakfast', 'lunch', 'dinner', 'meal']
FITNESS_WORDS = ['steps', 'exercise', 'walk', 'run', 'stretch', 'yoga']
FOCUS_WORDS = ['pomodoro', 'focus', 'study', 'work session']
POSITIVE_CARE_WORDS = ['explicitly said they felt a little better', 'user reported feeling happy', 'user reported feeling calm', 'user reported feeling hopeful']
NEGATIVE_CARE_WORDS = [
    'no explicit improvement reported',
    'still distressed',
    'still anxious',
    'still overwhelmed',
    'user reported feeling anxious',
    'user reported feeling stressed',
    'user reported feeling overwhelmed',
    'user reported feeling sad',
    'panicked',
    'unsafe',
    'hopeless',
    'spiraling',
]
LOW_ENERGY_WORDS = ['exhausted', 'drained', 'worn out', 'burned out', 'user reported feeling exhausted']
NEGATIVE_MOOD_WORDS = ['tired', 'anxious', 'sad', 'stress', 'stressed', 'burned out', 'overwhelmed']


def infer_event_impacts(title: str | None = None, description: str | None = None) -> dict[str, int]:
    title_text = (title or '').lower()
    desc_text = (description or '').lower()
    text = ' '.join(part for part in [title_text, desc_text] if part)

    impacts = {
        'hydration': 0,
        'energy': 0,
        'fitness': 0,
        'focus': 0,
        'mood': 0,
        'overall': 0,
    }

    if title_text in ZERO_IMPACT_TITLES:
        return impacts

    is_meal = any(word in text for word in MEAL_WORDS)
    is_hydration = any(word in text for word in HYDRATION_WORDS)
    is_task_completion = 'completed todo' in text or 'completed meal' in text or 'meal finished' in title_text
    is_care_chat = 'care chat' in text or 'supportive chat' in text or 'support chat' in text

    if is_hydration:
        if any(word in text for word in ['skip', 'dismiss', 'not yet', 'not_yet', 'postponed']):
            impacts['hydration'] -= 3
        else:
            amount_match = re.search(r'(\d+)\s*ml', text)
            amount = int(amount_match.group(1)) if amount_match else 250
            impacts['hydration'] += max(2, min(8, int(round(amount / 120))))

    if 'sleep' in text:
        impacts['energy'] += 4
    if any(word in text for word in FITNESS_WORDS):
        impacts['fitness'] += 4
    if is_meal and 'skipped' not in text:
        impacts['energy'] += 4
        impacts['fitness'] += 4
    elif any(word in text for word in FOCUS_WORDS) or (is_task_completion and not is_hydration):
        impacts['focus'] += 5
    if not is_care_chat and any(word in text for word in ['journal', 'mood', 'stress', 'feeling', 'felt']):
        impacts['mood'] += 3
    if not is_care_chat and any(word in text for word in NEGATIVE_MOOD_WORDS):
        impacts['mood'] -= 4

    if is_care_chat:
        has_explicit_improvement = any(word in text for word in POSITIVE_CARE_WORDS)
        has_negative_state = any(word in text for word in NEGATIVE_CARE_WORDS)
        if has_negative_state and has_explicit_improvement:
            impacts['mood'] += 2
            impacts['focus'] += 1
        elif has_negative_state:
            impacts['mood'] -= 6
        elif has_explicit_improvement:
            impacts['mood'] += 3
            impacts['focus'] += 1
        if any(word in text for word in LOW_ENERGY_WORDS):
            impacts['energy'] -= 2

    impacts['overall'] = int(round((impacts['hydration'] + impacts['energy'] + impacts['fitness'] + impacts['focus'] + impacts['mood']) / 3))
    return impacts


def history_payload_from_impacts(impacts: dict[str, int], collapse_zero: bool = True) -> list[dict[str, int | str]]:
    items = [
        {
            'key': key,
            'label': EVENT_IMPACT_LABELS[key],
            'value': int(impacts.get(key, 0) or 0),
            'signed': f"{int(impacts.get(key, 0) or 0):+d}",
        }
        for key in ['hydration', 'energy', 'fitness', 'focus', 'mood', 'overall']
    ]
    if collapse_zero and items and all(int(item.get('value', 0) or 0) == 0 for item in items):
        return [
            {
                'key': 'overall',
                'label': EVENT_IMPACT_LABELS['overall'],
                'value': 0,
                'signed': '+0',
            }
        ]
    return items


def ai_score_bumps_from_impacts(impacts: dict[str, int]) -> dict[str, int]:
    return {
        'hydration_score': int(impacts.get('hydration', 0) or 0),
        'energy_score': int(impacts.get('energy', 0) or 0),
        'fitness_score': int(impacts.get('fitness', 0) or 0),
        'focus_score': int(impacts.get('focus', 0) or 0),
        'mood_score': int(impacts.get('mood', 0) or 0),
    }
