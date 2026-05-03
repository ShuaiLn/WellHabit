# Research Notes

These notes explain the design choices behind WellHabit's camera and Care AI features. They are meant for judges/demo reviewers, not as clinical claims.

## 1. Just-in-time adaptive support

**Nahum-Shani, I., Smith, S. N., Spring, B. J., Collins, L. M., Witkiewitz, K., Tewari, A., & Murphy, S. A. (2018). _Just-in-time adaptive interventions (JITAIs) in mobile health: Key components and design principles for ongoing health behavior support_. Annals of Behavioral Medicine, 52(6), 446–462. https://doi.org/10.1007/s12160-016-9830-8**

Design takeaway for WellHabit: the app should offer support when a user may need it, but the support should be small, contextual, and easy to decline. The fatigue and hydration prompts are therefore lightweight interventions instead of mandatory instructions.

## 2. Facial expression needs context

**Goel, S., Jara-Ettinger, J., Ong, D. C., & Gendron, M. (2024). _Face and context integration in emotion inference is limited and variable across categories and individuals_. Nature Communications, 15, 2443. https://doi.org/10.1038/s41467-024-46670-5**

Design takeaway for WellHabit: the app should not claim that a facial signal means a user is happy, sad, or tired. Camera output is treated as a weak signal and is paired with context such as hydration, focus completion, break completion, chat tone, or self-report. Positive affect is never stored directly unless the user confirms.

## 3. Multimodal emotion recognition is uncertain

**Wu, Y., et al. (2025). _A comprehensive review of multimodal emotion recognition_. PMC. https://pmc.ncbi.nlm.nih.gov/articles/PMC12292624/**

Design takeaway for WellHabit: multimodal systems can combine facial movement, text, behavior, and other signals, but the result is still probabilistic. WellHabit uses multimodal confirmation prompts rather than single-signal emotion labeling.

## 4. Generative AI wellness boundaries

**American Psychological Association. (2025). _Use of generative AI chatbots and wellness applications for mental health_. https://www.apa.org/topics/artificial-intelligence-machine-learning/health-advisory-chatbots-wellness-apps**

Design takeaway for WellHabit: Care AI is positioned as habit support, not psychotherapy or medical treatment. The app keeps crisis wording explicit and routes high-risk situations toward real-world emergency or crisis support.
