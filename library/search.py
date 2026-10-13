"""Small in-memory BM25 index and query-centered previews; no extra model or download."""
import math
import re
from collections import Counter, defaultdict

STOP = set("a an and are as at be by do does for from how i in is it of on or that the this to was what when where which who why with you your".split())


def tokens(text):
    return [t for t in re.findall(r"[^\W_]+(?:['’-][^\W_]+)*", text.casefold()) if t not in STOP and len(t) > 1]


def snippet(text, query, size=300):
    flat = re.sub(r"\s+", " ", text).strip()
    positions = [flat.casefold().find(t) for t in tokens(query)]
    first = min((p for p in positions if p >= 0), default=0)
    start = max(0, first - size // 3)
    end = min(len(flat), start + size)
    return ("…" if start else "") + flat[start:end] + ("…" if end < len(flat) else "")


class KeywordIndex:
    def __init__(self, metadata):
        self.postings, self.lengths = defaultdict(list), []
        self.metadata = metadata
        for i, item in enumerate(metadata):
            words = tokens(item["text"]) + tokens(item["source"]) * 2
            self.lengths.append(len(words))
            for word, count in Counter(words).items():
                self.postings[word].append((i, count))
        self.average = sum(self.lengths) / max(1, len(metadata)) or 1

    def search(self, query, allowed=None, limit=100):
        scores, count = defaultdict(float), len(self.lengths)
        words = set(tokens(query))
        for word in words:
            posting = self.postings.get(word, [])
            idf = math.log(1 + (count - len(posting) + .5) / (len(posting) + .5))
            for i, frequency in posting:
                if allowed is not None and i not in allowed:
                    continue
                normalizer = frequency + 1.5 * (.25 + .75 * self.lengths[i] / self.average)
                scores[i] += idf * frequency * 2.5 / normalizer
        # Exact phrases/names get a boost only among matching candidates.
        phrase = query.strip().casefold()
        if len(words) >= 2:
            for i in scores:
                if phrase in (self.metadata[i]["text"] + " " + self.metadata[i]["source"]).casefold():
                    scores[i] *= 1.5
        return sorted(scores, key=lambda i: scores[i], reverse=True)[:limit]
