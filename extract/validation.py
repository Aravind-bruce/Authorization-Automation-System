def validate_document(text, entities):
    score = 1.0
    flags = []

    text_lower = text.lower()
    text_len = len(text.strip())

    # 🚨 1. Too perfect (very important)
    if (
        entities.get("diseases") and
        entities.get("treatments") and
        entities.get("icd_codes") and
        "medically necessary" in text_lower and
        text_len < 800
    ):
        score -= 0.3
        flags.append("Too perfect & short → likely synthetic document")

    # 🚨 2. No hospital / real-world context
    if not any(x in text_lower for x in [
        "hospital", "clinic", "department", "medical center", "dr.", "consultant"
    ]):
        score -= 0.25
        flags.append("No real-world clinical context (hospital/doctor missing)")

    # 🚨 3. Low variability (IMPORTANT)
    unique_words = len(set(text_lower.split()))
    total_words = len(text_lower.split())

    if total_words > 0 and (unique_words / total_words) < 0.4:
        score -= 0.2
        flags.append("Low linguistic variability → template-like text")

    # 🚨 4. Missing natural noise (REAL DOCS ARE MESSY)
    if "," not in text and "." not in text:
        score -= 0.1
        flags.append("Too clean → lacks natural clinical writing style")

    # 🚨 5. No abbreviations (real docs always have)
    if not any(x in text_lower for x in ["mg", "ml", "bid", "tid", "po", "iv"]):
        score -= 0.15
        flags.append("No medical abbreviations → unnatural document")

    # 🚨 6. Signature weakness
    if "electronically signed" in text_lower and "dr" not in text_lower:
        score -= 0.2
        flags.append("Weak signature authenticity")

    score = max(0, score)

    if score < 0.5:
        risk = "HIGH"
    elif score < 0.75:
        risk = "MEDIUM"
    else:
        risk = "LOW"

    return {
        "validation_score": score,
        "fraud_risk": risk,
        "validation_flags": flags
    }