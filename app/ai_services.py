from __future__ import annotations

import json
import os
import re
from typing import Any

MEAL_KEYWORDS = {
    'eat', 'eating', 'ate', 'dinner', 'lunch', 'breakfast', 'brunch', 'snack', 'meal',
    'rice', 'noodles', 'pizza', 'burger', 'sandwich', 'salad', 'soup', 'pasta', 'cake',
    'dumplings', 'fries', 'chicken', 'fish', 'beef', 'pork', 'fruit', 'dessert',
    '喝汤', '吃饭', '吃了', '早餐', '午饭', '午餐', '晚饭', '晚餐', '零食', '饭', '面', '米饭', '汉堡', '披萨'
}

NEGATIVE_HINTS = {'study', 'homework', 'walk', 'sleep', 'shower', 'water', 'milk', 'cola', 'coke'}

WORD_NUMBER_MAP = {
    'a': 1,
    'an': 1,
    'one': 1,
    'two': 2,
    'three': 3,
    'four': 4,
    'five': 5,
    'six': 6,
    'seven': 7,
    'eight': 8,
    'nine': 9,
    'ten': 10,
}

BASE_UNIT_ML = {
    'glass': 250,
    'glasses': 250,
    'cup': 240,
    'cups': 240,
    'bottle': 500,
    'bottles': 500,
    'can': 330,
    'cans': 330,
}



def _get_openai_client():
    api_key = os.getenv('OPENAI_API_KEY')
    if not api_key:
        return None

    try:
        from openai import OpenAI

        return OpenAI(api_key=api_key)
    except Exception:
        return None



def _keyword_detect(text: str) -> dict[str, Any]:
    lowered = text.lower()
    matched = sorted({word for word in MEAL_KEYWORDS if word in lowered or word in text})
    if matched:
        return {
            'ate_meal': True,
            'confidence': 'medium',
            'reason': f"Meal-like words found: {', '.join(matched[:6])}",
        }

    negative_hit = any(word in lowered for word in NEGATIVE_HINTS)
    return {
        'ate_meal': False,
        'confidence': 'low' if not negative_hit else 'medium',
        'reason': 'No meal-related cues found in the text.',
    }



def analyze_meal_text(text: str) -> dict[str, Any]:
    cleaned = (text or '').strip()
    if not cleaned:
        return {
            'ate_meal': False,
            'confidence': 'low',
            'reason': 'No activity text was provided.',
        }

    client = _get_openai_client()
    if not client:
        return _keyword_detect(cleaned)

    try:
        model_name = os.getenv('OPENAI_MODEL', 'gpt-5.4')
        response = client.responses.create(
            model=model_name,
            input=(
                'You are classifying a short wellness journal entry. '
                'Return ONLY valid JSON with keys ate_meal (boolean), confidence (string), and reason (string). '
                'Set ate_meal to true only if the user likely ate a meal or snack. '
                f'User text: {cleaned}'
            ),
        )
        raw_text = (response.output_text or '').strip()
        match = re.search(r'\{.*\}', raw_text, re.DOTALL)
        payload = json.loads(match.group(0) if match else raw_text)

        return {
            'ate_meal': bool(payload.get('ate_meal')),
            'confidence': str(payload.get('confidence') or 'medium'),
            'reason': str(payload.get('reason') or 'AI analysis completed.'),
        }
    except Exception:
        return _keyword_detect(cleaned)



def convert_drink_amount_to_ml(beverage: str, amount_text: str) -> dict[str, Any]:
    cleaned_amount = re.sub(r'\s+', ' ', (amount_text or '').strip().lower())
    cleaned_beverage = (beverage or 'water').strip().lower() or 'water'

    if not cleaned_amount:
        return {
            'amount_ml': 250,
            'source': 'default',
            'reason': 'No amount provided. Defaulted to 250 ml.',
        }

    direct_ml = re.search(r'(\d+(?:\.\d+)?)\s*ml\b', cleaned_amount)
    if direct_ml:
        return {
            'amount_ml': max(0, int(float(direct_ml.group(1)))),
            'source': 'direct_ml',
            'reason': 'Used the numeric ml value directly.',
        }

    normalized = cleaned_amount
    for word, number in WORD_NUMBER_MAP.items():
        normalized = re.sub(rf'\b{word}\b', str(number), normalized)

    unit_match = re.search(r'(\d+(?:\.\d+)?)?\s*(glass|glasses|cup|cups|bottle|bottles|can|cans)\b', normalized)
    if unit_match:
        count = float(unit_match.group(1) or 1)
        unit = unit_match.group(2)
        return {
            'amount_ml': max(0, int(count * BASE_UNIT_ML[unit])),
            'source': 'heuristic_unit',
            'reason': f'Converted {count:g} {unit} into ml.',
        }

    plain_number = re.fullmatch(r'(\d+(?:\.\d+)?)', normalized)
    if plain_number:
        return {
            'amount_ml': max(0, int(float(plain_number.group(1)))),
            'source': 'plain_number',
            'reason': 'Used the numeric value as ml.',
        }

    client = _get_openai_client()
    if client:
        try:
            model_name = os.getenv('OPENAI_MODEL', 'gpt-5.4')
            response = client.responses.create(
                model=model_name,
                input=(
                    'Convert the following drink amount into milliliters. '
                    'Return ONLY valid JSON with keys amount_ml (integer) and reason (string). '
                    'Use practical everyday estimates. '
                    'Useful defaults: glass 250 ml, cup 240 ml, bottle 500 ml, can 330 ml. '
                    f'Beverage: {cleaned_beverage}. '
                    f'Amount text: {cleaned_amount}.'
                ),
            )
            raw_text = (response.output_text or '').strip()
            match = re.search(r'\{.*\}', raw_text, re.DOTALL)
            payload = json.loads(match.group(0) if match else raw_text)
            amount_ml = max(0, int(float(payload.get('amount_ml') or 250)))
            return {
                'amount_ml': amount_ml,
                'source': 'ai',
                'reason': str(payload.get('reason') or 'AI converted the amount to ml.'),
            }
        except Exception:
            pass

    return {
        'amount_ml': 250,
        'source': 'fallback_default',
        'reason': 'Could not confidently parse the amount. Defaulted to 250 ml.',
    }
