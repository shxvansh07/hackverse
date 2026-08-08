"""Deterministic question bank used when no LLM provider is reachable.

The spec requires that an AI outage preserves session state and keeps the
patient moving rather than dead-ending. This bank means intake still completes
with zero providers configured — useful for offline demos, and the reason the
test suite can run without API keys.

Questions are keyed by the missing field they address, so the same
"ask about what we don't know" logic drives both the LLM path and this one.
"""

from __future__ import annotations

from typing import Dict, List

from app.shared.languages import resolve

# field -> language code -> question
QUESTIONS: Dict[str, Dict[str, str]] = {
    "symptoms": {
        "en": "Could you tell me what is troubling you most right now?",
        "hi": "कृपया बताइए कि आपको सबसे ज़्यादा क्या तकलीफ़ हो रही है?",
        "bn": "অনুগ্রহ করে বলুন, এখন আপনার সবচেয়ে বেশি কী সমস্যা হচ্ছে?",
        "mr": "कृपया सांगा, तुम्हाला सध्या सर्वात जास्त काय त्रास होत आहे?",
        "ta": "தற்போது உங்களுக்கு எது அதிகம் தொந்தரவு தருகிறது என்று சொல்ல முடியுமா?",
        "te": "ప్రస్తుతం మిమ్మల్ని ఎక్కువగా ఇబ్బంది పెడుతున్నది ఏమిటో చెప్పగలరా?",
        "gu": "કૃપા કરીને જણાવો કે અત્યારે તમને સૌથી વધુ શું તકલીફ થઈ રહી છે?",
        "kn": "ದಯವಿಟ್ಟು ಹೇಳಿ, ಈಗ ನಿಮಗೆ ಹೆಚ್ಚು ತೊಂದರೆ ಕೊಡುತ್ತಿರುವುದು ಏನು?",
    },
    "duration": {
        "en": "How long have you been feeling this way?",
        "hi": "यह तकलीफ़ आपको कितने समय से हो रही है?",
        "bn": "এই সমস্যাটি কতদিন ধরে হচ্ছে?",
        "mr": "हा त्रास तुम्हाला किती दिवसांपासून होत आहे?",
        "ta": "இந்த பிரச்சனை எவ்வளவு நாட்களாக இருக்கிறது?",
        "te": "ఈ సమస్య మీకు ఎన్ని రోజుల నుండి ఉంది?",
        "gu": "આ તકલીફ તમને કેટલા સમયથી છે?",
        "kn": "ಈ ತೊಂದರೆ ನಿಮಗೆ ಎಷ್ಟು ದಿನಗಳಿಂದ ಇದೆ?",
    },
    "associated_symptoms": {
        "en": "Are you noticing anything else along with this?",
        "hi": "इसके साथ आपको और कुछ महसूस हो रहा है?",
        "bn": "এর সাথে আর কিছু অনুভব করছেন?",
        "mr": "याबरोबर तुम्हाला आणखी काही जाणवत आहे का?",
        "ta": "இதனுடன் வேறு ஏதேனும் உணர்கிறீர்களா?",
        "te": "దీనితో పాటు మీకు ఇంకేమైనా అనిపిస్తోందా?",
        "gu": "આની સાથે તમને બીજું કંઈ લાગે છે?",
        "kn": "ಇದರ ಜೊತೆಗೆ ನಿಮಗೆ ಬೇರೇನಾದರೂ ಅನಿಸುತ್ತಿದೆಯೇ?",
    },
    "allergies": {
        "en": "Do you have any allergies to medicines? Please say no if you have none.",
        "hi": "क्या आपको किसी दवा से एलर्जी है? अगर नहीं है तो कृपया 'नहीं' कहें।",
        "bn": "আপনার কি কোনো ওষুধে অ্যালার্জি আছে? না থাকলে 'না' বলুন।",
        "mr": "तुम्हाला कोणत्या औषधाची ऍलर्जी आहे का? नसल्यास 'नाही' म्हणा.",
        "ta": "உங்களுக்கு ஏதேனும் மருந்து ஒவ்வாமை உள்ளதா? இல்லையென்றால் 'இல்லை' எனச் சொல்லுங்கள்.",
        "te": "మీకు ఏదైనా మందుల అలర్జీ ఉందా? లేకపోతే 'లేదు' అని చెప్పండి.",
        "gu": "તમને કોઈ દવાની એલર્જી છે? ન હોય તો 'ના' કહો.",
        "kn": "ನಿಮಗೆ ಯಾವುದೇ ಔಷಧಿಯ ಅಲರ್ಜಿ ಇದೆಯೇ? ಇಲ್ಲದಿದ್ದರೆ 'ಇಲ್ಲ' ಎಂದು ಹೇಳಿ.",
    },
    "medical_history": {
        "en": "Do you have any ongoing conditions such as diabetes, blood pressure or asthma?",
        "hi": "क्या आपको शुगर, बीपी या दमा जैसी कोई पुरानी बीमारी है?",
        "bn": "আপনার কি ডায়াবেটিস, রক্তচাপ বা হাঁপানির মতো কোনো পুরোনো রোগ আছে?",
        "mr": "तुम्हाला मधुमेह, रक्तदाब किंवा दमा असा काही जुना आजार आहे का?",
        "ta": "உங்களுக்கு நீரிழிவு, ரத்த அழுத்தம் அல்லது ஆஸ்துமா போன்ற நோய் உள்ளதா?",
        "te": "మీకు షుగర్, బీపీ లేదా ఆస్తమా వంటి దీర్ఘకాలిక సమస్యలు ఉన్నాయా?",
        "gu": "તમને ડાયાબિટીસ, બ્લડ પ્રેશર કે અસ્થમા જેવી કોઈ જૂની બીમારી છે?",
        "kn": "ನಿಮಗೆ ಸಕ್ಕರೆ ಕಾಯಿಲೆ, ರಕ್ತದೊತ್ತಡ ಅಥವಾ ಅಸ್ತಮಾ ಇದೆಯೇ?",
    },
    "medications": {
        "en": "Are you taking any medicines at the moment?",
        "hi": "क्या आप इस समय कोई दवा ले रहे हैं?",
        "bn": "আপনি কি এখন কোনো ওষুধ খাচ্ছেন?",
        "mr": "तुम्ही सध्या कोणती औषधे घेत आहात का?",
        "ta": "நீங்கள் தற்போது ஏதேனும் மருந்து எடுத்துக் கொள்கிறீர்களா?",
        "te": "మీరు ప్రస్తుతం ఏవైనా మందులు వాడుతున్నారా?",
        "gu": "તમે અત્યારે કોઈ દવા લઈ રહ્યા છો?",
        "kn": "ನೀವು ಈಗ ಯಾವುದಾದರೂ ಔಷಧಿ ತೆಗೆದುಕೊಳ್ಳುತ್ತಿದ್ದೀರಾ?",
    },
    "anything_else": {
        "en": "Is there anything else you would like the doctor to know?",
        "hi": "क्या आप डॉक्टर को और कुछ बताना चाहेंगे?",
        "bn": "ডাক্তারকে আর কিছু জানাতে চান?",
        "mr": "डॉक्टरांना आणखी काही सांगू इच्छिता का?",
        "ta": "மருத்துவரிடம் வேறு ஏதேனும் சொல்ல விரும்புகிறீர்களா?",
        "te": "డాక్టర్‌కు ఇంకేమైనా చెప్పాలనుకుంటున్నారా?",
        "gu": "શું તમે ડૉક્ટરને બીજું કંઈ જણાવવા માંગો છો?",
        "kn": "ವೈದ್ಯರಿಗೆ ಬೇರೇನಾದರೂ ತಿಳಿಸಲು ಬಯಸುತ್ತೀರಾ?",
    },
}

GREETINGS: Dict[str, str] = {
    "en": "Hello. I am here to collect your symptoms for the doctor. What is troubling you today?",
    "hi": "नमस्ते। मैं डॉक्टर के लिए आपकी तकलीफ़ की जानकारी ले रही हूँ। आज आपको क्या परेशानी है?",
    "bn": "নমস্কার। আমি ডাক্তারের জন্য আপনার সমস্যার তথ্য নিচ্ছি। আজ আপনার কী সমস্যা?",
    "mr": "नमस्कार. मी डॉक्टरांसाठी तुमची माहिती घेत आहे. आज तुम्हाला काय त्रास होत आहे?",
    "ta": "வணக்கம். மருத்துவருக்காக உங்கள் அறிகுறிகளை சேகரிக்கிறேன். இன்று உங்களுக்கு என்ன பிரச்சனை?",
    "te": "నమస్కారం. డాక్టర్ కోసం మీ సమస్యల వివరాలు తీసుకుంటున్నాను. ఈరోజు మీకు ఏమి ఇబ్బంది?",
    "gu": "નમસ્તે. હું ડૉક્ટર માટે તમારી તકલીફની માહિતી લઈ રહી છું. આજે તમને શું તકલીફ છે?",
    "kn": "ನಮಸ್ಕಾರ. ವೈದ್ಯರಿಗಾಗಿ ನಿಮ್ಮ ತೊಂದರೆಯ ಮಾಹಿತಿ ಪಡೆಯುತ್ತಿದ್ದೇನೆ. ಇಂದು ನಿಮಗೆ ಏನು ತೊಂದರೆ?",
}

HANDOFF: Dict[str, str] = {
    "en": "Thank you. Your information has been collected and sent for doctor review. You will receive the prescription after the doctor reviews it.",
    "hi": "धन्यवाद। आपकी जानकारी दर्ज कर ली गई है और डॉक्टर की समीक्षा के लिए भेज दी गई है। डॉक्टर की समीक्षा के बाद आपको प्रिस्क्रिप्शन मिलेगा।",
    "bn": "ধন্যবাদ। আপনার তথ্য সংগ্রহ করে ডাক্তারের পর্যালোচনার জন্য পাঠানো হয়েছে। পর্যালোচনার পরে আপনি প্রেসক্রিপশন পাবেন।",
    "mr": "धन्यवाद. तुमची माहिती नोंदवून डॉक्टरांच्या तपासणीसाठी पाठवली आहे. तपासणीनंतर तुम्हाला प्रिस्क्रिप्शन मिळेल.",
    "ta": "நன்றி. உங்கள் தகவல் மருத்துவர் பரிசீலனைக்கு அனுப்பப்பட்டுள்ளது. பரிசீலனைக்குப் பிறகு மருந்துச் சீட்டு கிடைக்கும்.",
    "te": "ధన్యవాదాలు. మీ సమాచారం డాక్టర్ సమీక్ష కోసం పంపబడింది. సమీక్ష తర్వాత మీకు ప్రిస్క్రిప్షన్ అందుతుంది.",
    "gu": "આભાર. તમારી માહિતી ડૉક્ટરની સમીક્ષા માટે મોકલવામાં આવી છે. સમીક્ષા પછી તમને પ્રિસ્ક્રિપ્શન મળશે.",
    "kn": "ಧನ್ಯವಾದಗಳು. ನಿಮ್ಮ ಮಾಹಿತಿಯನ್ನು ವೈದ್ಯರ ಪರಿಶೀಲನೆಗೆ ಕಳುಹಿಸಲಾಗಿದೆ. ಪರಿಶೀಲನೆಯ ನಂತರ ನಿಮಗೆ ಪ್ರಿಸ್ಕ್ರಿಪ್ಷನ್ ಸಿಗುತ್ತದೆ.",
}

URGENT: Dict[str, str] = {
    "en": "The symptoms you have described need emergency medical care right now. Please contact emergency services or go to the nearest emergency department immediately. Do not wait for this review. A doctor has been alerted.",
    "hi": "आपने जो लक्षण बताए हैं उनके लिए अभी तुरंत आपातकालीन चिकित्सा ज़रूरी है। कृपया तुरंत आपातकालीन सेवा को फ़ोन करें या नज़दीकी अस्पताल के इमरजेंसी विभाग में जाएँ। इस समीक्षा का इंतज़ार न करें। डॉक्टर को सूचित कर दिया गया है।",
    "bn": "আপনি যে লক্ষণগুলি বলেছেন তার জন্য এখনই জরুরি চিকিৎসা প্রয়োজন। অনুগ্রহ করে অবিলম্বে জরুরি পরিষেবায় যোগাযোগ করুন বা নিকটতম হাসপাতালের জরুরি বিভাগে যান। অপেক্ষা করবেন না। ডাক্তারকে জানানো হয়েছে।",
    "mr": "तुम्ही सांगितलेल्या लक्षणांसाठी तातडीने वैद्यकीय मदत आवश्यक आहे. कृपया लगेच आपत्कालीन सेवेशी संपर्क साधा किंवा जवळच्या रुग्णालयात जा. वाट पाहू नका. डॉक्टरांना कळवले आहे.",
    "ta": "நீங்கள் கூறிய அறிகுறிகளுக்கு உடனடி அவசர மருத்துவ சிகிச்சை தேவை. உடனே அவசர சேவையைத் தொடர்பு கொள்ளுங்கள் அல்லது அருகிலுள்ள மருத்துவமனைக்குச் செல்லுங்கள். காத்திருக்க வேண்டாம். மருத்துவருக்குத் தெரிவிக்கப்பட்டுள்ளது.",
    "te": "మీరు చెప్పిన లక్షణాలకు వెంటనే అత్యవసర వైద్య సహాయం అవసరం. దయచేసి వెంటనే అత్యవసర సేవలను సంప్రదించండి లేదా సమీప ఆసుపత్రికి వెళ్లండి. వేచి ఉండవద్దు. డాక్టర్‌కు తెలియజేయబడింది.",
    "gu": "તમે જણાવેલા લક્ષણો માટે તાત્કાલિક કટોકટી સારવાર જરૂરી છે. કૃપા કરીને તરત જ કટોકટી સેવાનો સંપર્ક કરો અથવા નજીકની હોસ્પિટલમાં જાઓ. રાહ ન જુઓ. ડૉક્ટરને જાણ કરવામાં આવી છે.",
    "kn": "ನೀವು ಹೇಳಿದ ಲಕ್ಷಣಗಳಿಗೆ ಈಗಲೇ ತುರ್ತು ವೈದ್ಯಕೀಯ ಆರೈಕೆ ಅಗತ್ಯ. ದಯವಿಟ್ಟು ತಕ್ಷಣ ತುರ್ತು ಸೇವೆಯನ್ನು ಸಂಪರ್ಕಿಸಿ ಅಥವಾ ಹತ್ತಿರದ ಆಸ್ಪತ್ರೆಗೆ ಹೋಗಿ. ಕಾಯಬೇಡಿ. ವೈದ್ಯರಿಗೆ ತಿಳಿಸಲಾಗಿದೆ.",
}

CLARIFY: Dict[str, str] = {
    "en": "I did not quite follow that. Could you say it again in your own words?",
    "hi": "मैं यह ठीक से समझ नहीं पाई। क्या आप इसे अपने शब्दों में दोबारा बता सकते हैं?",
    "bn": "আমি ঠিক বুঝতে পারিনি। আপনি কি আবার বলবেন?",
    "mr": "मला नीट समजले नाही. तुम्ही पुन्हा सांगू शकाल का?",
    "ta": "எனக்குச் சரியாகப் புரியவில்லை. மீண்டும் சொல்ல முடியுமா?",
    "te": "నాకు సరిగ్గా అర్థం కాలేదు. మళ్ళీ చెప్పగలరా?",
    "gu": "મને બરાબર સમજાયું નહીં. તમે ફરીથી કહી શકશો?",
    "kn": "ನನಗೆ ಸರಿಯಾಗಿ ಅರ್ಥವಾಗಲಿಲ್ಲ. ಮತ್ತೊಮ್ಮೆ ಹೇಳಬಹುದೇ?",
}

SERVICE_ERROR: Dict[str, str] = {
    "en": "I am having trouble reaching the assistant service right now. Your information is saved. Please try sending your message again.",
    "hi": "अभी सहायक सेवा से संपर्क नहीं हो पा रहा है। आपकी जानकारी सुरक्षित है। कृपया दोबारा भेजने की कोशिश करें।",
    "bn": "এই মুহূর্তে পরিষেবাটিতে সমস্যা হচ্ছে। আপনার তথ্য সংরক্ষিত আছে। আবার চেষ্টা করুন।",
    "mr": "सध्या सेवेशी संपर्क होत नाही. तुमची माहिती सुरक्षित आहे. कृपया पुन्हा प्रयत्न करा.",
    "ta": "தற்போது சேவையை அணுகுவதில் சிக்கல் உள்ளது. உங்கள் தகவல் பாதுகாப்பாக உள்ளது. மீண்டும் முயற்சிக்கவும்.",
    "te": "ప్రస్తుతం సేవను చేరుకోవడంలో సమస్య ఉంది. మీ సమాచారం భద్రంగా ఉంది. మళ్ళీ ప్రయత్నించండి.",
    "gu": "અત્યારે સેવા સાથે સંપર્ક થઈ રહ્યો નથી. તમારી માહિતી સુરક્ષિત છે. ફરી પ્રયાસ કરો.",
    "kn": "ಸದ್ಯಕ್ಕೆ ಸೇವೆಯನ್ನು ತಲುಪುವಲ್ಲಿ ಸಮಸ್ಯೆ ಇದೆ. ನಿಮ್ಮ ಮಾಹಿತಿ ಸುರಕ್ಷಿತವಾಗಿದೆ. ಮತ್ತೊಮ್ಮೆ ಪ್ರಯತ್ನಿಸಿ.",
}


def _pick(table: Dict[str, str], lang: str) -> str:
    return table.get(resolve(lang).code, table["en"])


def greeting(lang: str) -> str:
    return _pick(GREETINGS, lang)


def handoff_message(lang: str) -> str:
    return _pick(HANDOFF, lang)


def urgent_message(lang: str) -> str:
    return _pick(URGENT, lang)


def clarify_message(lang: str) -> str:
    return _pick(CLARIFY, lang)


def service_error_message(lang: str) -> str:
    return _pick(SERVICE_ERROR, lang)


#: Fixed interview order. Deliberately not driven by `missing_information`
#: (REQUIRED_FIELDS only tracks symptoms/duration/allergies, so a
#: missing-driven walk would jump straight from symptoms to duration and
#: skip "any other symptoms?" entirely) — every field here gets asked
#: exactly once, in this order, so the interview feels like an actual
#: history-taking conversation instead of racing to the minimum the safety
#: engine requires. "anything_else" is always last: it is the only question
#: whose answer is allowed to end the interview (see
#: TriageService.process_message's intake_complete check).
#:
#: "symptoms" is deliberately NOT in this list. next_question() is only ever
#: called after at least one patient message, so the greeting — which
#: already asks "what is troubling you today?" — has always already covered
#: it; including it here would ask the same thing a second time in
#: different words right after the patient's first answer.
QUESTION_ORDER: List[str] = [
    "associated_symptoms", "duration",
    "allergies", "medical_history", "medications",
]


def next_question(lang: str, already_asked: List[str]) -> str:
    """Pick the next unasked question in the fixed interview order."""
    code = resolve(lang).code

    for field in QUESTION_ORDER:
        question = QUESTIONS.get(field, {}).get(code)
        if question and question not in already_asked:
            return question

    return QUESTIONS["anything_else"].get(code, QUESTIONS["anything_else"]["en"])
