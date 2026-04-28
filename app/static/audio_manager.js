(function () {
    const DEFAULT_LANG = 'en-US';
    const DEFAULT_RATE = 1.0;
    const FEMALE_HINTS = [
        'female', 'woman', 'samantha', 'victoria', 'karen', 'moira', 'tessa',
        'zira', 'jenny', 'aria', 'susan', 'serena', 'hazel', 'sandy', 'joanna',
        'salli', 'kimberly', 'amy', 'emma', 'olivia'
    ];
    const MALE_HINTS = [
        'male', 'man', 'daniel', 'alex', 'fred', 'tom', 'david', 'mark',
        'guy', 'brian', 'matthew', 'justin', 'joey', 'arthur'
    ];

    const bootstrap = window.WELLHABIT_BOOTSTRAP || {};
    let settings = normalizeSettings(bootstrap.tts || {});
    let voices = [];
    let voicesLoaded = false;
    let currentUtterance = null;
    let currentBeep = null;
    let pendingBeep = false;

    function normalizeSettings(raw) {
        const enabled = raw.enabled === true || raw.enabled === 'true' || raw.enabled === 1 || raw.enabled === '1';
        const rate = clamp(Number(raw.rate || DEFAULT_RATE), 0.5, 1.5);
        const preference = ['default', 'female', 'male'].includes(raw.voice_preference) ? raw.voice_preference : 'default';
        return {
            enabled,
            rate,
            voice_uri: String(raw.voice_uri || ''),
            voice_name: String(raw.voice_name || ''),
            voice_lang: String(raw.voice_lang || DEFAULT_LANG),
            voice_preference: preference,
        };
    }

    function clamp(value, min, max) {
        if (!Number.isFinite(value)) return DEFAULT_RATE;
        return Math.max(min, Math.min(max, value));
    }

    function voiceGenderHint(voice) {
        const name = String(voice?.name || '').toLowerCase();
        if (FEMALE_HINTS.some((hint) => name.includes(hint))) return 'female';
        if (MALE_HINTS.some((hint) => name.includes(hint))) return 'male';
        return 'default';
    }

    function sameLang(voice, lang) {
        const target = String(lang || DEFAULT_LANG).toLowerCase();
        const voiceLang = String(voice?.lang || '').toLowerCase();
        if (!voiceLang) return false;
        return voiceLang === target || voiceLang.split('-')[0] === target.split('-')[0];
    }

    function readVoices(shouldDispatch) {
        if (!('speechSynthesis' in window)) {
            voices = [];
            voicesLoaded = true;
            return voices;
        }
        voices = window.speechSynthesis.getVoices() || [];
        voicesLoaded = voices.length > 0;
        if (shouldDispatch) {
            document.dispatchEvent(new CustomEvent('wellhabit:voices-updated', { detail: { voices } }));
        }
        return voices;
    }

    function loadVoices() {
        readVoices(true);
        if ('speechSynthesis' in window) {
            window.speechSynthesis.onvoiceschanged = () => readVoices(true);
        }
    }

    function getVoices() {
        if (!voicesLoaded) readVoices(false);
        return voices.slice();
    }

    function resolveVoice(preferredSettings) {
        const opts = normalizeSettings(Object.assign({}, settings, preferredSettings || {}));
        const list = getVoices();
        if (!list.length) return null;

        const byUri = opts.voice_uri ? list.find((voice) => voice.voiceURI === opts.voice_uri) : null;
        if (byUri) return byUri;

        const lowerName = opts.voice_name.toLowerCase();
        const byNameAndLang = lowerName
            ? list.find((voice) => voice.name.toLowerCase() === lowerName && sameLang(voice, opts.voice_lang))
            : null;
        if (byNameAndLang) return byNameAndLang;

        const byName = lowerName ? list.find((voice) => voice.name.toLowerCase() === lowerName) : null;
        if (byName) return byName;

        const sameLanguage = list.filter((voice) => sameLang(voice, opts.voice_lang));
        if (opts.voice_preference !== 'default') {
            const preferred = sameLanguage.find((voice) => voiceGenderHint(voice) === opts.voice_preference)
                || list.find((voice) => sameLang(voice, DEFAULT_LANG) && voiceGenderHint(voice) === opts.voice_preference);
            if (preferred) return preferred;
        }

        return sameLanguage.find((voice) => voice.default)
            || sameLanguage[0]
            || list.find((voice) => sameLang(voice, DEFAULT_LANG))
            || list.find((voice) => voice.default)
            || list[0]
            || null;
    }

    function stopBeep() {
        if (!currentBeep) return;
        const beep = currentBeep;
        currentBeep = null;
        try { beep.oscillator.stop(); } catch (error) {}
        try { beep.oscillator.disconnect(); } catch (error) {}
        try { beep.gain.disconnect(); } catch (error) {}
        if (beep.closeContext) {
            try { beep.ctx.close(); } catch (error) {}
        }
    }

    function makeAudioContext() {
        const AudioContextClass = window.AudioContext || window.webkitAudioContext;
        if (!AudioContextClass) return null;
        try {
            return new AudioContextClass();
        } catch (error) {
            return null;
        }
    }

    function playPromptBeep() {
        if ('speechSynthesis' in window && window.speechSynthesis.speaking) {
            pendingBeep = true;
            return false;
        }
        stopBeep();
        const ctx = makeAudioContext();
        if (!ctx) return false;
        try {
            if (ctx.state === 'suspended' && ctx.resume) ctx.resume().catch(() => {});
            const oscillator = ctx.createOscillator();
            const gain = ctx.createGain();
            const now = ctx.currentTime || 0;
            oscillator.type = 'sine';
            oscillator.frequency.setValueAtTime(880, now);
            gain.gain.setValueAtTime(0.0001, now);
            gain.gain.exponentialRampToValueAtTime(0.28, now + 0.03);
            gain.gain.exponentialRampToValueAtTime(0.0001, now + 0.22);
            oscillator.connect(gain);
            gain.connect(ctx.destination);
            currentBeep = { ctx, oscillator, gain, closeContext: true };
            oscillator.start(now);
            oscillator.stop(now + 0.24);
            oscillator.onended = () => {
                if (currentBeep && currentBeep.oscillator === oscillator) currentBeep = null;
                try { oscillator.disconnect(); } catch (error) {}
                try { gain.disconnect(); } catch (error) {}
                try { ctx.close(); } catch (error) {}
            };
            return true;
        } catch (error) {
            stopBeep();
            try { ctx.close(); } catch (closeError) {}
            return false;
        }
    }

    function flushPendingBeep() {
        if (!pendingBeep) return;
        pendingBeep = false;
        window.setTimeout(playPromptBeep, 90);
    }

    function cancelSpeech() {
        if (!('speechSynthesis' in window)) return;
        try { window.speechSynthesis.cancel(); } catch (error) {}
        currentUtterance = null;
    }

    function stopAll() {
        pendingBeep = false;
        cancelSpeech();
        stopBeep();
    }

    function speak(text, options) {
        const cleanText = String(text || '').replace(/\s+/g, ' ').trim();
        const opts = normalizeSettings(Object.assign({}, settings, options || {}));
        if (!opts.enabled || !cleanText || !('speechSynthesis' in window)) return false;

        stopBeep();
        cancelSpeech();

        const utterance = new SpeechSynthesisUtterance(cleanText);
        const voice = resolveVoice(opts);
        utterance.lang = voice?.lang || opts.voice_lang || DEFAULT_LANG;
        utterance.rate = opts.rate;
        if (voice) utterance.voice = voice;
        utterance.onend = () => {
            if (currentUtterance === utterance) currentUtterance = null;
            flushPendingBeep();
        };
        utterance.onerror = () => {
            if (currentUtterance === utterance) currentUtterance = null;
            flushPendingBeep();
        };
        currentUtterance = utterance;
        try {
            window.speechSynthesis.speak(utterance);
            return true;
        } catch (error) {
            currentUtterance = null;
            return false;
        }
    }

    function updateSettings(nextSettings) {
        settings = normalizeSettings(Object.assign({}, settings, nextSettings || {}));
        document.dispatchEvent(new CustomEvent('wellhabit:tts-settings-updated', { detail: { settings } }));
        return Object.assign({}, settings);
    }

    document.addEventListener('visibilitychange', () => {
        if (document.visibilityState !== 'visible') cancelSpeech();
    });
    window.addEventListener('pagehide', stopAll);

    loadVoices();

    window.WellHabitAudio = {
        getSettings: () => Object.assign({}, settings),
        updateSettings,
        getVoices,
        resolveVoice,
        speak,
        cancelSpeech,
        stopAll,
        playPromptBeep,
        stopBeep,
        voiceGenderHint,
    };
})();
