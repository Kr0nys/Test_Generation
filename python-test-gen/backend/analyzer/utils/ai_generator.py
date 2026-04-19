import requests
import re
from typing import Dict
from django.conf import settings


class AITestGenerator:
    def __init__(self):
        self.ollama_url = settings.OLLAMA_URL
        self.model = settings.OLLAMA_MODEL
        self.timeout = 300

    def generate_tests(self, code: str, metrics: Dict, config: Dict) -> str:
        prompt = f"""Ты QA инженер. Напиши юнит-тесты на pytest для кода:

{code[:5000]}  # Ограничиваем длину

Метрики: {metrics}
Настройки: моки={config.get('use_mocks')}, асинхронность={config.get('async_support')}

Верни ТОЛЬКО код тестов в блоке ```python```
"""
        try:
            response = requests.post(
                f'{self.ollama_url}/api/generate',
                json={'model': self.model, 'prompt': prompt, 'stream': False},
                timeout=self.timeout
            )
            if response.status_code == 200:
                text = response.json().get('response', '')
                match = re.search(r'```python\s*(.*?)\s*```', text, re.DOTALL)
                return match.group(1).strip() if match else text
        except:
            pass

        return f"# Тесты (fallback)\nimport pytest\n\ndef test_placeholder():\n    assert True"