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
STRETCH_WORDS = {
    'stretch', 'stretching', 'yoga', 'mobility', '拉伸', '瑜伽'
}

QUALITY_SCORES = {
    'poor': 35,
    'average': 60,
    'good': 80,
    'excellent': 95,
}


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
