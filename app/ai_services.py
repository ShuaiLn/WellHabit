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
    'mug': 320,
    'mugs': 320,
    'small bottle': 330,
    'small bottles': 330,
    'bottle': 500,
    'bottles': 500,
    'large bottle': 750,
    'large bottles': 750,
    'thermos': 750,
    'thermoses': 750,
    'can': 330,
    'cans': 330,
}

POSITIVE_WORDS = {
    'good', 'great', 'happy', 'calm', 'productive', 'relaxed', 'proud', 'better', 'nice', 'fun',
    '开心', '轻松', '放松', '高兴', '不错', '满意', '有成就感'
}
NEGATIVE_WORDS = {
    'sad', 'bad', 'tired', 'upset', 'angry', 'frustrated', 'burned out', 'anxious', 'depressed',
    '压力', '焦虑', '难过', '累', '烦', '崩溃', '生气', '糟糕'
}
STRESS_WORDS = {
    'stress', 'stressed', 'panic', 'deadline', 'overwhelmed', 'anxiety', 'worry',
    '压力', '焦虑', '担心', '紧张', '崩溃'
}
CARE_HIGH_DISTRESS_WORDS = {
    'panic attack', "can't breathe", 'cannot breathe', 'breaking down', 'spiraling', 'unsafe',
    'hopeless', 'falling apart', 'losing control', '崩溃', '喘不过气', '不安全', '绝望'
}
CARE_LOW_ENERGY_WORDS = {
    'tired', 'exhausted', 'drained', 'burned out', 'sleepy', 'worn out', '累', '疲劳', '没力气'
}
CARE_POSITIVE_WORDS = {
    'happy', 'relieved', 'better', 'calmer', 'hopeful', 'grateful', 'lighter', 'proud',
    '开心', '放心', '好多了', '平静', '有希望'
}
CARE_GROUNDING_WORDS = {
    'breathe', 'breathing', 'grounded', 'grounding', 'pause', 'slow down', 'walk', 'water',
    '呼吸', '冷静', '停一下', '喝水'
}
STRETCH_WORDS = {
    'stretch', 'stretching', 'yoga', 'mobility', '拉伸', '瑜伽'
}

QUALITY_SCORES = {
    'poor': 35,
    'average': 60,
    'good': 80,
    'excellent': 95,
}

MOOD_VALUE_MAP = {
    'happy': 84,
    'normal': 56,
    'sad': 24,
    'anxious': 28,
    'exhausted': 34,
    'stressed': 30,
    'calm': 72,
    'overwhelmed': 22,
    'hopeful': 74,
    'mixed': 50,
}

MOOD_DISPLAY_MAP = {
    'happy': 'Happy',
    'normal': 'Normal',
    'sad': 'Sad',
    'anxious': 'Anxious',
    'exhausted': 'Exhausted',
    'stressed': 'Stressed',
    'calm': 'Calm',
    'overwhelmed': 'Overwhelmed',
    'hopeful': 'Hopeful',
    'mixed': 'Mixed',
    'custom': 'Custom',
}




def _normalize_mood_key(value: str | None) -> str:
    raw = (value or '').strip().lower()
    aliases = {
        '开心': 'happy',
        '普通': 'normal',
        '伤心': 'sad',
        '焦虑': 'anxious',
        '疲劳': 'exhausted',
        '累': 'exhausted',
        '平静': 'calm',
        '希望': 'hopeful',
        'custom': 'custom',
        'other': 'custom',
    }
    return aliases.get(raw, raw)


def mood_display_label(mood_label: str | None, custom_text: str | None = None) -> str:
    key = _normalize_mood_key(mood_label)
    if key == 'custom' and (custom_text or '').strip():
        return (custom_text or '').strip()[:60]
    return MOOD_DISPLAY_MAP.get(key, (custom_text or '').strip()[:60] or 'Normal')


def mood_value_for_label(mood_label: str | None, custom_text: str | None = None) -> int:
    key = _normalize_mood_key(mood_label)
    if key in MOOD_VALUE_MAP:
        return MOOD_VALUE_MAP[key]

    sample = f"{mood_label or ''} {custom_text or ''}".lower()
    if any(word in sample for word in ['happy', '开心', 'relieved', 'hopeful', 'calm', 'good', 'better', 'light']):
        return MOOD_VALUE_MAP['happy']
    if any(word in sample for word in ['sad', '伤心', 'down', 'low', 'cry', '糟糕']):
        return MOOD_VALUE_MAP['sad']
    if any(word in sample for word in ['anxious', '焦虑', 'panic', 'stress', 'overwhelmed', '紧张']):
        return MOOD_VALUE_MAP['anxious']
    if any(word in sample for word in ['tired', '疲劳', 'exhausted', 'drained', 'burned out', '累']):
        return MOOD_VALUE_MAP['exhausted']
    return MOOD_VALUE_MAP['normal']


def _fallback_detect_mood(text: str, preferred: str | None = None) -> dict[str, Any]:
    preferred_key = _normalize_mood_key(preferred)
    if preferred_key in MOOD_VALUE_MAP:
        return {
            'mood_label': preferred_key,
            'mood_value': mood_value_for_label(preferred_key),
            'display_label': mood_display_label(preferred_key),
            'source': 'preferred',
        }

    sample = (text or '').strip()
    lowered = sample.lower()
    checks = [
        ('overwhelmed', ['overwhelmed', 'spiraling', 'falling apart', '崩溃']),
        ('anxious', ['anxious', 'anxiety', 'panic', '焦虑', '紧张', '担心']),
        ('exhausted', ['exhausted', 'tired', 'drained', 'burned out', '疲劳', '累', '没力气']),
        ('happy', ['happy', 'glad', 'grateful', '开心', '高兴', '轻松', 'proud', 'better']),
        ('calm', ['calm', 'steady', 'grounded', 'relieved', '平静', '冷静']),
        ('sad', ['sad', 'down', 'hurt', '难过', '伤心']),
        ('stressed', ['stress', 'stressed', 'deadline', '压力']),
        ('hopeful', ['hopeful', 'hope', '有希望']),
    ]
    for key, words in checks:
        if any(word in lowered or word in sample for word in words):
            return {
                'mood_label': key,
                'mood_value': mood_value_for_label(key),
                'display_label': mood_display_label(key),
                'source': 'fallback',
            }

    return {
        'mood_label': 'normal',
        'mood_value': mood_value_for_label('normal'),
        'display_label': mood_display_label('normal'),
        'source': 'fallback',
    }


def analyze_text_mood(text: str, preferred: str | None = None) -> dict[str, Any]:
    cleaned = (text or '').strip()
    client = _get_openai_client()
    if not client:
        return _fallback_detect_mood(cleaned, preferred=preferred)

    try:
        model_name = os.getenv('OPENAI_MODEL', 'gpt-5.4')
        response = client.responses.create(
            model=model_name,
            input=(
                'Classify the main mood in this short wellness text. '
                'Return ONLY valid JSON with mood_label, mood_value, and display_label. '
                'mood_label must be one of happy, normal, sad, anxious, exhausted, stressed, calm, overwhelmed, hopeful, mixed. '
                'mood_value must be an integer from 0 to 100. '
                f'Preferred hint: {preferred or "none"}. '
                f'Text: {cleaned}'
            ),
        )
        raw_text = (response.output_text or '').strip()
        match = re.search(r'\{.*\}', raw_text, re.DOTALL)
        payload = json.loads(match.group(0) if match else raw_text)
        mood_label = _normalize_mood_key(payload.get('mood_label'))
        if mood_label not in MOOD_VALUE_MAP and mood_label != 'mixed':
            raise ValueError('Invalid mood label')
        display_label = str(payload.get('display_label') or mood_display_label(mood_label)).strip()[:60]
        mood_value = _clamp_score(float(payload.get('mood_value') or mood_value_for_label(mood_label)))
        return {
            'mood_label': mood_label,
            'mood_value': mood_value,
            'display_label': display_label or mood_display_label(mood_label),
            'source': 'ai',
        }
    except Exception:
        return _fallback_detect_mood(cleaned, preferred=preferred)

def _format_clock(hours_float: float) -> str:
    total_minutes = int(round(hours_float * 60)) % (24 * 60)
    return f"{total_minutes // 60:02d}:{total_minutes % 60:02d}"


def _recommended_sleep_schedule(age: int | None, sleep_goal_hours: float) -> dict[str, str]:
    age_value = int(age or 18)
    wake_hour = 7.0 if age_value <= 18 else 7.5
    bedtime_hour = wake_hour - float(sleep_goal_hours or 8.0)
    while bedtime_hour < 0:
        bedtime_hour += 24.0
    return {
        'optimal_wake_time': _format_clock(wake_hour),
        'optimal_bedtime': _format_clock(bedtime_hour),
    }



def _clamp_score(value: float) -> int:
    return max(0, min(100, int(round(value))))



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



def suggest_personal_goals(age: int | None, weight_kg: float | None, height_cm: float | None, gender_identity: str | None = None) -> dict[str, Any]:
    client = _get_openai_client()
    if client:
        try:
            model_name = os.getenv('OPENAI_MODEL', 'gpt-5.4')
            response = client.responses.create(
                model=model_name,
                input=(
                    'Suggest practical daily wellness targets for one person. '
                    'Return ONLY valid JSON with daily_water_goal_ml (int), daily_sleep_goal_hours (number), '
                    'daily_step_goal (int), optimal_bedtime (HH:MM 24-hour string), optimal_wake_time (HH:MM 24-hour string), and reason (string). '
                    'Keep the numbers realistic for a student and round them neatly. '
                    'Choose a balanced sleep schedule that matches the recommended sleep hours. '
                    f'Age: {age}; Gender: {gender_identity}; Weight kg: {weight_kg}; Height cm: {height_cm}.'
                ),
            )
            raw_text = (response.output_text or '').strip()
            match = re.search(r'\{.*\}', raw_text, re.DOTALL)
            payload = json.loads(match.group(0) if match else raw_text)
            sleep_goal = max(6.0, min(10.0, float(payload.get('daily_sleep_goal_hours') or 8.0)))
            schedule = _recommended_sleep_schedule(age, sleep_goal)
            return {
                'daily_water_goal_ml': max(1200, int(payload.get('daily_water_goal_ml') or 2000)),
                'daily_sleep_goal_hours': sleep_goal,
                'daily_step_goal': max(4000, int(payload.get('daily_step_goal') or 8000)),
                'optimal_bedtime': str(payload.get('optimal_bedtime') or schedule['optimal_bedtime'])[:5],
                'optimal_wake_time': str(payload.get('optimal_wake_time') or schedule['optimal_wake_time'])[:5],
                'reason': str(payload.get('reason') or 'AI set your goals from your basic information.'),
                'source': 'ai',
            }
        except Exception:
            pass

    weight = float(weight_kg or 60)
    age_value = int(age or 18)
    gender_value = (gender_identity or 'prefer_not_say').strip().lower()
    water_goal = int(round(max(1500, min(3500, weight * 33)) / 100.0) * 100)
    if gender_value == 'male':
        water_goal = min(3600, water_goal + 200)
    elif gender_value == 'female':
        water_goal = max(1400, water_goal - 100)
    sleep_goal = 8.5 if age_value <= 18 else 8.0
    if age_value <= 18:
        step_goal = 10000
    elif weight >= 85:
        step_goal = 8000
    else:
        step_goal = 9000

    gender_reason = ''
    if gender_value == 'male':
        gender_reason = ' A small hydration adjustment was added for the selected gender.'
    elif gender_value == 'female':
        gender_reason = ' A small hydration adjustment was made for the selected gender.'
    elif gender_value == 'non_binary':
        gender_reason = ' The plan stayed balanced for the selected gender.'

    schedule = _recommended_sleep_schedule(age_value, sleep_goal)
    return {
        'daily_water_goal_ml': water_goal,
        'daily_sleep_goal_hours': sleep_goal,
        'daily_step_goal': step_goal,
        'optimal_bedtime': schedule['optimal_bedtime'],
        'optimal_wake_time': schedule['optimal_wake_time'],
        'reason': 'Goals were estimated from your age, weight, height, and basic wellness rules.' + gender_reason,
        'source': 'fallback',
    }

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

    normalized = cleaned_amount.replace('half', '0.5').replace('quarter', '0.25').replace('1/2', '0.5').replace('1/4', '0.25')
    for word, number in WORD_NUMBER_MAP.items():
        normalized = re.sub(rf'\b{word}\b', str(number), normalized)

    compound_patterns = [
        (r'(\d+(?:\.\d+)?)\s*(small bottle|small bottles|large bottle|large bottles|thermos|thermoses)\b', 'compound_unit'),
        (r'(\d+(?:\.\d+)?)?\s*(glass|glasses|cup|cups|mug|mugs|bottle|bottles|can|cans)\b', 'heuristic_unit'),
    ]
    for regex, source in compound_patterns:
        unit_match = re.search(regex, normalized)
        if unit_match:
            count = float(unit_match.group(1) or 1)
            unit = unit_match.group(2)
            return {
                'amount_ml': max(0, int(count * BASE_UNIT_ML[unit])),
                'source': source,
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




def _care_text_flags(text: str) -> dict[str, Any]:
    lowered = (text or '').lower()
    return {
        'high_distress': any(word in lowered or word in text for word in CARE_HIGH_DISTRESS_WORDS),
        'low_energy': any(word in lowered or word in text for word in CARE_LOW_ENERGY_WORDS),
        'positive': any(word in lowered or word in text for word in CARE_POSITIVE_WORDS),
        'stress': any(word in lowered or word in text for word in STRESS_WORDS),
        'grounding': any(word in lowered or word in text for word in CARE_GROUNDING_WORDS),
    }



def _care_micro_action(wellness_scores: dict[str, Any] | None, flags: dict[str, Any]) -> str:
    scores = wellness_scores or {}
    hydration = int(scores.get('hydration') or scores.get('hydration_score') or 50)
    energy = int(scores.get('energy') or scores.get('energy_score') or 50)
    focus = int(scores.get('focus') or scores.get('focus_score') or 50)
    mood = int(scores.get('mood') or scores.get('mood_score') or 50)

    if flags.get('high_distress'):
        return 'Please put both feet on the floor, unclench your jaw, and take one slow breath in and one longer breath out.'
    if hydration <= min(energy, focus, mood):
        return 'Take a few sips of water now, then notice whether your body feels even 1% more settled.'
    if energy <= min(hydration, focus, mood):
        return 'Give yourself a 60-second pause: relax your shoulders, rest your eyes, and let your body soften a little.'
    if focus <= min(hydration, energy, mood):
        return 'Pick one tiny next step that takes under 3 minutes, and only do that one thing.'
    if flags.get('positive'):
        return 'Stay with this good moment for a few seconds and name one thing that helped you feel this way.'
    return 'Take one slow breath, loosen your shoulders, and choose one gentle next step instead of trying to solve everything at once.'



def _fallback_care_chat_reply(messages: list[dict[str, str]], wellness_scores: dict[str, Any] | None = None) -> dict[str, Any]:
    transcript = '\n'.join(f"{(item.get('role') or 'user')}: {(item.get('content') or '').strip()}" for item in messages[-10:])
    last_user = next((item for item in reversed(messages) if (item.get('role') or '').lower() == 'user' and (item.get('content') or '').strip()), {})
    user_text = (last_user.get('content') or '').strip()
    flags = _care_text_flags(f"{transcript}\n{user_text}")
    action = _care_micro_action(wellness_scores, flags)

    if flags['high_distress']:
        reply = (
            "That sounds really intense, and I’m glad you said it out loud. "
            "I want to stay gentle and practical here: "
            f"{action} "
            "If you feel unsafe or close to panicking, please reach out to a trusted person or local emergency support now, because AI text alone may not be enough in that moment."
        )
        risk = 'high'
    elif flags['stress'] or flags['low_energy']:
        reply = (
            "I’m sorry this feels heavy right now. You do not need to fix everything at once. "
            f"{action} "
            "After that, tell me whether you want comfort, help sorting the problem, or a tiny plan for the next hour."
        )
        risk = 'medium'
    elif flags['positive']:
        reply = (
            "I’m really glad this feels a little lighter right now. "
            f"{action} "
            "If you want, tell me what went right so we can help you hold onto it."
        )
        risk = 'low'
    else:
        reply = (
            "I’m here with you. "
            f"{action} "
            "Tell me what feels most true right now: tired, anxious, frustrated, relieved, or something else."
        )
        risk = 'low'

    return {
        'reply': reply,
        'risk_level': risk,
        'source': 'fallback',
    }
def care_chat_reply(messages: list[dict[str, str]], wellness_scores: dict[str, Any] | None = None) -> dict[str, Any]:
    cleaned_messages = []
    for item in messages or []:
        role = (item.get('role') or '').strip().lower()
        content = (item.get('content') or '').strip()
        if role not in {'user', 'assistant'} or not content:
            continue
        cleaned_messages.append({'role': role, 'content': content[:1200]})

    if not cleaned_messages:
        return _fallback_care_chat_reply([], wellness_scores)

    client = _get_openai_client()
    if not client:
        return _fallback_care_chat_reply(cleaned_messages, wellness_scores)

    try:
        model_name = os.getenv('OPENAI_MODEL', 'gpt-5.4')
        prompt = {
            'task': 'Respond as a caring wellness support chat inside a student habit app.',
            'rules': [
                'Return ONLY valid JSON.',
                'Sound warm, human, and grounded, not robotic or overly formal.',
                'Keep the reply supportive but practical, usually 2 to 5 short sentences.',
                'Use the wellness scores as quiet context. Mention them only when naturally helpful.',
                'Validate the feeling first, then offer one small concrete next step.',
                'Prefer short, natural sentences that can be shown one by one in chat.',
                'Do not pretend to replace a close friend, therapist, doctor, or emergency help.',
                'If the user sounds intensely distressed, unsafe, or close to panic, say clearly that AI text may not be enough and encourage reaching a trusted person or local emergency support now.',
                'Avoid empty praise and avoid repeating the same sentence patterns.',
            ],
            'wellness_scores': wellness_scores or {},
            'recent_messages': cleaned_messages[-10:],
            'output_schema': {
                'reply': 'string',
                'risk_level': 'low | medium | high',
            },
        }
        response = client.responses.create(model=model_name, input=json.dumps(prompt, ensure_ascii=False))
        raw_text = (response.output_text or '').strip()
        match = re.search(r'\{.*\}', raw_text, re.DOTALL)
        payload = json.loads(match.group(0) if match else raw_text)
        reply = str(payload.get('reply') or '').strip()
        risk_level = str(payload.get('risk_level') or 'low').strip().lower()
        if not reply:
            raise ValueError('Empty reply')
        if risk_level not in {'low', 'medium', 'high'}:
            risk_level = 'low'
        return {
            'reply': reply,
            'risk_level': risk_level,
            'source': 'ai',
        }
    except Exception:
        return _fallback_care_chat_reply(cleaned_messages, wellness_scores)

def _care_topic_summary(messages: list[dict[str, str]]) -> str:
    user_lines = [re.sub(r'\s+', ' ', (item.get('content') or '').strip()) for item in messages if (item.get('role') or '').lower() == 'user' and (item.get('content') or '').strip()]
    if not user_lines:
        return 'No specific feeling, event, or problem was shared in the chat yet.'

    combined = ' '.join(user_lines)
    first_sentence = re.split(r'(?<=[.!?])\s+', combined, maxsplit=1)[0].strip()
    topic = (first_sentence or combined)[:150].rstrip(' ,.;:')
    lowered = combined.lower()

    if any(word in lowered for word in ['school', 'study', 'homework', 'exam', 'class']):
        prefix = 'The user mainly talked about school or study pressure'
    elif any(word in lowered for word in ['sleep', 'tired', 'exhausted', 'drained']):
        prefix = 'The user mainly talked about tiredness or low energy'
    elif any(word in lowered for word in ['anxious', 'anxiety', 'panic', 'overwhelmed', 'stress', 'stressed']):
        prefix = 'The user mainly talked about anxiety, stress, or feeling overwhelmed'
    elif any(word in lowered for word in ['happy', 'good', 'better', 'calm', 'grateful', 'excited']):
        prefix = 'The user mainly talked about feeling better, calmer, or more positive'
    else:
        prefix = 'The user mainly talked about what they were feeling or processing'

    if len(topic) > 110:
        topic = topic[:110].rstrip(' ,.;:') + '…'
    return f"{prefix}: {topic}."

def _fallback_care_chat_summary(messages: list[dict[str, str]], wellness_scores: dict[str, Any] | None = None) -> dict[str, Any]:
    transcript = '\n'.join((item.get('content') or '').strip() for item in messages[-12:])
    user_messages = ' '.join((item.get('content') or '').strip() for item in messages if (item.get('role') or '').lower() == 'user')
    flags = _care_text_flags(f"{transcript}\n{user_messages}")

    feelings = []
    lowered = user_messages.lower()
    if 'anxious' in lowered or '焦虑' in user_messages or flags['stress']:
        feelings.append('anxious')
    if 'tired' in lowered or 'exhausted' in lowered or '疲劳' in user_messages or flags['low_energy']:
        feelings.append('exhausted')
    if 'happy' in lowered or '开心' in user_messages or flags['positive']:
        feelings.append('happy')
    if not feelings:
        feelings.append('emotionally open')

    feeling_text = ', '.join(feelings[:2])
    mood_info = _fallback_detect_mood(user_messages, preferred=feelings[0] if feelings else None)
    topic_summary = _care_topic_summary(messages)

    if flags['high_distress']:
        summary = f"{topic_summary} The emotional tone stayed intense across the chat."
        latest_event = f"Care chat ended: user felt {feeling_text} and still distressed after the conversation"
    elif flags['positive'] and not flags['stress']:
        summary = f"{topic_summary} The chat ended on a steadier or more positive note."
        latest_event = f"Care chat ended: user felt {feeling_text} and more grounded after the conversation"
    else:
        summary = f"{topic_summary} The chat ended a little calmer than it started."
        latest_event = f"Care chat ended: user felt {feeling_text} and slightly calmer after the conversation"

    return {
        'summary': summary,
        'latest_event': latest_event,
        'detected_mood': mood_info['mood_label'],
        'detected_mood_display': mood_info['display_label'],
        'mood_value': mood_info['mood_value'],
        'source': 'fallback',
    }

def summarize_care_chat_session(messages: list[dict[str, str]], wellness_scores: dict[str, Any] | None = None) -> dict[str, Any]:
    cleaned_messages = []
    for item in messages or []:
        role = (item.get('role') or '').strip().lower()
        content = (item.get('content') or '').strip()
        if role not in {'user', 'assistant'} or not content:
            continue
        cleaned_messages.append({'role': role, 'content': content[:1200]})

    if not cleaned_messages:
        return {
            'summary': 'The care chat was opened but ended before any real conversation was saved.',
            'latest_event': 'Care chat ended without a saved conversation',
            'source': 'fallback',
        }

    client = _get_openai_client()
    if not client:
        return _fallback_care_chat_summary(cleaned_messages, wellness_scores)

    try:
        model_name = os.getenv('OPENAI_MODEL', 'gpt-5.4')
        prompt = {
            'task': 'Summarize a finished care chat for a habit app history feed and provide a short event line that helps wellness scoring.',
            'rules': [
                'Return ONLY valid JSON.',
                'The summary should be 1 or 2 short sentences and should sound specific, not generic.',
                'Focus the summary almost entirely on what the user talked about, felt, or was processing in the chat. Avoid generic phrases like supportive check-in unless the content really says that.',
                'The latest_event should be short and mention the emotional state before and after the chat when possible.',
                'Use phrases like anxious, tired, calmer, grounded, relieved, hopeful, overwhelmed only when supported by the chat.',
                'Do not invent medical claims or dramatic details.',
                'Also classify the main mood of the chat.',
            ],
            'wellness_scores': wellness_scores or {},
            'recent_messages': cleaned_messages[-12:],
            'output_schema': {
                'summary': 'string',
                'latest_event': 'string',
                'detected_mood': 'happy | normal | sad | anxious | exhausted | stressed | calm | overwhelmed | hopeful | mixed',
                'detected_mood_display': 'string',
                'mood_value': 'integer 0-100',
            },
        }
        response = client.responses.create(model=model_name, input=json.dumps(prompt, ensure_ascii=False))
        raw_text = (response.output_text or '').strip()
        match = re.search(r'\{.*\}', raw_text, re.DOTALL)
        payload = json.loads(match.group(0) if match else raw_text)
        summary = str(payload.get('summary') or '').strip()
        latest_event = str(payload.get('latest_event') or '').strip()
        detected_mood = _normalize_mood_key(payload.get('detected_mood'))
        if not summary or not latest_event:
            raise ValueError('Missing summary fields')
        if detected_mood not in MOOD_VALUE_MAP and detected_mood != 'mixed':
            detected_mood = _fallback_detect_mood(' '.join(item['content'] for item in cleaned_messages if item['role'] == 'user')).get('mood_label', 'normal')
        detected_mood_display = str(payload.get('detected_mood_display') or mood_display_label(detected_mood)).strip()[:60]
        mood_value = _clamp_score(float(payload.get('mood_value') or mood_value_for_label(detected_mood)))
        return {
            'summary': summary,
            'latest_event': latest_event,
            'detected_mood': detected_mood,
            'detected_mood_display': detected_mood_display or mood_display_label(detected_mood),
            'mood_value': mood_value,
            'source': 'ai',
        }
    except Exception:
        return _fallback_care_chat_summary(cleaned_messages, wellness_scores)
def _event_bumps(latest_event: str) -> dict[str, int]:
    text = (latest_event or '').lower()
    bumps = {
        'hydration_score': 0,
        'energy_score': 0,
        'fitness_score': 0,
        'focus_score': 0,
        'mood_score': 0,
    }

    is_meal = any(word in text for word in ['breakfast', 'lunch', 'dinner', 'meal'])
    is_hydration = any(word in text for word in ['drink', 'water', 'hydration', 'milk', 'coke', 'beverage'])
    is_task_completion = 'completed todo' in text or 'completed meal' in text

    if is_hydration:
        if any(word in text for word in ['skip', 'dismiss', 'not_yet', 'postponed']):
            bumps['hydration_score'] -= 3
        else:
            amount_match = re.search(r'(\d+)\s*ml', text)
            amount = int(amount_match.group(1)) if amount_match else 250
            bumps['hydration_score'] += max(2, min(8, int(round(amount / 120))))

    if 'sleep' in text:
        bumps['energy_score'] += 4
    if any(word in text for word in ['steps', 'exercise', 'walk', 'run', 'stretch', 'yoga']):
        bumps['fitness_score'] += 4
    if is_meal and 'skipped' not in text:
        bumps['energy_score'] += 4
        bumps['fitness_score'] += 4
    elif any(word in text for word in ['pomodoro', 'focus', 'study', 'work session']) or (is_task_completion and not is_hydration):
        bumps['focus_score'] += 5
    if any(word in text for word in ['journal', 'mood', 'stress', 'feeling', 'felt']):
        bumps['mood_score'] += 3
    if any(word in text for word in ['tired', 'anxious', 'sad', 'stress', 'stressed', 'burned out']):
        bumps['mood_score'] -= 4

    if 'care chat' in text or 'supportive chat' in text or 'support chat' in text:
        if any(word in text for word in ['calmer', 'grounded', 'relieved', 'better', 'hopeful', 'reassured', 'steadier']):
            bumps['mood_score'] += 4
            bumps['focus_score'] += 2
        if any(word in text for word in ['still distressed', 'still anxious', 'still overwhelmed', 'panicked', 'unsafe', 'hopeless', 'spiraling']):
            bumps['mood_score'] -= 5
            bumps['focus_score'] -= 2
        if any(word in text for word in ['exhausted', 'drained', 'worn out', 'burned out']):
            bumps['energy_score'] -= 2
        if any(word in text for word in ['water', 'hydration', 'drink']) and any(word in text for word in ['grounded', 'calmer', 'better']):
            bumps['hydration_score'] += 2

    return bumps



def _fallback_wellness_scores(
    profile: dict[str, Any],
    daily_log: dict[str, Any],
    focus: dict[str, Any],
    todo: dict[str, Any],
    latest_event: str,
    current_scores: dict[str, Any] | None = None,
) -> dict[str, Any]:
    current_scores = current_scores or {}
    current = {
        'hydration_score': int(current_scores.get('hydration_score') or 50),
        'energy_score': int(current_scores.get('energy_score') or 50),
        'fitness_score': int(current_scores.get('fitness_score') or 50),
        'focus_score': int(current_scores.get('focus_score') or 50),
        'mood_score': int(current_scores.get('mood_score') or 50),
        'overall_wellness_score': int(current_scores.get('overall_wellness_score') or 50),
    }

    water_goal = max(500, int(profile.get('daily_water_goal_ml') or 2000))
    sleep_goal = max(4.0, float(profile.get('daily_sleep_goal_hours') or 8.0))
    step_goal = max(1000, int(profile.get('daily_step_goal') or 8000))

    water_ml = max(0, int(daily_log.get('water_ml') or 0))
    sleep_hours = max(0.0, float(daily_log.get('sleep_hours') or 0))
    sleep_quality = str(daily_log.get('sleep_quality') or 'Average').lower()
    steps = max(0, int(daily_log.get('steps') or 0))
    exercise_minutes = max(0, int(daily_log.get('exercise_minutes') or 0))
    journal_text = ' '.join(str(daily_log.get(key) or '') for key in ['journal_text', 'activity_text', 'notes']).strip()
    mood_label = str(daily_log.get('mood_label') or '')
    mood_custom_text = str(daily_log.get('mood_custom_text') or '')
    lowered_text = journal_text.lower()

    hydration_anchor = _clamp_score(35 + min(water_ml / water_goal, 1.4) * 40)
    energy_anchor = _clamp_score(35 + min(sleep_hours / sleep_goal, 1.2) * 30 + QUALITY_SCORES.get(sleep_quality, 60) * 0.20)
    fitness_anchor = _clamp_score(
        35
        + min(steps / step_goal, 1.2) * 25
        + min(exercise_minutes / 45, 1.2) * 20
        + (10 if any(word in lowered_text or word in journal_text for word in STRETCH_WORDS) else 0)
    )

    focus_minutes = max(0, int(focus.get('focus_minutes') or 0))
    focus_count = max(0, int(focus.get('focus_count') or 0))
    completed_tasks = max(0, int(todo.get('completed_count') or 0))
    focus_completed_tasks = max(0, int(todo.get('focus_completed_count') or completed_tasks))
    focus_anchor = _clamp_score(35 + min(focus_minutes / 180, 1.0) * 25 + min(focus_count / 4, 1.0) * 15 + min(focus_completed_tasks / 5, 1.0) * 15)

    positive_hits = sum(1 for word in POSITIVE_WORDS if word in lowered_text or word in journal_text)
    negative_hits = sum(1 for word in NEGATIVE_WORDS if word in lowered_text or word in journal_text)
    stress_hits = sum(1 for word in STRESS_WORDS if word in lowered_text or word in journal_text)
    mood_anchor = _clamp_score(50 + positive_hits * 6 - negative_hits * 7 - stress_hits * 6 + (4 if journal_text else 0))
    if mood_label or mood_custom_text:
        manual_mood_value = mood_value_for_label(mood_label, mood_custom_text)
        mood_anchor = _clamp_score((mood_anchor * 0.55) + (manual_mood_value * 0.45))

    bumps = _event_bumps(latest_event)

    def blend(key: str, anchor: int, has_signal: bool):
        value = current[key]
        if has_signal:
            value = value + (anchor - value) * 0.12
        value += bumps.get(key, 0)
        return _clamp_score(value)

    hydration = blend('hydration_score', hydration_anchor, water_ml > 0 or bumps['hydration_score'] != 0)
    energy = blend('energy_score', energy_anchor, sleep_hours > 0 or bumps['energy_score'] != 0)
    fitness = blend('fitness_score', fitness_anchor, steps > 0 or exercise_minutes > 0 or bumps['fitness_score'] != 0)
    focus_score = blend('focus_score', focus_anchor, focus_minutes > 0 or focus_count > 0 or focus_completed_tasks > 0 or bumps['focus_score'] != 0)
    mood = blend('mood_score', mood_anchor, bool(journal_text) or bumps['mood_score'] != 0)
    overall = _clamp_score((hydration + energy + fitness + focus_score + mood) / 5)

    deltas = {
        'hydration_score': hydration - current['hydration_score'],
        'energy_score': energy - current['energy_score'],
        'fitness_score': fitness - current['fitness_score'],
        'focus_score': focus_score - current['focus_score'],
        'mood_score': mood - current['mood_score'],
        'overall_wellness_score': overall - current['overall_wellness_score'],
    }
    positive_total = sum(value for value in deltas.values() if value > 0)
    negative_total = sum(value for value in deltas.values() if value < 0)

    strongest_metric = max(
        [
            ('Hydration', deltas['hydration_score']),
            ('Energy', deltas['energy_score']),
            ('Fitness', deltas['fitness_score']),
            ('Focus', deltas['focus_score']),
            ('Mood', deltas['mood_score']),
        ],
        key=lambda item: abs(item[1]),
    )

    if positive_total > abs(negative_total):
        summary = f"Nice work — your {strongest_metric[0].lower()} moved in a good direction after this update. Keep this momentum going."
    elif negative_total < 0:
        if strongest_metric[0] == 'Hydration':
            tip = 'Try a glass of water soon.'
        elif strongest_metric[0] == 'Energy':
            tip = 'A steadier sleep routine would help most.'
        elif strongest_metric[0] == 'Fitness':
            tip = 'A short walk or quick stretch would help.'
        elif strongest_metric[0] == 'Focus':
            tip = 'Try one short tomato session or finish one small task.'
        else:
            tip = 'Write a short journal note or take a calm break.'
        summary = f"Some scores dipped after this update. {tip}"
    else:
        summary = 'Scores stayed mostly steady after this update. Keep building small healthy wins.'

    return {
        'hydration_score': hydration,
        'energy_score': energy,
        'fitness_score': fitness,
        'focus_score': focus_score,
        'mood_score': mood,
        'overall_wellness_score': overall,
        'summary': summary,
        'source': 'fallback',
    }



def update_wellness_scores(
    profile: dict[str, Any],
    daily_log: dict[str, Any],
    focus: dict[str, Any],
    todo: dict[str, Any],
    latest_event: str,
    current_scores: dict[str, Any] | None = None,
) -> dict[str, Any]:
    client = _get_openai_client()
    if not client:
        return _fallback_wellness_scores(profile, daily_log, focus, todo, latest_event, current_scores=current_scores)

    try:
        model_name = os.getenv('OPENAI_MODEL', 'gpt-5.4')
        prompt = {
            'task': 'Update wellness scores for a habit tracking app.',
            'rules': [
                'Return ONLY valid JSON.',
                'Use integer scores from 0 to 100.',
                'Default neutral scores are around 50 when little data exists.',
                'The latest event may update only one category or a few categories.',
                'If one category changed, most unrelated categories should stay close to their current scores.',
                'A small action like drinking 300 ml should usually improve hydration a little, not wildly.',
                'Adding a todo task should not increase scores by itself.',
                'Completing a normal study or work task may improve focus a little.',
                'Completing a hydration task should mostly affect hydration, not focus.',
                'Completing a meal task should mainly affect energy and fitness, not focus or mood.',
                'Skipping a meal should not create a positive score change.',
                'Overall wellness should be based on the five category scores.',
                'The summary must react to the score change versus current_scores.',
                'If the update is positive overall, the summary should encourage the user in a warm motivational tone.',
                'If the update is negative overall, the summary should briefly explain what to do next to recover.',
                'Keep the summary to 1 or 2 short sentences.',
            ],
            'categories': {
                'hydration_score': 'Hydration 水分值: whether water intake is on track',
                'energy_score': 'Energy 精力值: sleep duration',
                'fitness_score': 'Fitness 活动值: exercise, steps, stretching',
                'focus_score': 'Focus 专注值: pomodoro completion and study-break balance',
                'mood_score': 'Mood 心情值: mood records, journal analysis, stress',
                'overall_wellness_score': 'Overall Wellness 总健康度: combine the rest',
            },
            'profile': profile,
            'daily_log': daily_log,
            'focus': focus,
            'todo': todo,
            'current_scores': current_scores or {},
            'latest_event': latest_event,
            'output_schema': {
                'hydration_score': 'int',
                'energy_score': 'int',
                'fitness_score': 'int',
                'focus_score': 'int',
                'mood_score': 'int',
                'overall_wellness_score': 'int',
                'summary': 'string',
            },
        }
        response = client.responses.create(model=model_name, input=json.dumps(prompt, ensure_ascii=False))
        raw_text = (response.output_text or '').strip()
        match = re.search(r'\{.*\}', raw_text, re.DOTALL)
        payload = json.loads(match.group(0) if match else raw_text)

        result = {
            'hydration_score': _clamp_score(float(payload.get('hydration_score', 50))),
            'energy_score': _clamp_score(float(payload.get('energy_score', 50))),
            'fitness_score': _clamp_score(float(payload.get('fitness_score', 50))),
            'focus_score': _clamp_score(float(payload.get('focus_score', 50))),
            'mood_score': _clamp_score(float(payload.get('mood_score', 50))),
            'overall_wellness_score': _clamp_score(float(payload.get('overall_wellness_score', 0))),
            'summary': str(payload.get('summary') or 'AI wellness update completed.'),
            'source': 'ai',
        }
        if not result['overall_wellness_score']:
            result['overall_wellness_score'] = _clamp_score(
                (
                    result['hydration_score']
                    + result['energy_score']
                    + result['fitness_score']
                    + result['focus_score']
                    + result['mood_score']
                ) / 5
            )
        return result
    except Exception:
        return _fallback_wellness_scores(profile, daily_log, focus, todo, latest_event, current_scores=current_scores)
