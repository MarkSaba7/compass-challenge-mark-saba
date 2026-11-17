"""Appel du modèle : POST {LLM_BASE_URL}/chat/completions, avec reprises en cas d'erreur temporaire."""
import json
import os
import random
import time
import urllib.error
import urllib.request

from .validate import extract_json

RETRYABLE = {408, 429, 500, 502, 503, 504, 529}  # codes HTTP qui valent la peine de réessayer


class LLMError(Exception):
    pass


class LLM:
    def __init__(self, base_url, api_key, model, timeout=60, max_retries=5):
        self.url = base_url.rstrip("/") + "/chat/completions"
        self.api_key = api_key
        self.model = model
        self.timeout = timeout
        self.max_retries = max_retries
        self.deadline = None  # heure après laquelle on n'appelle plus le modèle
        self.calls = 0
        self.retries = 0

    @classmethod
    def from_env(cls):
        missing = [v for v in ("LLM_BASE_URL", "LLM_API_KEY", "LLM_MODEL") if not os.environ.get(v)]
        if missing:
            raise LLMError("variable(s) d'environnement manquante(s) : " + ", ".join(missing))
        return cls(os.environ["LLM_BASE_URL"], os.environ["LLM_API_KEY"], os.environ["LLM_MODEL"])

    def chat(self, messages, max_tokens=500):
        body = json.dumps({"model": self.model, "messages": messages, "temperature": 0,
                           "max_tokens": max_tokens}).encode()
        headers = {"Content-Type": "application/json", "Authorization": f"Bearer {self.api_key}"}
        error = ""
        for attempt in range(self.max_retries):
            if self.deadline and time.time() > self.deadline:
                raise LLMError("budget de temps épuisé")
            self.calls += 1
            try:
                request = urllib.request.Request(self.url, data=body, headers=headers, method="POST")
                with urllib.request.urlopen(request, timeout=self.timeout) as response:
                    data = json.loads(response.read().decode("utf-8"))
                content = data["choices"][0]["message"]["content"]
                if content and content.strip():
                    return content
                error = "réponse vide"
            except urllib.error.HTTPError as e:
                error = f"HTTP {e.code}"
                if e.code not in RETRYABLE:  # ex. 401 : réessayer ne changera rien
                    raise LLMError(error)
            except (OSError, ValueError, KeyError, IndexError, TypeError) as e:  # réseau, timeout, JSON inattendu
                error = repr(e)
            self.retries += 1
            time.sleep(min(20, 2 ** attempt) * random.uniform(0.5, 1))  # 1 s, 2 s, 4 s... avec un peu d'aléa
        raise LLMError(f"échec après {self.max_retries} tentatives ({error})")

    def ask_json(self, system, user, max_tokens=500):
        """Demande un objet JSON. Si la réponse n'en contient pas, on le signale au modèle et on réessaie une fois."""
        messages = [{"role": "system", "content": system}, {"role": "user", "content": user}]
        text = self.chat(messages, max_tokens)
        obj = extract_json(text)
        if obj is None:
            messages.append({"role": "assistant", "content": text})
            messages.append({"role": "user", "content": "Your reply was not a valid JSON object. "
                                                        "Reply again with ONLY the JSON object."})
            obj = extract_json(self.chat(messages, max_tokens))
        if obj is None:
            raise LLMError("le modèle ne renvoie pas de JSON valide")
        return obj
