import re
from functools import lru_cache
from typing import Tuple


class OptimizedValidators:
    """Оптимизированные валидаторы с кэшированием и предкомпилированными regex"""
    
    def __init__(self):
        # Предкомпилированные регулярные выражения
        self.age_pattern = re.compile(r'\s(\d{2}).?')
        
        # Кэш для банвордов (set для O(1) поиска)
        self._banwords_set = set()
        self._promotings_dict = {}
        
        # Статистика
        self.validation_count = 0
        self.cache_hits = 0
    
    def update_banwords(self, banwords: list):
        """Обновить список банвордов"""
        self._banwords_set = {word.lower() for word in banwords}
    
    def update_promotings(self, promotings: list):
        """Обновить список промо"""
        self._promotings_dict = {}
        for promoting in promotings:
            if "|" in promoting:
                searched, reply = promoting.split("|", 1)
                self._promotings_dict[searched.lower()] = reply
    
    @lru_cache(maxsize=500)
    def _normalize_text(self, text: str) -> str:
        """Нормализация текста с кэшированием"""
        return text.lower().strip()
    
    def check_promoting(self, worker, text: str) -> Tuple[bool, str]:
        """Быстрая проверка рекламы"""
        if not hasattr(self, '_promotings_dict'):
            self.update_promotings(worker.promotings)
        
        normalized_text = self._normalize_text(text)
        
        for searched, reply in self._promotings_dict.items():
            if searched in normalized_text:
                return True, reply
        
        return False, ""
    
    def check_age(self, worker, age: str, text: str) -> Tuple[str, bool]:
        """Оптимизированная проверка возраста"""
        self.validation_count += 1
        
        min_age = worker.config["search"]["forms"]["age"]["min_age"]
        max_age = worker.config["search"]["forms"]["age"]["max_age"]
        
        try:
            age_int = int(age)
        except (ValueError, TypeError):
            return age, False
        
        if not (min_age <= age_int <= max_age):
            return age, False
        
        # Проверка возраста в тексте (если включена)
        if worker.config["search"]["forms"]["age"]["check_age_in_text"]:
            sub_ages = self.age_pattern.findall(text)
            if sub_ages:
                try:
                    sub_age = int(sub_ages[0])
                    if not (min_age <= sub_age <= max_age):
                        return f"Возраст в тексте - {sub_age}", False
                except (ValueError, TypeError):
                    return f"Возраст в тексте - {sub_ages[0]}", False
        
        return age, True
    
    def check_about_len(self, worker, text: str) -> Tuple[str, bool]:
        """Быстрая проверка длины"""
        length = len(text)
        config = worker.config["search"]["forms"]["about_text"]
        
        if config["skip_empty_texts"] and length == 0:
            return "0", False
        
        return str(length), length >= config["minimal_text_size"]
    
    def check_about_banwords(self, worker, text: str) -> Tuple[str, bool]:
        """Оптимизированная проверка банвордов"""
        if not text:
            return "", True
        
        if not hasattr(self, '_banwords_set') or not self._banwords_set:
            self.update_banwords(worker.banwords)
        
        normalized_text = self._normalize_text(text)
        
        # Используем set для O(1) поиска
        for banword in self._banwords_set:
            if banword in normalized_text:
                return banword, False
        
        return "", True
    
    def get_stats(self) -> dict:
        """Получить статистику валидации"""
        return {
            'validations_performed': self.validation_count,
            'cache_hits': self.cache_hits,
            'banwords_count': len(getattr(self, '_banwords_set', [])),
            'promotings_count': len(getattr(self, '_promotings_dict', {}))
        }


# Глобальный экземпляр оптимизированных валидаторов
optimized_validators = OptimizedValidators()


# Функции для обратной совместимости
def check_promoting(worker, text) -> Tuple[bool, str]:
    return optimized_validators.check_promoting(worker, text)


def check_age(worker, age, text) -> Tuple[str, bool]:
    return optimized_validators.check_age(worker, age, text)


def check_about_len(worker, text) -> Tuple[str, bool]:
    return optimized_validators.check_about_len(worker, text)


def check_about_banwords(worker, text) -> Tuple[str, bool]:
    return optimized_validators.check_about_banwords(worker, text)
