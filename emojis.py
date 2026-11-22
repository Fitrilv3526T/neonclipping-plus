"""Rule-based emoji suggestions for captions and hooks. Purely local, no API, no model.

Emoji are added only where a word clearly implies them (an off-road emoji for "car crash"),
never inside URLs, numbers or names. Two modes:
  hook      - up to 2 emoji appended after the strongest words of the opening hook
  captions  - one emoji at the end of a caption group when its text contains a trigger word
Disabled by default; the UI toggle is "Add emoji to captions".
"""
import re

# (regex on the caption text, emoji to add). First match wins, one emoji per group.
CAPTION_RULES = (
    (r'\bpolice|cops?\b|swat|siren', '🚓'),
    (r'\bcar\b|gaadi|drive|driving|traffic', '🚗'),
    (r'\bcrash|accident|bump|hit\b', '💥'),
    (r'\bfire|burn|aag\b', '🔥'),
    (r'\bmoney|cash|rupees?|dollars?|lakh|crore|\$|₹', '💰'),
    (r'\bgun|shot|shooting|bullet|goli', '🔫'),
    (r'\bwin|won|victory|champion|world record', '🏆'),
    (r'\bgame over|defeat|lost\b', '🎮'),
    (r'\blaugh|funny|joke|haha|lmao|comedy', '😂'),
    (r'\blove|heart|pyaar|jaan', '❤️'),
    (r'\bscared|afraid|fear|darr|bhoot|ghost', '😱'),
    (r'\bsurprise|shock|unexpected|can.?t believe', '🤯'),
    (r'\bthink|idea|smart|genius|trick|hack', '💡'),
    (r'\bfood|eat|khana|hungry|pizza', '🍕'),
    (r'\bmusic|song|gaana|beat', '🎵'),
    (r'\brun|bhaag|chase|escape', '🏃'),
    (r'\bjump|flip|stunt|parkour', '🤸'),
    (r'\bexplosion|bomb|blast|dhamaka|grenade|rocket', '🧨'),
)

# Emoji appended to a hook line, strongest emotion first, max 2.
HOOK_RULES = (
    (r'\bpolice|cops?\b|chase|bhaag', '🚨'),
    (r'\bfire|burn|aag\b|explosion|blast|dhamaka', '🔥'),
    (r'\bmoney|cash|rupees?|lakh|crore|\$|₹|rich', '💰'),
    (r'\bwin|won|record|champion', '🏆'),
    (r'\bfunny|joke|comedy|laugh', '😂'),
    (r'\bsecret|nobody knows|hidden', '🤫'),
    (r'\bshock|insane|crazy|unbelievable|can.?t believe', '🤯'),
    (r'\bscared|fear|darr|ghost|bhoot', '😱'),
    (r'\bcar|gaadi|drive|race', '🚗'),
    (r'\bgun|shot|bullet|goli', '🎯'),
)

_URL = re.compile(r'https?://\S+|www\.\S+')


def _outside_urls(text):
    """Mask URLs so trigger words inside links never match."""
    return _URL.sub(lambda m: '\x00' * len(m.group(0)), text)


def for_caption(text):
    """One emoji for a caption group, or ''. Text with a URL in it never gets one."""
    if not text or _URL.search(text):
        return ''
    clean = _outside_urls(text)
    for pattern, emoji in CAPTION_RULES:
        if re.search(pattern, clean, re.I):
            return emoji
    return ''


def for_hook(hook, limit=2):
    """Up to `limit` emoji for an opening hook, or ''. Keeps leading/trailing punctuation intact."""
    if not hook or _URL.search(hook):
        return ''
    clean = _outside_urls(hook)
    picks = []
    for pattern, emoji in HOOK_RULES:
        if re.search(pattern, clean, re.I) and emoji not in picks:
            picks.append(emoji)
        if len(picks) >= limit:
            break
    return ''.join(picks)


def decorate_group(text):
    """Caption text with one emoji appended, when a rule fires."""
    emoji = for_caption(text)
    return f'{text} {emoji}' if emoji else text


def decorate_hook(hook):
    """Hook text with up to two emoji appended."""
    emoji = for_hook(hook)
    return f'{hook} {emoji}'.strip() if emoji else hook
